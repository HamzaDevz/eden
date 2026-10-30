// SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
// SPDX-License-Identifier: GPL-3.0-or-later

#include "pads.h"

#include <algorithm>
#include <array>
#include <cerrno>
#include <climits>
#include <cstdio>
#include <cstdlib>
#include <thread>
#include <utility>
#include <vector>

#include <fcntl.h>
#include <glob.h>
#include <grp.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <sys/wait.h>
#include <unistd.h>

#include <linux/input.h>

#include "log.h"

namespace {
constexpr const char* INPUT_NODES = "/sys/class/input/event*";
constexpr const char* PAD_OFF_DEX = "/data/local/tmp/odin3-xbox/padoff.dex";
constexpr unsigned long BLUETOOTH_BUS = 0x0005;
constexpr uid_t SHELL_ID = 2000;

// A move counts once it covers this share of the axis range: resting sticks wander a few steps
constexpr int32_t AXIS_STEPS = 16;

constexpr unsigned WORD_BITS = sizeof(unsigned long) * CHAR_BIT;

template <size_t N>
bool HasBit(const std::array<unsigned long, N>& bits, unsigned bit) {
    return (bits[bit / WORD_BITS] >> (bit % WORD_BITS)) & 1;
}

std::string ReadLine(const std::string& path) {
    const int fd = open(path.c_str(), O_RDONLY | O_CLOEXEC);
    if (fd < 0) {
        return {};
    }
    char buffer[64];
    const ssize_t size = read(fd, buffer, sizeof(buffer) - 1);
    close(fd);
    if (size <= 0) {
        return {};
    }
    std::string line(buffer, static_cast<size_t>(size));
    return line.substr(0, line.find('\n'));
}

std::vector<std::string> Glob(const std::string& pattern) {
    std::vector<std::string> paths;
    glob_t matches{};
    if (glob(pattern.c_str(), 0, nullptr, &matches) == 0) {
        paths.assign(matches.gl_pathv, matches.gl_pathv + matches.gl_pathc);
    }
    globfree(&matches);
    return paths;
}

int Minutes(std::chrono::steady_clock::duration duration) {
    return static_cast<int>(std::chrono::duration_cast<std::chrono::minutes>(duration).count());
}

/// hid-playstation and hid-nintendo name the battery after the controller's address
std::string Battery(const std::string& address) {
    for (const std::string& supply : Glob("/sys/class/power_supply/*" + address + "*")) {
        const std::string capacity = ReadLine(supply + "/capacity");
        if (!capacity.empty()) {
            return capacity + "% " + ReadLine(supply + "/status");
        }
    }
    return "unknown";
}

/// AYN's com.odin.mapping deletes a Bluetooth gamepad's node to hide it behind its own copy, so a
/// missing node is made again from its device number
int OpenNode(const std::string& sysfs, const std::string& device) {
    const int fd = open(device.c_str(), O_RDONLY | O_NONBLOCK | O_CLOEXEC);
    if (fd >= 0 || errno != ENOENT) {
        return fd;
    }
    unsigned major_number = 0;
    unsigned minor_number = 0;
    if (std::sscanf(ReadLine(sysfs + "/dev").c_str(), "%u:%u", &major_number, &minor_number) != 2) {
        return -1;
    }
    const std::string own = "/dev/odin3-" + device.substr(device.rfind('/') + 1);
    unlink(own.c_str());
    if (mknod(own.c_str(), S_IFCHR | 0600, makedev(major_number, minor_number)) != 0) {
        return -1;
    }
    const int own_fd = open(own.c_str(), O_RDONLY | O_NONBLOCK | O_CLOEXEC);
    unlink(own.c_str());
    return own_fd;
}

/// Drops the controller's link through PadOff.java, which turns a DualShock 4 off
void TurnOff(const std::string& address) {
    const std::string class_path = std::string("-Djava.class.path=") + PAD_OFF_DEX;
    const pid_t pid = fork();
    if (pid == 0) {
        // disconnect() wants BLUETOOTH_PRIVILEGED, which the shell user holds
        const int null_fd = open("/dev/null", O_WRONLY);
        dup2(null_fd, STDOUT_FILENO);
        if (getuid() == 0 && (setgroups(0, nullptr) != 0 ||
                              setresgid(SHELL_ID, SHELL_ID, SHELL_ID) != 0 ||
                              setresuid(SHELL_ID, SHELL_ID, SHELL_ID) != 0)) {
            _exit(126);
        }
        execl("/system/bin/app_process", "app_process", class_path.c_str(), "/system/bin",
              "PadOff", address.c_str(), nullptr);
        _exit(127);
    }
    int status = -1;
    if (pid > 0 && waitpid(pid, &status, 0) == pid && WIFEXITED(status) &&
        WEXITSTATUS(status) == 0) {
        Log::info("Pad %s turned off", address.c_str());
    } else {
        Log::error("Pad %s could not be turned off (status %d)", address.c_str(), status);
    }
}
} // namespace

BluetoothPads::BluetoothPads(std::chrono::minutes idle_limit) : idle_limit(idle_limit) {}

