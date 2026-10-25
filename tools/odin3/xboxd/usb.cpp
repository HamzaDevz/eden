// SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
// SPDX-License-Identifier: GPL-3.0-or-later

#include <cerrno>
#include <cstring>

#include <linux/usbdevice_fs.h>
#include <sys/ioctl.h>

#include "log.h"
#include "usb.h"

namespace {
// Timeouts in milliseconds
constexpr unsigned int USB_TIMEOUT_READ = 1000;
constexpr unsigned int USB_TIMEOUT_WRITE = 1000;

// Vendor request to the device itself
constexpr uint8_t REQUEST_TYPE_VENDOR_OUT = 0x40;
constexpr uint8_t REQUEST_TYPE_VENDOR_IN = 0xc0;
constexpr uint8_t ENDPOINT_IN = 0x80;
} // namespace

UsbDevice::UsbDevice(int fd_, Terminate terminate_) : fd(fd_), terminate(std::move(terminate_)) {}

void UsbDevice::fail(const char* what) {
    // Only the first failure is reported, the others follow from the device going away
    if (!failed.exchange(true)) {
        Log::error("Error in %s: %s", what, std::strerror(errno));
        terminate();
    }
}

void UsbDevice::controlTransfer(ControlPacket packet, bool write) {
    usbdevfs_ctrltransfer transfer{};
    transfer.bRequestType = write ? REQUEST_TYPE_VENDOR_OUT : REQUEST_TYPE_VENDOR_IN;
    transfer.bRequest = packet.request;
    transfer.wValue = packet.value;
    transfer.wIndex = packet.index;
    transfer.wLength = packet.length;
    transfer.timeout = USB_TIMEOUT_WRITE;
    transfer.data = packet.data;

    if (ioctl(fd, USBDEVFS_CONTROL, &transfer) != packet.length) {
        fail("control transfer");
    }
}

int UsbDevice::bulkRead(uint8_t endpoint, FixedBytes<USB_MAX_BULK_TRANSFER_SIZE>& buffer) {
    usbdevfs_bulktransfer transfer{};
    transfer.ep = endpoint | ENDPOINT_IN;
    transfer.len = static_cast<unsigned int>(buffer.size());
    transfer.timeout = USB_TIMEOUT_READ;
    transfer.data = buffer.raw();

    const int transferred = ioctl(fd, USBDEVFS_BULK, &transfer);
    if (transferred < 0) {
        if (errno == ETIMEDOUT) {
            return 0;
        }
        fail("bulk read");
        return -1;
    }
    return transferred;
}

bool UsbDevice::bulkWrite(uint8_t endpoint, Bytes& data) {
    usbdevfs_bulktransfer transfer{};
    transfer.ep = endpoint;
    transfer.len = static_cast<unsigned int>(data.size());
    transfer.timeout = USB_TIMEOUT_WRITE;
    transfer.data = data.raw();

    if (ioctl(fd, USBDEVFS_BULK, &transfer) < 0) {
        fail("bulk write");
        return false;
    }
    return true;
}
