// SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
// SPDX-License-Identifier: GPL-3.0-or-later

// Routes xow's printf-style logging to stderr, with the time

#pragma once

#include <cstdio>
#include <ctime>
#include <string>

#include "bytes.h"

namespace Log {

template <typename... Args>
std::string format(const std::string& message, Args... args) {
    if constexpr (sizeof...(Args) == 0) {
        return message;
    } else {
        char buffer[256];
        std::snprintf(buffer, sizeof(buffer), message.c_str(), args...);
        return buffer;
    }
}

inline void write(const char* level, const std::string& message) {
    const std::time_t now = std::time(nullptr);
    char stamp[20];
    std::strftime(stamp, sizeof(stamp), "%m-%d %H:%M:%S", std::localtime(&now));
    std::fprintf(stderr, "%s %s %s\n", stamp, level, message.c_str());
}

// Packet-level detail, too much for the log
template <typename... Args>
void debug(const std::string& message, Args... args) {}

template <typename... Args>
void info(const std::string& message, Args... args) {
    write("I", format(message, args...));
}

template <typename... Args>
void error(const std::string& message, Args... args) {
    write("E", format(message, args...));
}

inline std::string formatBytes(const Bytes& bytes) {
    std::string output;
    char hex[4];
    for (uint8_t byte : bytes) {
        std::snprintf(hex, sizeof(hex), "%02x ", byte);
        output += hex;
    }
    return output;
}

} // namespace Log