BluetoothPads::~BluetoothPads() {
    for (const auto& [sysfs, node] : nodes) {
        close(node.fd);
    }
}

void BluetoothPads::Update() {
    Scan();
    for (auto it = nodes.begin(); it != nodes.end();) {
        if (Drain(it->second, pads[it->second.address])) {
            ++it;
            continue;
        }
        close(it->second.fd);
        it = nodes.erase(it);
    }
    for (auto it = pads.begin(); it != pads.end();) {
        const bool present = std::any_of(nodes.begin(), nodes.end(), [&](const auto& node) {
            return node.second.address == it->first;
        });
        if (present) {
            Check(it->first, it->second);
            ++it;
            continue;
        }
        Log::info("Pad %s left after %d min, battery %s", it->first.c_str(),
                  Minutes(Clock::now() - it->second.connected), it->second.battery.c_str());
        it = pads.erase(it);
    }
}

void BluetoothPads::Scan() {
    std::set<std::string> present;
    for (const std::string& link : Glob(INPUT_NODES)) {
        char resolved[PATH_MAX];
        if (realpath(link.c_str(), resolved) == nullptr) {
            continue;
        }
        const std::string sysfs = resolved;
        present.insert(sysfs);
        if (!nodes.contains(sysfs) && !ignored.contains(sysfs)) {
            Open(sysfs, "/dev/input/" + link.substr(link.rfind('/') + 1));
        }
    }
    std::erase_if(ignored, [&](const std::string& sysfs) { return !present.contains(sysfs); });
}

void BluetoothPads::Open(const std::string& sysfs, const std::string& device) {
    const std::string address = ReadLine(sysfs + "/device/uniq");
    const std::string bus = ReadLine(sysfs + "/device/id/bustype");
    if (address.empty() || std::strtoul(bus.c_str(), nullptr, 16) != BLUETOOTH_BUS) {
        ignored.insert(sysfs);
        return;
    }
    const int fd = OpenNode(sysfs, device);
    if (fd < 0) {
        // ueventd may not have set the node up yet, tried again next second
        return;
    }
    // Only the gamepad itself: a DualShock 4's touchpad and motion sensors are nodes of their own
    std::array<unsigned long, KEY_MAX / WORD_BITS + 1> keys{};
    if (ioctl(fd, EVIOCGBIT(EV_KEY, sizeof(keys)), keys.data()) < 0 ||
        (!HasBit(keys, BTN_SOUTH) && !HasBit(keys, BTN_TRIGGER))) {
        close(fd);
        ignored.insert(sysfs);
        return;
    }

    Node node{fd, address, {}};
    std::array<unsigned long, ABS_MAX / WORD_BITS + 1> axes{};
    ioctl(fd, EVIOCGBIT(EV_ABS, sizeof(axes)), axes.data());
    for (unsigned code = 0; code <= ABS_MAX; ++code) {
        input_absinfo info{};
        if (HasBit(axes, code) && ioctl(fd, EVIOCGABS(code), &info) == 0) {
            node.axes[static_cast<uint16_t>(code)] = {
                info.value, std::max(1, (info.maximum - info.minimum) / AXIS_STEPS)};
        }
    }
    nodes.emplace(sysfs, std::move(node));

    if (!pads.contains(address)) {
        const auto now = Clock::now();
        Pad& pad = pads[address];
        pad = {ReadLine(sysfs + "/device/name"), now, now, Battery(address)};
        Log::info("Pad %s connected (%s), battery %s", address.c_str(), pad.name.c_str(),
                  pad.battery.c_str());
    }
}

bool BluetoothPads::Drain(Node& node, Pad& pad) {
    std::array<input_event, 64> events;
    while (true) {
        const ssize_t size = read(node.fd, events.data(), sizeof(events));
        if (size <= 0) {
            return size < 0 && (errno == EAGAIN || errno == EINTR);
        }
        for (size_t i = 0; i < static_cast<size_t>(size) / sizeof(input_event); ++i) {
            const input_event& event = events[i];
            if (event.type == EV_KEY && event.value == 1) {
                pad.last_used = Clock::now();
            } else if (event.type == EV_ABS) {
                const auto axis = node.axes.find(event.code);
                if (axis != node.axes.end() &&
                    std::abs(event.value - axis->second.reference) >= axis->second.threshold) {
                    axis->second.reference = event.value;
                    pad.last_used = Clock::now();
                }
            }
        }
    }
}

void BluetoothPads::Check(const std::string& address, Pad& pad) {
    const auto now = Clock::now();
    const std::string battery = Battery(address);
    if (battery != pad.battery) {
        Log::info("Pad %s battery %s after %d min", address.c_str(), battery.c_str(),
                  Minutes(now - pad.connected));
        pad.battery = battery;
    }
    if (now - pad.last_used >= idle_limit) {
        Log::info("Pad %s untouched for %d min, turning it off", address.c_str(),
                  static_cast<int>(idle_limit.count()));
        pad.last_used = now;
        std::thread(TurnOff, address).detach();
    }
}
