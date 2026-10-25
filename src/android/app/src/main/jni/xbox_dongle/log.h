// SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
// SPDX-License-Identifier: GPL-3.0-or-later

// Routes xow's printf-style logging to Eden's log

#pragma once

#include <cstdio>
#include <string>

#include "bytes.h"
#include "common/logging.h"

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

template <typename... Args>
void debug(const std::string& message, Args... args) {
    LOG_DEBUG(Input, "[XboxDongle] {}", format(message, args...));
}

template <typename... Args>
void info(const std::string& message, Args... args) {
    LOG_INFO(Input, "[XboxDongle] {}", format(message, args...));
}

template <typename... Args>
void error(const std::string& message, Args... args) {
    LOG_ERROR(Input, "[XboxDongle] {}", format(message, args...));
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
