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
import org.yuzu.yuzu_emu.features.input.YuzuInputDevice
import org.yuzu.yuzu_emu.features.input.YuzuInputOverlayDevice
import org.yuzu.yuzu_emu.features.input.YuzuPhysicalDevice
import org.yuzu.yuzu_emu.features.input.model.NativeButton

object InputHandler {
    var androidControllers = mapOf<Int, YuzuPhysicalDevice>()
    var registeredControllers = mutableListOf<ParamPackage>()

    // Pads read by Eden itself rather than by Android, such as the GameCube adapter's
    var extraControllers = listOf<YuzuInputDevice>()

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
            claimPlayer(controllerData)
        }

        NativeInput.onGamePadButtonEvent(
            controllerData.getGUID(),
            controllerData.getPort(),
            getButtonIdFromEvent(event),
            action
        )
        return true
    }

    private const val PLAYER_COUNT = 8

    // The handheld's own pad, which gives player 1 up to any pad that joins
    private const val BUILT_IN_CONTROLLER_NAME = "Odin Controller"

    private fun isBuiltIn(device: YuzuInputDevice): Boolean =
        device.getName() == BUILT_IN_CONTROLLER_NAME

    /**
     * Like a Switch, a pad that plays as no one joins on a button press: as player 1 if the
     * handheld's own pad or no pad holds it, else as the first free player. The handheld's own
     * pad takes player 1 back when no one else plays.
     */
    fun claimPlayer(device: YuzuInputDevice) {
        if ((0 until PLAYER_COUNT).any { isPlaying(device, it) }) {
            return
        }
        val holders = (0 until PLAYER_COUNT).map(::holderOf)
        val playerIndex = if (isBuiltIn(device) && holders.drop(1).all { it == null }) {
            0
        } else {
            holders.indexOfFirst { it == null || (isBuiltIn(it) && !isBuiltIn(device)) }
        }
        if (playerIndex >= 0) {
            assignPlayer(playerIndex, device)
        }
    }

    /**
     * Frees the players whose pad went away, and hands player 1 back to the handheld's own pad
     * when its pad went away too or when the handheld leaves its dock.
     */
    fun refreshPlayers(undocked: Boolean = false) {
        // Players set up without a pad, such as those driven by TAS, are left connected
        for (playerIndex in 1 until PLAYER_COUNT) {
            val params = NativeInput.getButtonParam(playerIndex, NativeButton.A)
            if (params.get("engine", "") == "android" && params.get("guid", "").isNotEmpty() &&
                NativeInput.getIsConnected(playerIndex) && holderOf(playerIndex) == null
            ) {
                NativeInput.releasePlayer(playerIndex)
            }
        }
        val builtIn = androidControllers.values.firstOrNull(::isBuiltIn) ?: return
        if ((undocked || holderOf(0) == null) && !isPlaying(builtIn, 0)) {
            assignPlayer(0, builtIn)
        }
    }

    private fun assignPlayer(playerIndex: Int, device: YuzuInputDevice) {
        Log.info("[InputHandler] ${device.getName()} plays as player ${playerIndex + 1}")
        NativeInput.assignPlayer(
            playerIndex,
            findProfile(device) ?: "",
            device.getGUID(),
            device.getPort(),
            device.getName()
        )
    }

    private fun holderOf(playerIndex: Int): YuzuInputDevice? {
        if (!NativeInput.getIsConnected(playerIndex)) {
            return null
        }
        val params = NativeInput.getButtonParam(playerIndex, NativeButton.A)
        return (androidControllers.values + extraControllers).firstOrNull { isDevice(params, it) }
    }

    private fun isPlaying(device: YuzuInputDevice, playerIndex: Int): Boolean =
        NativeInput.getIsConnected(playerIndex) &&
            isDevice(NativeInput.getButtonParam(playerIndex, NativeButton.A), device)

    private fun isDevice(params: ParamPackage, device: YuzuInputDevice): Boolean =
        params.get("engine", "") == "android" &&
            params.get("guid", "") == device.getGUID() &&
            params.get("port", 0) == device.getPort()

    private fun findProfile(device: YuzuInputDevice): String? {
        val profileDir = File(DirectoryInitialization.userDirectory, "config/input")
        NativeInput.loadInputProfiles()
        return NativeInput.getInputProfileNames().firstOrNull { name ->
            val buttonA = File(profileDir, "$name.ini").takeIf { it.isFile }
                ?.useLines { lines -> lines.firstOrNull { it.startsWith("button_a=") } }
                ?: return@firstOrNull false
            isDevice(ParamPackage(buttonA.substringAfter('=').trim('"')), device)
        }
    }

    fun isMenuButton(keyCode: Int): Boolean =
        keyCode == KeyEvent.KEYCODE_BUTTON_MODE || keyCode == KeyEvent.KEYCODE_BACK

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
