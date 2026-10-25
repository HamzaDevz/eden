// SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
// SPDX-License-Identifier: GPL-3.0-or-later

package org.yuzu.yuzu_emu.features.input

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbDeviceConnection
import android.hardware.usb.UsbInterface
import android.hardware.usb.UsbManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.view.KeyEvent
import android.view.MotionEvent
import androidx.annotation.Keep
import androidx.core.content.ContextCompat
import androidx.core.content.IntentCompat
import java.io.File
import org.yuzu.yuzu_emu.utils.InputHandler
import org.yuzu.yuzu_emu.utils.Log

/**
 * Microsoft's Xbox Wireless Adapter, which Android has no driver for. The native side loads the
 * adapter's firmware and speaks to its controllers like xow does on Linux. Each controller joins
 * the game like any other pad, and its Xbox button brings up the game list.
 *
 * The firmware is Microsoft's, not shipped with Eden: it is read from
 * `files/xbox_dongle/xone_dongle_<product id>.bin`, as xone's firmware script names it.
 */
object XboxWirelessAdapter {
    private const val VENDOR_ID = 0x045e

    // First and second adapter generations, and the ones built into some laptops
    private val PRODUCT_IDS = setOf(0x02e6, 0x02fe, 0x02f9, 0x091e)

    private const val ACTION_USB_PERMISSION = "org.yuzu.yuzu_emu.XBOX_WIRELESS_ADAPTER_PERMISSION"

    // One per wireless client the adapter can hold
    private const val SLOT_COUNT = 16

    private val mainHandler = Handler(Looper.getMainLooper())
    private val pads = Array(SLOT_COUNT) { Pad(it) }

    private var context: Context? = null
    private var onHomePressed: (() -> Unit)? = null
    private var connection: UsbDeviceConnection? = null
    private var usbInterface: UsbInterface? = null

