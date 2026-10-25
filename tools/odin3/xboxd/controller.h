// SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
// SPDX-License-Identifier: GPL-3.0-or-later

// Stands in for xow's controller: the gamepad state goes to the caller's events, which feed uinput

#pragma once

#include <array>
#include <atomic>
#include <condition_variable>
#include <cstdint>
#include <functional>
#include <mutex>
#include <thread>

#include "gip.h"

// The GIP input buttons as reported, with the guide button above them
constexpr uint32_t CONTROLLER_GUIDE_BUTTON = 1 << 16;

struct ControllerState {
    uint32_t buttons;
    uint16_t triggerLeft;
    uint16_t triggerRight;
    int16_t stickLeftX;
    int16_t stickLeftY;
    int16_t stickRightX;
    int16_t stickRightY;
};

struct ControllerEvents {
    std::function<void(uint8_t slot, uint16_t vendorId, uint16_t productId)> connected;
    std::function<void(uint8_t slot)> disconnected;
    std::function<void(uint8_t slot, const ControllerState& state)> input;
};

class Controller : public GipDevice {
public:
    Controller(SendPacket sendPacket, uint8_t slot, const ControllerEvents& events);
    ~Controller() override;

    /// Sets both motors' power, from 0 to 100
    void setRumble(uint8_t power);

private:
    void deviceAnnounced(uint8_t id, const AnnounceData* announce) override;
    void statusReceived(uint8_t id, const StatusData* status) override;
    void guideButtonPressed(const GuideButtonData* button) override;
    void serialNumberReceived(const SerialData* serial) override;
    void inputReceived(const InputData* input) override;

    void processRumble();

    const uint8_t slot;
    const ControllerEvents& events;
    bool announced = false;
    ControllerState state{};
    uint8_t batteryLevel = 0xff;

    std::atomic<bool> stopRumbleThread{false};
    std::atomic<uint8_t> rumblePower{0};
    std::mutex rumbleMutex;
    std::condition_variable rumbleCondition;
    std::thread rumbleThread;
};
