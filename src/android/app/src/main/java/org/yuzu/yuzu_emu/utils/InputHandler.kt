// SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
// SPDX-License-Identifier: GPL-3.0-or-later

// SPDX-FileCopyrightText: 2023 yuzu Emulator Project
// SPDX-License-Identifier: GPL-3.0-or-later

package org.yuzu.yuzu_emu.utils

import android.view.InputDevice
import android.view.KeyEvent
import android.view.MotionEvent
import java.io.File
import org.yuzu.yuzu_emu.features.input.NativeInput
import org.yuzu.yuzu_emu.features.input.YuzuInputOverlayDevice
import org.yuzu.yuzu_emu.features.input.YuzuPhysicalDevice
import org.yuzu.yuzu_emu.features.input.model.NativeButton

object InputHandler {
    var androidControllers = mapOf<Int, YuzuPhysicalDevice>()
    var registeredControllers = mutableListOf<ParamPackage>()

    private val controllerButtons = intArrayOf(
        KeyEvent.KEYCODE_BUTTON_A,
        KeyEvent.KEYCODE_BUTTON_B,
        KeyEvent.KEYCODE_BUTTON_X,
        KeyEvent.KEYCODE_BUTTON_Y,
        KeyEvent.KEYCODE_BUTTON_L1,
        KeyEvent.KEYCODE_BUTTON_R1,
        KeyEvent.KEYCODE_BUTTON_L2,
        KeyEvent.KEYCODE_BUTTON_R2,
        KeyEvent.KEYCODE_BUTTON_THUMBL,
        KeyEvent.KEYCODE_BUTTON_THUMBR,
        KeyEvent.KEYCODE_BUTTON_START,
        KeyEvent.KEYCODE_BUTTON_SELECT,
        KeyEvent.KEYCODE_DPAD_UP,
        KeyEvent.KEYCODE_DPAD_DOWN,
        KeyEvent.KEYCODE_DPAD_LEFT,
        KeyEvent.KEYCODE_DPAD_RIGHT
    )

    private val controllerAxes = intArrayOf(
        MotionEvent.AXIS_X,
        MotionEvent.AXIS_Y,
        MotionEvent.AXIS_Z,
        MotionEvent.AXIS_RX,
        MotionEvent.AXIS_RY,
        MotionEvent.AXIS_RZ,
        MotionEvent.AXIS_HAT_X,
        MotionEvent.AXIS_HAT_Y,
        MotionEvent.AXIS_LTRIGGER,
        MotionEvent.AXIS_RTRIGGER
    )

    // Currently, Android doesn't support Joy-Con D-pad buttons. We fall back to the scan code
    private const val LINUX_BUTTON_DPAD_UP = 0x220
    private const val LINUX_BUTTON_DPAD_DOWN = 0x221
    private const val LINUX_BUTTON_DPAD_LEFT = 0x222
    private const val LINUX_BUTTON_DPAD_RIGHT = 0x223

    fun isPhysicalGameController(device: InputDevice?): Boolean {
        device ?: return false

        if (device.isVirtual) {
            return false
        }

        val sources = device.sources
        val hasControllerSource =
            sources and InputDevice.SOURCE_GAMEPAD == InputDevice.SOURCE_GAMEPAD ||
                sources and InputDevice.SOURCE_JOYSTICK == InputDevice.SOURCE_JOYSTICK
        if (!hasControllerSource) {
            return false
        }

        val hasControllerButtons = device.hasKeys(*controllerButtons).any { it }
        val hasControllerAxes = device.motionRanges.any { range ->
            controllerAxes.contains(range.axis)
        }
        return hasControllerButtons || hasControllerAxes
    }

    fun dispatchKeyEvent(event: KeyEvent): Boolean {
        val action = when (event.action) {
            KeyEvent.ACTION_DOWN -> NativeInput.ButtonState.PRESSED
            KeyEvent.ACTION_UP -> NativeInput.ButtonState.RELEASED
            else -> return false
        }

        var controllerData = androidControllers[event.device.controllerNumber]
        if (controllerData == null) {
            updateControllerData()
            controllerData = androidControllers[event.device.controllerNumber] ?: return false
        }

        if (event.action == KeyEvent.ACTION_DOWN && event.repeatCount == 0) {
            takeOverPlayerOne(controllerData)
        }

        NativeInput.onGamePadButtonEvent(
            controllerData.getGUID(),
            controllerData.getPort(),
            getButtonIdFromEvent(event),
            action
        )
        return true
    }

    // Like a Switch, a button pressed on a pad no other player holds hands player 1 over to it
    private fun takeOverPlayerOne(device: YuzuPhysicalDevice) {
        if (isBoundTo(device, 0) ||
            (1 until 8).any { NativeInput.getIsConnected(it) && isBoundTo(device, it) }
        ) {
            return
        }

        val profile = findProfile(device)
        if (profile != null && NativeInput.switchPlayerOneProfile(profile)) {
            return
        }
        val params = registeredControllers.firstOrNull {
            it.get("guid", "") == device.getGUID() && it.get("port", 0) == device.getPort()
        } ?: return
        NativeInput.updateMappingsWithDefault(0, params, device.getName())
    }

