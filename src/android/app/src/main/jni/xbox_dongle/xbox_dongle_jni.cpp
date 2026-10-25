// SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
// SPDX-License-Identifier: GPL-3.0-or-later

#include <condition_variable>
#include <exception>
#include <memory>
#include <mutex>
#include <thread>

#include <jni.h>

#include "common/android/android_common.h"
#include "common/android/id_cache.h"
#include "dongle.h"
#include "log.h"

namespace {
std::thread worker;
std::mutex dongle_mutex;
std::condition_variable stop_condition;
bool stop_requested = false;
Dongle* dongle = nullptr;

jclass adapter_class = nullptr;
jmethodID on_pad_connected = nullptr;
jmethodID on_pad_disconnected = nullptr;
jmethodID on_pad_input = nullptr;

void RequestStop() {
    {
        std::lock_guard lock(dongle_mutex);
        stop_requested = true;
    }
    stop_condition.notify_all();
}

void CallAdapter(jmethodID method, auto... args) {
    JNIEnv* env = Common::Android::GetEnvForThread();
    env->CallStaticVoidMethod(adapter_class, method, static_cast<jint>(args)...);
    if (env->ExceptionCheck()) {
        env->ExceptionDescribe();
        env->ExceptionClear();
    }
}

/// Brings the dongle up, then serves its controllers until stopped or unplugged
void Run(int fd, std::string firmware_path) {
    ControllerEvents events;
    events.connected = [](uint8_t slot, uint16_t vendor_id, uint16_t product_id) {
        CallAdapter(on_pad_connected, slot, vendor_id, product_id);
    };
    events.disconnected = [](uint8_t slot) { CallAdapter(on_pad_disconnected, slot); };
    events.input = [](uint8_t slot, const ControllerState& state) {
        CallAdapter(on_pad_input, slot, state.buttons, state.triggerLeft, state.triggerRight,
                    state.stickLeftX, state.stickLeftY, state.stickRightX, state.stickRightY);
    };

    try {
        auto usb_device = std::make_unique<UsbDevice>(fd, RequestStop);
        auto new_dongle = std::make_unique<Dongle>(std::move(usb_device), firmware_path, events);
        std::unique_lock lock(dongle_mutex);
        dongle = new_dongle.get();
        stop_condition.wait(lock, [] { return stop_requested; });
        dongle = nullptr;
    } catch (const std::exception& e) {
        Log::error("Could not start the dongle: %s", e.what());
    }
}
} // namespace

extern "C" {

JNIEXPORT void JNICALL Java_org_yuzu_yuzu_1emu_features_input_XboxWirelessAdapter_startDongle(
    JNIEnv* env, jobject obj, jint fd, jstring j_firmware_path) {
    if (worker.joinable()) {
        return;
    }
    if (adapter_class == nullptr) {
        adapter_class = static_cast<jclass>(env->NewGlobalRef(env->GetObjectClass(obj)));
        on_pad_connected = env->GetStaticMethodID(adapter_class, "onPadConnected", "(III)V");
        on_pad_disconnected = env->GetStaticMethodID(adapter_class, "onPadDisconnected", "(I)V");
        on_pad_input = env->GetStaticMethodID(adapter_class, "onPadInput", "(IIIIIIII)V");
    }
    {
        std::lock_guard lock(dongle_mutex);
        stop_requested = false;
    }
    worker = std::thread(Run, fd, Common::Android::GetJString(env, j_firmware_path));
}

JNIEXPORT void JNICALL Java_org_yuzu_yuzu_1emu_features_input_XboxWirelessAdapter_stopDongle(
    JNIEnv* env, jobject obj) {
    if (!worker.joinable()) {
        return;
    }
    RequestStop();
    worker.join();
}

JNIEXPORT void JNICALL Java_org_yuzu_yuzu_1emu_features_input_XboxWirelessAdapter_setRumble(
    JNIEnv* env, jobject obj, jint slot, jint power) {
    std::lock_guard lock(dongle_mutex);
    if (dongle != nullptr) {
        dongle->setRumble(static_cast<uint8_t>(slot), static_cast<uint8_t>(power));
    }
}

} // extern "C"
