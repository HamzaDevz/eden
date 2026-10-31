// SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
// SPDX-License-Identifier: GPL-3.0-or-later

// Protocol: one command or reply per line.
//   info                              -> info <title> <build id> main=<base> <name>@<base>...
//   read <addr> <len>                 -> mem <addr> <hex|->
//   sym <name>                        -> sym <name> <addr> <size>, or sym <name> -
//   watch <id> <len> <base> [off...]  base is an address or main+<off>; each off dereferences
//                                     the u64 at the current address and adds itself
//   unwatch <id|*>, stream <0|1>
//   poke <id> <hex> <base> [off...]   writes the bytes at the watch-style address now and again on
//                                     every frame, like a cheat, until unpoke <id|*>; unpoke and
//                                     a disconnect leave the last value written
//   pad <player> <buttons> <lx> <ly> <rx> <ry>   player is the npad index (8 = handheld),
//                                     buttons the low 16 npad bits (A to D-pad down, the same in
//                                     TAS), sticks -32767..32767
//   release <player>
//   plug <player>                     connects that controller again -> ok plug
//   amiibo <path|->                   puts the amiibo file on the virtual NFC reader of player 1
//                                     while the game scans, or takes it off -> ok amiibo
//   sync <k>                          lockstep, 0 to stop: each frame waits (1 s at most) until
//                                     the client acked a frame at most k older
//   tick <frame>                      acks a frame line, no reply
//   shot <height>                     -> shot <width> <height> <hex BGRA rows, top first>, the
//                                     next presented 16:9 frame (not with the null renderer)
//   pause, resume                     -> ok pause <frame>, ok resume: the whole emulation, for as
//                                     long as the client takes (resumed when it disconnects)
//   step <frames> [height]            runs <frames> frames then pauses -> the shot of the next
//                                     frame, or ok step <frame> without a height
// Each composited frame: f <frame> <time us> [p <index> <buttons> <lx> <ly> <rx> <ry>]...
//                        [w <id> <hex|->]...

#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <istream>
#include <mutex>
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
#include "common/settings.h"
#include "core/frontend/framebuffer_layout.h"
#include "video_core/renderer_base.h"
#include "hid_core/frontend/emulated_controller.h"
#include "hid_core/hid_core.h"

namespace Tools {
namespace {

using boost::asio::ip::tcp;

constexpr std::size_t NumPads = 9;
constexpr u64 MaxRead = 0x10000;
constexpr u64 MaxWatch = 0x1000;
constexpr auto SyncTimeout = std::chrono::seconds{1};
constexpr u64 MaxShotHeight = 1080;

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

    struct Poke {
        Watch where;
        std::vector<u8> data;
    };

    static void ParseAddress(Watch& watch, const std::vector<std::string>& args, std::size_t at) {
        const auto& base = args[at];
        watch.from_main = base.starts_with("main");
        watch.base = watch.from_main ? (base.size() > 5 ? ParseU64(base.substr(5)) : 0)
                                     : ParseU64(base);
        for (std::size_t i = at + 1; i < args.size(); ++i) {
            watch.offsets.push_back(ParseS64(args[i]));
        }
    }

    static std::vector<u8> ParseHex(const std::string& text) {
        if (text.size() % 2 != 0) {
            throw std::invalid_argument("hex");
        }
        std::vector<u8> out(text.size() / 2);
        for (std::size_t i = 0; i < out.size(); ++i) {
            out[i] = static_cast<u8>(std::stoul(text.substr(i * 2, 2), nullptr, 16));
        }
        return out;
    }

    Impl(Core::System& system_, u16 port, GameBridgePadSink pad_sink_,
         GameBridgeAmiiboSink amiibo_sink_)
        : system{system_}, pad_sink{std::move(pad_sink_)}, amiibo_sink{std::move(amiibo_sink_)},
          acceptor{io_context} {
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
        SetSync(0);
        io_context.stop();
        if (thread.joinable()) {
            thread.join();
        }
        ReleasePads();
    }

