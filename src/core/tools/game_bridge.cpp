// SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
// SPDX-License-Identifier: GPL-3.0-or-later

// Protocol: one command or reply per line.
//   info                              -> info <title> <build id> main=<base> <name>@<base>...
//   read <addr> <len>                 -> mem <addr> <hex|->
//   sym <name>                        -> sym <name> <addr> <size>, or sym <name> -
//   watch <id> <len> <base> [off...]  base is an address or main+<off>; each off dereferences
//                                     the u64 at the current address and adds itself
//   unwatch <id|*>, stream <0|1>
//   pad <player> <buttons> <lx> <ly> <rx> <ry>   player is the npad index (8 = handheld),
//                                     buttons the low 16 npad bits (A to D-pad down, the same in
//                                     TAS), sticks -32767..32767
//   release <player>
// Each composited frame: f <frame> <time us> [p <index> <buttons> <lx> <ly> <rx> <ry>]...
//                        [w <id> <hex|->]...

#include <atomic>
#include <istream>
#include <optional>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

#include <boost/asio.hpp>
#include <fmt/format.h>

#include "common/logging.h"
#include "common/thread.h"
#include "core/arm/symbols.h"
#include "core/core.h"
#include "core/core_timing.h"
#include "core/loader/loader.h"
#include "core/memory.h"
#include "core/tools/game_bridge.h"
#include "hid_core/frontend/emulated_controller.h"
#include "hid_core/hid_core.h"

namespace Tools {
namespace {

using boost::asio::ip::tcp;

constexpr std::size_t NumPads = 9;
constexpr u64 MaxRead = 0x10000;
constexpr u64 MaxWatch = 0x1000;

void AppendHex(std::string& out, const std::vector<u8>& data) {
    static constexpr char digits[] = "0123456789abcdef";
    for (const u8 byte : data) {
        out += digits[byte >> 4];
        out += digits[byte & 0xf];
    }
}

u64 ParseU64(const std::string& text) {
    return std::stoull(text, nullptr, 0);
}

s64 ParseS64(const std::string& text) {
    return std::stoll(text, nullptr, 0);
}

} // namespace

struct GameBridge::Impl {
    struct Session {
        explicit Session(tcp::socket socket_) : socket{std::move(socket_)} {}
        tcp::socket socket;
        boost::asio::streambuf input;
    };

    struct Watch {
        std::string id;
        u64 length;
        bool from_main;
        u64 base;
        std::vector<s64> offsets;
    };

    Impl(Core::System& system_, u16 port, GameBridgePadSink pad_sink_)
        : system{system_}, pad_sink{std::move(pad_sink_)}, acceptor{io_context} {
        try {
            const tcp::endpoint endpoint{boost::asio::ip::address_v4::any(), port};
            acceptor.open(endpoint.protocol());
            acceptor.set_option(tcp::acceptor::reuse_address(true));
            acceptor.bind(endpoint);
            acceptor.listen();
        } catch (const std::exception& e) {
            LOG_ERROR(Core, "Game bridge cannot listen on port {}: {}", port, e.what());
            return;
        }
        LOG_INFO(Core, "Game bridge listening on port {}", port);
        Accept();
        thread = std::thread([this] {
            Common::SetCurrentThreadName("GameBridge");
            io_context.run();
        });
    }

    ~Impl() {
        io_context.stop();
        if (thread.joinable()) {
            thread.join();
        }
        ReleasePads();
    }

    void OnFrame() {
        ++frame;
        if (!streaming || publish_pending.exchange(true)) {
            return;
        }
        boost::asio::post(io_context, [this] {
            publish_pending = false;
            Publish();
        });
    }

private:
    void Accept() {
        acceptor.async_accept([this](const boost::system::error_code& error, tcp::socket peer) {
            if (error) {
                return;
            }
            Disconnect();
            boost::system::error_code ignored;
            peer.set_option(tcp::no_delay(true), ignored);
            session = std::make_shared<Session>(std::move(peer));
            streaming = true;
            LOG_INFO(Core, "Game bridge client connected");
            Read(session);
            Accept();
        });
    }

    void Read(std::shared_ptr<Session> s) {
        boost::asio::async_read_until(
            s->socket, s->input, '\n',
            [this, s](const boost::system::error_code& error, std::size_t) {
                if (s != session) {
                    return;
                }
                if (error) {
                    Disconnect();
                    return;
                }
                std::string line;
                std::istream stream{&s->input};
                std::getline(stream, line);
                if (!line.empty() && line.back() == '\r') {
                    line.pop_back();
                }
                HandleLine(line);
                if (s == session) {
                    Read(s);
                }
            });
    }

