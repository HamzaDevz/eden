// SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
// SPDX-License-Identifier: GPL-3.0-or-later

#include <chrono>
#include <cstring>

#include "controller.h"
#include "log.h"

namespace {
// Accessories use IDs greater than zero
constexpr uint8_t DEVICE_ID_CONTROLLER = 0;

// Rumble packets sent too close together are dropped by the controller's firmware
constexpr auto RUMBLE_DELAY = std::chrono::milliseconds(10);
} // namespace

Controller::Controller(SendPacket sendPacket, uint8_t slot_, const ControllerEvents& events_)
    : GipDevice(std::move(sendPacket)), slot(slot_), events(events_),
      rumbleThread(&Controller::processRumble, this) {}

Controller::~Controller() {
    {
        std::lock_guard lock(rumbleMutex);
        stopRumbleThread = true;
    }
    rumbleCondition.notify_one();
    rumbleThread.join();

    if (announced) {
        events.disconnected(slot);
    }
    if (!setPowerMode(DEVICE_ID_CONTROLLER, POWER_OFF)) {
        Log::error("Failed to turn off controller");
    }
}

void Controller::setRumble(uint8_t power) {
    if (rumblePower.exchange(power) != power) {
        rumbleCondition.notify_one();
    }
}

void Controller::deviceAnnounced(uint8_t id, const AnnounceData* announce) {
    Log::info("Device announced, product id: %04x", announce->productId);

    // Dim the LED a little bit, like the original driver
    LedModeData ledMode = {};
    ledMode.mode = LED_ON;
    ledMode.brightness = 0x14;

    if (!setPowerMode(DEVICE_ID_CONTROLLER, POWER_ON)) {
        Log::error("Failed to set initial power mode");
        return;
    }
    if (!setLedMode(ledMode)) {
        Log::error("Failed to set initial LED mode");
        return;
    }
    if (!requestSerialNumber()) {
        Log::error("Failed to request serial number");
        return;
    }

    if (!announced) {
        announced = true;
        events.connected(slot, announce->vendorId, announce->productId);
    }
}

void Controller::statusReceived(uint8_t id, const StatusData* status) {
    const char* levels[] = {"empty", "low", "medium", "full"};
    const uint8_t level = status->batteryLevel;
    if (status->batteryType == BATT_TYPE_CHARGING || level == batteryLevel) {
        return;
    }
    Log::info("Controller %d battery level: %s", slot, levels[level]);
    batteryLevel = level;
}

void Controller::guideButtonPressed(const GuideButtonData* button) {
    if (button->pressed) {
        state.buttons |= CONTROLLER_GUIDE_BUTTON;
    } else {
        state.buttons &= ~CONTROLLER_GUIDE_BUTTON;
    }
    if (announced) {
        events.input(slot, state);
    }
}

void Controller::serialNumberReceived(const SerialData* serial) {}

void Controller::inputReceived(const InputData* input) {
    uint16_t buttons;
    std::memcpy(&buttons, &input->buttons, sizeof(buttons));

    state.buttons = (state.buttons & CONTROLLER_GUIDE_BUTTON) | buttons;
    state.triggerLeft = input->triggerLeft;
    state.triggerRight = input->triggerRight;
    state.stickLeftX = input->stickLeftX;
    state.stickLeftY = input->stickLeftY;
    state.stickRightX = input->stickRightX;
    state.stickRightY = input->stickRightY;
    if (announced) {
        events.input(slot, state);
    }
}

void Controller::processRumble() {
    uint8_t sentPower = 0;
    std::unique_lock lock(rumbleMutex);
    while (!stopRumbleThread) {
        rumbleCondition.wait(lock, [&] { return stopRumbleThread || rumblePower != sentPower; });
        if (stopRumbleThread) {
            break;
        }
        sentPower = rumblePower;

        RumbleData rumble = {};
        rumble.setRight = true;
        rumble.setLeft = true;
        rumble.left = sentPower;
        rumble.right = sentPower;
        // Longest duration, the next update or a zero power stops it
        rumble.duration = sentPower ? 0xff : 0;

        lock.unlock();
        performRumble(rumble);
        std::this_thread::sleep_for(RUMBLE_DELAY);
        lock.lock();
    }
}