    void OnFrame() {
        const u64 current = ++frame;
        {
            std::unique_lock lock{sync_mutex};
            if (sync_slack != 0) {
                sync_cv.wait_for(lock, SyncTimeout,
                                 [&] { return sync_slack == 0 || acked + sync_slack >= current; });
            }
        }
        if (u64 until = step_until.load(); until != 0 && current >= until &&
            step_until.compare_exchange_strong(until, 0)) {
            boost::asio::post(io_context, [this] { EndStep(); });
        }
        if (has_pokes && !poke_pending.exchange(true)) {
            boost::asio::post(io_context, [this] {
                poke_pending = false;
                ApplyPokes();
            });
        }
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
        SetSync(0);
        step_until = 0;
        Resume();
        watches.clear();
        pokes.clear();
        has_pokes = false;
        ReleasePads();
        LOG_INFO(Core, "Game bridge client disconnected");
    }

    void Pause() {
        if (!paused) {
            system.Pause();
            paused = true;
        }
    }

    void Resume() {
        if (paused) {
            system.Run();
            paused = false;
        }
    }

    void EndStep() {
        if (step_height == 0) {
            Pause();
            Send(fmt::format("ok step {}\n", frame.load()));
            return;
        }
        try {
            RequestShot(step_height, true);
        } catch (const std::exception& e) {
            Pause();
            Send(fmt::format("err step {}\n", e.what()));
        }
    }