    void Disconnect() {
        if (!session) {
            return;
        }
        boost::system::error_code ignored;
        session->socket.close(ignored);
        session.reset();
        streaming = false;
        watches.clear();
        ReleasePads();
        LOG_INFO(Core, "Game bridge client disconnected");
    }

    void Send(const std::string& line) {
        if (!session) {
            return;
        }
        boost::system::error_code error;
        boost::asio::write(session->socket, boost::asio::buffer(line), error);
        if (error) {
            Disconnect();
        }
    }

    void HandleLine(const std::string& line) {
        std::istringstream stream{line};
        std::vector<std::string> args;
        for (std::string arg; stream >> arg;) {
            args.push_back(std::move(arg));
        }
        if (args.empty()) {
            return;
        }
        try {
            HandleCommand(args);
        } catch (const std::exception& e) {
            Send(fmt::format("err {} {}\n", args[0], e.what()));
        }
    }

    void HandleCommand(const std::vector<std::string>& args) {
        const auto& command = args[0];
        if (command == "pad" && args.size() == 7) {
            const auto player = ParseU64(args[1]);
            const auto axis = [&](std::size_t i) {
                return static_cast<float>(ParseS64(args[i])) / 32767.0f;
            };
            SetPad(player, ParseU64(args[2]), axis(3), axis(4), axis(5), axis(6));
        } else if (command == "release" && args.size() == 2) {
            const auto player = ParseU64(args[1]);
            SetPad(player, 0, 0, 0, 0, 0);
            if (player < NumPads) {
                pads_set &= ~(1U << player);
            }
        } else if (command == "watch" && args.size() >= 4) {
            Watch watch{.id = args[1], .length = ParseU64(args[2])};
            if (watch.length == 0 || watch.length > MaxWatch) {
                throw std::invalid_argument("length");
            }
            const auto& base = args[3];
            watch.from_main = base.starts_with("main");
            watch.base = watch.from_main ? (base.size() > 5 ? ParseU64(base.substr(5)) : 0)
                                         : ParseU64(base);
            for (std::size_t i = 4; i < args.size(); ++i) {
                watch.offsets.push_back(ParseS64(args[i]));
            }
            std::erase_if(watches, [&](const Watch& w) { return w.id == watch.id; });
            watches.push_back(std::move(watch));
            Send("ok watch\n");
        } else if (command == "unwatch" && args.size() == 2) {
            std::erase_if(watches, [&](const Watch& w) { return args[1] == "*" || w.id == args[1]; });
            Send("ok unwatch\n");
        } else if (command == "stream" && args.size() == 2) {
            streaming = args[1] != "0";
            Send("ok stream\n");
        } else if (command == "read" && args.size() == 3) {
            const auto address = ParseU64(args[1]);
            const auto length = ParseU64(args[2]);
            if (length == 0 || length > MaxRead) {
                throw std::invalid_argument("length");
            }
            std::string reply = fmt::format("mem {:x} ", address);
            AppendRead(reply, address, length);
            Send(reply + '\n');
        } else if (command == "sym" && args.size() == 2) {
            LoadSymbols();
            const auto it = symbols.find(args[1]);
            Send(it == symbols.end()
                     ? fmt::format("sym {} -\n", args[1])
                     : fmt::format("sym {} {:x} {:x}\n", args[1], it->second.first,
                                   it->second.second));
        } else if (command == "info") {
            std::string reply = fmt::format("info {:016x} ", system.GetApplicationProcessProgramID());
            for (const u8 byte : system.GetApplicationProcessBuildID()) {
                fmt::format_to(std::back_inserter(reply), "{:02x}", byte);
            }
            const auto main = MainBase();
            reply += main ? fmt::format(" main={:x}", *main) : " main=-";
            for (const auto& [base, name] : Modules()) {
                fmt::format_to(std::back_inserter(reply), " {}@{:x}", name, base);
            }
            Send(reply + '\n');
        } else {
            throw std::invalid_argument("unknown command or wrong argument count");
        }
    }

