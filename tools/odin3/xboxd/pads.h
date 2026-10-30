// SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
// SPDX-License-Identifier: GPL-3.0-or-later

// Watches the Bluetooth controllers: logs each battery step, which gives their real battery life,
// and turns off a controller left untouched, which a DualShock 4 never does by itself on Android

#pragma once

#include <chrono>
#include <cstdint>
#include <map>
#include <set>
#include <string>

class BluetoothPads {
public:
    explicit BluetoothPads(std::chrono::minutes idle_limit);
    ~BluetoothPads();

    /// Called every second: picks up new controllers, reads their input and battery
    void Update();

private:
    using Clock = std::chrono::steady_clock;

    struct Axis {
        int32_t reference; ///< Value when the last move was counted, so noise never adds up
        int32_t threshold;
    };

    struct Node {
        int fd;
        std::string address;
        std::map<uint16_t, Axis> axes;
    };

    struct Pad {
        std::string name;
        Clock::time_point connected;
        Clock::time_point last_used;
        std::string battery;
    };

    void Scan();
    void Open(const std::string& sysfs, const std::string& device);
    /// Returns false once the node is gone
    bool Drain(Node& node, Pad& pad);
    void Check(const std::string& address, Pad& pad);

    const std::chrono::minutes idle_limit;
    std::map<std::string, Node> nodes; ///< By sysfs path, which a new device never reuses
    std::set<std::string> ignored;
    std::map<std::string, Pad> pads; ///< By Bluetooth address
};