    void SetSync(u64 slack) {
        {
            std::scoped_lock lock{sync_mutex};
            sync_slack = slack;
            acked = frame;
        }
        sync_cv.notify_all();
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
        } else if (command == "plug" && args.size() == 2) {
            const auto player = ParseU64(args[1]);
            if (player >= NumPads) {
                throw std::invalid_argument("player");
            }
            auto* controller = system.HIDCore().GetEmulatedControllerByIndex(player);
            controller->Connect();
            Settings::values.players.GetValue()[player].connected = controller->IsConnected();
            Send(controller->IsConnected() ? "ok plug\n" : "err plug\n");
        } else if (command == "amiibo" && args.size() == 2) {
            const bool done = amiibo_sink && amiibo_sink(args[1] == "-" ? "" : args[1]);
            Send(done ? "ok amiibo\n" : "err amiibo\n");
        } else if (command == "watch" && args.size() >= 4) {
            Watch watch{.id = args[1], .length = ParseU64(args[2])};
            if (watch.length == 0 || watch.length > MaxWatch) {
                throw std::invalid_argument("length");
            }
            ParseAddress(watch, args, 3);
            std::erase_if(watches, [&](const Watch& w) { return w.id == watch.id; });
            watches.push_back(std::move(watch));
            Send("ok watch\n");
        } else if (command == "poke" && args.size() >= 4) {
            Poke poke{.data = ParseHex(args[2])};
            poke.where.id = args[1];
            poke.where.length = poke.data.size();
            if (poke.data.empty() || poke.data.size() > MaxWatch) {
                throw std::invalid_argument("data");
            }
            ParseAddress(poke.where, args, 3);
            const bool written = WritePoke(poke);
            std::erase_if(pokes, [&](const Poke& p) { return p.where.id == poke.where.id; });
            pokes.push_back(std::move(poke));
            has_pokes = true;
            Send(written ? "ok poke\n" : "ok poke -\n");
        } else if (command == "unpoke" && args.size() == 2) {
            std::erase_if(pokes,
                          [&](const Poke& p) { return args[1] == "*" || p.where.id == args[1]; });
            has_pokes = !pokes.empty();
            Send("ok unpoke\n");
        } else if (command == "unwatch" && args.size() == 2) {
            std::erase_if(watches, [&](const Watch& w) { return args[1] == "*" || w.id == args[1]; });
            Send("ok unwatch\n");
        } else if (command == "stream" && args.size() == 2) {
            streaming = args[1] != "0";
            Send("ok stream\n");
        } else if (command == "sync" && args.size() == 2) {
            SetSync(ParseU64(args[1]));
            Send("ok sync\n");
        } else if (command == "tick" && args.size() == 2) {
            {
                std::scoped_lock lock{sync_mutex};
                acked = std::max(acked, ParseU64(args[1]));
            }
            sync_cv.notify_all();
        } else if (command == "shot" && args.size() == 2) {
            RequestShot(ParseU64(args[1]));
        } else if (command == "pause" && args.size() == 1) {
            step_until = 0;
            Pause();
            Send(fmt::format("ok pause {}\n", frame.load()));
        } else if (command == "resume" && args.size() == 1) {
            step_until = 0;
            Resume();
            Send("ok resume\n");
        } else if (command == "step" && (args.size() == 2 || args.size() == 3)) {
            const auto frames = ParseU64(args[1]);
            step_height = args.size() == 3 ? ParseU64(args[2]) : 0;
            if (frames == 0 || step_height > MaxShotHeight) {
                throw std::invalid_argument("frames or height");
            }
            step_until = frame + frames;
            Resume();
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

    void RequestShot(u64 height, bool pause_after = false) {
        if (height == 0 || height > MaxShotHeight) {
            throw std::invalid_argument("height");
        }
        auto& renderer = system.Renderer();
        if (Settings::values.renderer_backend.GetValue() == Settings::RendererBackend::Null ||
            renderer.IsScreenshotPending()) {
            throw std::runtime_error("unavailable");
        }
        const auto width = static_cast<u32>(height * 16 / 9);
        shot_data.assign(std::size_t{width} * height * 4, 0);
        renderer.RequestScreenshot(
            shot_data.data(),
            [this, width, height, pause_after](bool invert_y) {
                boost::asio::post(io_context, [this, width, height, invert_y, pause_after] {
                    if (pause_after) {
                        Pause();
                    }
                    const std::size_t row = std::size_t{width} * 4;
                    std::string reply = fmt::format("shot {} {} ", width, height);
                    reply.reserve(reply.size() + shot_data.size() * 2 + 1);
                    for (u64 y = 0; y < height; ++y) {
                        const auto src = invert_y ? height - 1 - y : y;
                        const auto begin = shot_data.begin() + src * row;
                        AppendHex(reply, std::vector<u8>(begin, begin + row));
                    }
                    Send(reply + '\n');
                });
            },
            Layout::DefaultFrameLayout(width, static_cast<u32>(height)));
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

    bool WritePoke(const Poke& poke) {
        const auto address = Resolve(poke.where);
        auto& memory = system.ApplicationMemory();
        return address && memory.IsValidVirtualAddressRange(*address, poke.data.size()) &&
               memory.WriteBlockUnsafe(*address, poke.data.data(), poke.data.size());
    }

    void ApplyPokes() {
        for (const auto& poke : pokes) {
            WritePoke(poke);
        }
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
    GameBridgeAmiiboSink amiibo_sink;

    boost::asio::io_context io_context;
    tcp::acceptor acceptor;
    std::shared_ptr<Session> session;
    std::thread thread;

    std::atomic<u64> frame{};
    std::atomic_bool streaming{};
    std::atomic_bool publish_pending{};
    std::atomic_bool has_pokes{};
    std::atomic_bool poke_pending{};

    std::mutex sync_mutex;
    std::condition_variable sync_cv;
    u64 sync_slack{};
    u64 acked{};

    std::atomic<u64> step_until{};
    u64 step_height{};
    bool paused{};

    std::vector<Watch> watches;
    std::vector<Poke> pokes;
    std::vector<u8> shot_data;
    std::vector<u8> scratch;
    u32 pads_set{};
    std::optional<u64> main_base;
    bool symbols_loaded{};
    Core::Symbols::Symbols symbols;
};

GameBridge::GameBridge(Core::System& system, u16 port, GameBridgePadSink pad_sink,
                       GameBridgeAmiiboSink amiibo_sink)
    : impl{std::make_unique<Impl>(system, port, std::move(pad_sink), std::move(amiibo_sink))} {}

GameBridge::~GameBridge() = default;

void GameBridge::OnFrame() {
    impl->OnFrame();
}

} // namespace Tools