    void SetPad(u64 player, u64 buttons, float lx, float ly, float rx, float ry) {
        if (player >= NumPads) {
            throw std::invalid_argument("player");
        }
        // Above bit 15 npad and TAS bits differ, and TAS bit 18 is Home.
        if (pad_sink) {
            pad_sink(player, buttons & 0xffff, lx, ly, rx, ry);
        }
        pads_set |= 1U << player;
    }

    void ReleasePads() {
        for (std::size_t player = 0; player < NumPads; ++player) {
            if ((pads_set & (1U << player)) != 0 && pad_sink) {
                pad_sink(player, 0, 0, 0, 0, 0);
            }
        }
        pads_set = 0;
    }

    void Publish() {
        if (!session || !streaming) {
            return;
        }
        std::string line = fmt::format("f {} {}", frame.load(),
                                       system.CoreTiming().GetGlobalTimeUs().count());
        auto& hid = system.HIDCore();
        for (std::size_t i = 0; i < NumPads; ++i) {
            const auto* controller = hid.GetEmulatedControllerByIndex(i);
            if (controller == nullptr || !controller->IsConnected()) {
                continue;
            }
            const auto sticks = controller->GetSticks();
            fmt::format_to(std::back_inserter(line), " p {} {:x} {} {} {} {}", i,
                           static_cast<u64>(controller->GetNpadButtons().raw), sticks.left.x,
                           sticks.left.y, sticks.right.x, sticks.right.y);
        }
        for (const auto& watch : watches) {
            line += " w ";
            line += watch.id;
            line += ' ';
            AppendRead(line, Resolve(watch), watch.length);
        }
        line += '\n';
        Send(line);
    }

    bool ReadGuest(u64 address, void* data, u64 length) {
        auto& memory = system.ApplicationMemory();
        // The unsafe read skips GPU flushes, which must not be triggered from this thread.
        return memory.IsValidVirtualAddressRange(address, length) &&
               memory.ReadBlockUnsafe(address, data, length);
    }

    void AppendRead(std::string& out, std::optional<u64> address, u64 length) {
        scratch.resize(length);
        if (!address || !ReadGuest(*address, scratch.data(), length)) {
            out += '-';
            return;
        }
        AppendHex(out, scratch);
    }

    std::optional<u64> Resolve(const Watch& watch) {
        u64 address = watch.base;
        if (watch.from_main) {
            const auto main = MainBase();
            if (!main) {
                return std::nullopt;
            }
            address += *main;
        }
        for (const s64 offset : watch.offsets) {
            u64 pointer{};
            if (!ReadGuest(address, &pointer, sizeof(pointer))) {
                return std::nullopt;
            }
            address = pointer + offset;
        }
        return address;
    }

    Loader::AppLoader::Modules Modules() {
        Loader::AppLoader::Modules modules;
        if (system.GetAppLoader().ReadNSOModules(modules) != Loader::ResultStatus::Success) {
            modules.clear();
        }
        return modules;
    }

    std::optional<u64> MainBase() {
        if (!main_base) {
            for (const auto& [base, name] : Modules()) {
                if (name == "main") {
                    main_base = base;
                }
            }
        }
        return main_base;
    }

    void LoadSymbols() {
        if (symbols_loaded) {
            return;
        }
        symbols_loaded = true;
        // Symbol values are relative to their module; zero means imported from another one.
        for (const auto& [base, name] : Modules()) {
            for (auto& [symbol, value] : Core::Symbols::GetSymbols(base, system.ApplicationMemory())) {
                if (value.first != 0) {
                    symbols.try_emplace(symbol, base + value.first, value.second);
                }
            }
        }
    }

    Core::System& system;
    GameBridgePadSink pad_sink;

    boost::asio::io_context io_context;
    tcp::acceptor acceptor;
    std::shared_ptr<Session> session;
    std::thread thread;

    std::atomic<u64> frame{};
    std::atomic_bool streaming{};
    std::atomic_bool publish_pending{};

    std::vector<Watch> watches;
    std::vector<u8> scratch;
    u32 pads_set{};
    std::optional<u64> main_base;
    bool symbols_loaded{};
    Core::Symbols::Symbols symbols;
};

GameBridge::GameBridge(Core::System& system, u16 port, GameBridgePadSink pad_sink)
    : impl{std::make_unique<Impl>(system, port, std::move(pad_sink))} {}

GameBridge::~GameBridge() = default;

void GameBridge::OnFrame() {
    impl->OnFrame();
}

} // namespace Tools
