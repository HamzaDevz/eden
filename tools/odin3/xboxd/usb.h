// SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
// SPDX-License-Identifier: GPL-3.0-or-later

// xow's USB device interface over a usbfs file descriptor whose interface the caller has
// already claimed

#pragma once

#include <atomic>
#include <cstdint>
#include <functional>

#include "bytes.h"

#define USB_MAX_BULK_TRANSFER_SIZE 512

class UsbDevice {
public:
    using Terminate = std::function<void()>;

    struct ControlPacket {
        uint8_t request;
        uint16_t value;
        uint16_t index;
        uint8_t* data;
        uint16_t length;
    };

    UsbDevice(int fd, Terminate terminate);

    void controlTransfer(ControlPacket packet, bool write);
    int bulkRead(uint8_t endpoint, FixedBytes<USB_MAX_BULK_TRANSFER_SIZE>& buffer);
    bool bulkWrite(uint8_t endpoint, Bytes& data);

private:
    void fail(const char* what);

    int fd;
    Terminate terminate;
    std::atomic<bool> failed{false};
};