    private val receiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            val device = IntentCompat.getParcelableExtra(
                intent,
                UsbManager.EXTRA_DEVICE,
                UsbDevice::class.java
            ) ?: return
            if (!isAdapter(device)) {
                return
            }
            when (intent.action) {
                UsbManager.ACTION_USB_DEVICE_ATTACHED -> open(device)
                UsbManager.ACTION_USB_DEVICE_DETACHED -> close()
                ACTION_USB_PERMISSION -> {
                    if (intent.getBooleanExtra(UsbManager.EXTRA_PERMISSION_GRANTED, false)) {
                        open(device)
                    }
                }
            }
        }
    }

    fun start(context: Context, onHomePressed: () -> Unit) {
        if (this.context != null) {
            return
        }
        val appContext = context.applicationContext
        this.context = appContext
        this.onHomePressed = onHomePressed
        val filter = IntentFilter().apply {
            addAction(UsbManager.ACTION_USB_DEVICE_ATTACHED)
            addAction(UsbManager.ACTION_USB_DEVICE_DETACHED)
            addAction(ACTION_USB_PERMISSION)
        }
        ContextCompat.registerReceiver(
            appContext,
            receiver,
            filter,
            ContextCompat.RECEIVER_NOT_EXPORTED
        )
        appContext.getSystemService(UsbManager::class.java).deviceList.values
            .firstOrNull(::isAdapter)?.let(::open)
    }

    fun stop() {
        val appContext = context ?: return
        appContext.unregisterReceiver(receiver)
        close()
        context = null
        onHomePressed = null
    }

    private fun isAdapter(device: UsbDevice): Boolean =
        device.vendorId == VENDOR_ID && device.productId in PRODUCT_IDS

    private fun open(device: UsbDevice) {
        val appContext = context ?: return
        if (connection != null) {
            return
        }
        val usbManager = appContext.getSystemService(UsbManager::class.java)
        if (!usbManager.hasPermission(device)) {
            val flags = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                PendingIntent.FLAG_MUTABLE
            } else {
                0
            }
            val intent = Intent(ACTION_USB_PERMISSION).setPackage(appContext.packageName)
            usbManager.requestPermission(
                device,
                PendingIntent.getBroadcast(appContext, 0, intent, flags)
            )
            return
        }

        val firmware = File(
            appContext.getExternalFilesDir(null),
            "xbox_dongle/xone_dongle_%04x.bin".format(device.productId)
        )
        if (!firmware.isFile) {
            Log.error("[XboxWirelessAdapter] Missing firmware ${firmware.path}")
            return
        }
        if (device.interfaceCount == 0) {
            return
        }
        val usbInterface = device.getInterface(0)
        val connection = usbManager.openDevice(device)
        if (connection == null) {
            Log.error("[XboxWirelessAdapter] Could not open the adapter")
            return
        }
        if (!connection.claimInterface(usbInterface, true)) {
            connection.close()
            Log.error("[XboxWirelessAdapter] Could not claim the adapter")
            return
        }

        this.usbInterface = usbInterface
        this.connection = connection
        startDongle(connection.fileDescriptor, firmware.path)
        Log.info("[XboxWirelessAdapter] Opened")
    }

    private fun close() {
        val connection = connection ?: return
        stopDongle()
        this.connection = null
        usbInterface?.let(connection::releaseInterface)
        connection.close()
        usbInterface = null
        Log.info("[XboxWirelessAdapter] Closed")
    }

    private fun onPadsChanged() {
        val connectedPads = pads.filter { it.connected }
        val joined = connectedPads - InputHandler.extraControllers.toSet()
        InputHandler.extraControllers =
            InputHandler.extraControllers.filter { it !in pads } + connectedPads
        joined.forEach(NativeInput::registerController)
        InputHandler.refreshPlayers()
        joined.forEach(InputHandler::claimPlayer)
    }

    @Keep
    @JvmStatic
    fun onPadConnected(slot: Int, vendorId: Int, productId: Int) {
        val pad = pads.getOrNull(slot) ?: return
        pad.guid = String.format("%016x%016x", productId, vendorId)
        mainHandler.post {
            pad.connected = true
            onPadsChanged()
        }
    }

    @Keep
    @JvmStatic
    fun onPadDisconnected(slot: Int) {
        val pad = pads.getOrNull(slot) ?: return
        pad.update(0, IntArray(Pad.AXES.size))
        mainHandler.post {
            pad.connected = false
            onPadsChanged()
        }
    }

    @Keep
    @JvmStatic
    fun onPadInput(
        slot: Int,
        buttons: Int,
        triggerLeft: Int,
        triggerRight: Int,
        stickLeftX: Int,
        stickLeftY: Int,
        stickRightX: Int,
        stickRightY: Int
    ) {
        pads.getOrNull(slot)?.update(
            buttons,
            intArrayOf(stickLeftX, stickLeftY, stickRightX, stickRightY, triggerLeft, triggerRight)
        )
    }

    private external fun startDongle(fd: Int, firmwarePath: String)

    private external fun stopDongle()

    private external fun setRumble(slot: Int, power: Int)

    private class Pad(private val slot: Int) : YuzuInputDevice {
        @Volatile
        var connected = false

        @Volatile
        var guid = ""

        private var buttons = 0
        private val axes = IntArray(AXES.size)
        private var rumble = 0

        override fun getName(): String = "Xbox Wireless Controller ${slot + 1}"

        override fun getGUID(): String = guid

        override fun getPort(): Int = slot

        override fun getSupportsVibration(): Boolean = true

        override fun vibrate(intensity: Float) {
            val power = (intensity * MAX_RUMBLE).toInt().coerceIn(0, MAX_RUMBLE)
            if (power != rumble) {
                rumble = power
                setRumble(slot, power)
            }
        }

        override fun getAxes(): Array<Int> = AXES

        override fun hasKeys(keys: IntArray): BooleanArray =
            BooleanArray(keys.size) { index -> BUTTONS.any { it.second == keys[index] } }

        /** Takes the controller's report, with the axes in [AXES] order */
        fun update(newButtons: Int, newAxes: IntArray) {
            val pressed = newButtons and buttons.inv()
            BUTTONS.forEach { (mask, keyCode) ->
                if ((newButtons xor buttons) and mask != 0) {
                    NativeInput.onGamePadButtonEvent(
                        guid,
                        slot,
                        keyCode,
                        if (newButtons and mask != 0) {
                            NativeInput.ButtonState.PRESSED
                        } else {
                            NativeInput.ButtonState.RELEASED
                        }
                    )
                }
            }
            buttons = newButtons
            if (pressed and GUIDE_BUTTON != 0) {
                mainHandler.post { onHomePressed?.invoke() }
            } else if (pressed != 0) {
                mainHandler.post { InputHandler.claimPlayer(this) }
            }

            for (axis in AXES.indices) {
                if (newAxes[axis] == axes[axis]) {
                    continue
                }
                axes[axis] = newAxes[axis]
                NativeInput.onGamePadAxisEvent(
                    guid,
                    slot,
                    AXES[axis],
                    when (AXES[axis]) {
                        // Android's vertical axes point down
                        MotionEvent.AXIS_Y, MotionEvent.AXIS_RZ ->
                            -(newAxes[axis] / STICK_RANGE).coerceIn(-1f, 1f)
                        MotionEvent.AXIS_LTRIGGER, MotionEvent.AXIS_RTRIGGER ->
                            (newAxes[axis] / TRIGGER_RANGE).coerceIn(0f, 1f)
                        else -> (newAxes[axis] / STICK_RANGE).coerceIn(-1f, 1f)
                    }
                )
            }
        }

        companion object {
            private const val GUIDE_BUTTON = 0x10000
            private const val STICK_RANGE = 32767f
            private const val TRIGGER_RANGE = 1023f
            private const val MAX_RUMBLE = 100

            // Left stick, right stick, then the triggers, in the order onPadInput passes them
            val AXES = arrayOf(
                MotionEvent.AXIS_X,
                MotionEvent.AXIS_Y,
                MotionEvent.AXIS_Z,
                MotionEvent.AXIS_RZ,
                MotionEvent.AXIS_LTRIGGER,
                MotionEvent.AXIS_RTRIGGER
            )

            // GIP report bits and the keys they stand for
            private val BUTTONS = listOf(
                0x0004 to KeyEvent.KEYCODE_BUTTON_START,
                0x0008 to KeyEvent.KEYCODE_BUTTON_SELECT,
                0x0010 to KeyEvent.KEYCODE_BUTTON_A,
                0x0020 to KeyEvent.KEYCODE_BUTTON_B,
                0x0040 to KeyEvent.KEYCODE_BUTTON_X,
                0x0080 to KeyEvent.KEYCODE_BUTTON_Y,
                0x0100 to KeyEvent.KEYCODE_DPAD_UP,
                0x0200 to KeyEvent.KEYCODE_DPAD_DOWN,
                0x0400 to KeyEvent.KEYCODE_DPAD_LEFT,
                0x0800 to KeyEvent.KEYCODE_DPAD_RIGHT,
                0x1000 to KeyEvent.KEYCODE_BUTTON_L1,
                0x2000 to KeyEvent.KEYCODE_BUTTON_R1,
                0x4000 to KeyEvent.KEYCODE_BUTTON_THUMBL,
                0x8000 to KeyEvent.KEYCODE_BUTTON_THUMBR
            )
        }
    }
}