    private fun isBoundTo(device: YuzuPhysicalDevice, playerIndex: Int): Boolean =
        isDevice(NativeInput.getButtonParam(playerIndex, NativeButton.A), device)

    private fun isDevice(params: ParamPackage, device: YuzuPhysicalDevice): Boolean =
        params.get("engine", "") == "android" &&
            params.get("guid", "") == device.getGUID() &&
            params.get("port", 0) == device.getPort()

    private fun findProfile(device: YuzuPhysicalDevice): String? {
        val profileDir = File(DirectoryInitialization.userDirectory, "config/input")
        NativeInput.loadInputProfiles()
        return NativeInput.getInputProfileNames().firstOrNull { name ->
            val buttonA = File(profileDir, "$name.ini").takeIf { it.isFile }
                ?.useLines { lines -> lines.firstOrNull { it.startsWith("button_a=") } }
                ?: return@firstOrNull false
            isDevice(ParamPackage(buttonA.substringAfter('=').trim('"')), device)
        }
    }

    fun getButtonIdFromEvent(event: KeyEvent): Int {
        if (event.keyCode == 0) {
            return when (event.scanCode) {
                LINUX_BUTTON_DPAD_UP -> KeyEvent.KEYCODE_DPAD_UP
                LINUX_BUTTON_DPAD_DOWN -> KeyEvent.KEYCODE_DPAD_DOWN
                LINUX_BUTTON_DPAD_LEFT -> KeyEvent.KEYCODE_DPAD_LEFT
                LINUX_BUTTON_DPAD_RIGHT -> KeyEvent.KEYCODE_DPAD_RIGHT
                else -> return 0
            }
        }
        return event.keyCode
    }

    fun dispatchGenericMotionEvent(event: MotionEvent): Boolean {
        val controllerData =
            androidControllers[event.device.controllerNumber] ?: return false
        event.device.motionRanges.forEach {
            NativeInput.onGamePadAxisEvent(
                controllerData.getGUID(),
                controllerData.getPort(),
                getAxisId(event.device, it.axis),
                event.getAxisValue(it.axis)
            )
        }
        return true
    }

    // Pads that only report GAS and BRAKE get them as the trigger axes, so a binding holds
    // whichever of the two layouts AYN's copies of a controller come up with.
    fun getAxisId(device: InputDevice, axis: Int): Int = when {
        axis == MotionEvent.AXIS_GAS &&
            device.getMotionRange(MotionEvent.AXIS_RTRIGGER) == null -> MotionEvent.AXIS_RTRIGGER
        axis == MotionEvent.AXIS_BRAKE &&
            device.getMotionRange(MotionEvent.AXIS_LTRIGGER) == null -> MotionEvent.AXIS_LTRIGGER
        else -> axis
    }

    fun getDevices(): Map<Int, YuzuPhysicalDevice> {
        val gameControllerDeviceIds = mutableMapOf<Int, YuzuPhysicalDevice>()
        val deviceIds = InputDevice.getDeviceIds()
        // Ports count per GUID, so a binding survives other controllers coming and going
        val nextPorts = mutableMapOf<String, Int>()
        val inputSettings = NativeConfig.getInputSettings(true)
        deviceIds.forEach { deviceId ->
            InputDevice.getDevice(deviceId)?.apply {
                if (isPhysicalGameController(this) &&
                    !gameControllerDeviceIds.contains(controllerNumber)
                ) {
                    val guid = getGUID()
                    val port = nextPorts.getOrDefault(guid, 0)
                    nextPorts[guid] = port + 1
                    gameControllerDeviceIds[controllerNumber] = YuzuPhysicalDevice(
                        this,
                        port,
                        inputSettings[port].useSystemVibrator
                    )
                }
            }
        }
        return gameControllerDeviceIds
    }

    fun updateControllerData() {
        androidControllers = getDevices()
        androidControllers.forEach {
            NativeInput.registerController(it.value)
        }

        // Register the input overlay on a dedicated port for all player 1 vibrations
        NativeInput.registerController(YuzuInputOverlayDevice(androidControllers.isEmpty(), 100))
        registeredControllers.clear()
        NativeInput.getInputDevices().forEach {
            registeredControllers.add(ParamPackage(it))
        }
        registeredControllers.sortBy { it.get("port", 0) }
    }

    // AYN's mapping service hides real controllers behind uinput copies under its own vendor ID
    // and flips their product ID with the trigger layout, so those are told apart by name.
    private const val AYN_VENDOR_ID = 0x2020

    fun InputDevice.getGUID(): String = if (vendorId == AYN_VENDOR_ID) {
        String.format("%016x%016x", name.hashCode().toLong() and 0xFFFFFFFFL, vendorId)
    } else {
        String.format("%016x%016x", productId, vendorId)
    }
}
