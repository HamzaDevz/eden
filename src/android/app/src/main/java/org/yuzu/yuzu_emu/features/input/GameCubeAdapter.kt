// SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
// SPDX-License-Identifier: GPL-3.0-or-later

package org.yuzu.yuzu_emu.features.input

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.hardware.usb.UsbConstants
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbDeviceConnection
import android.hardware.usb.UsbEndpoint
import android.hardware.usb.UsbInterface
import android.hardware.usb.UsbManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.view.KeyEvent
import android.view.MotionEvent
import androidx.core.content.ContextCompat
import androidx.core.content.IntentCompat
import org.yuzu.yuzu_emu.utils.InputHandler
import org.yuzu.yuzu_emu.utils.Log

/**
 * Nintendo's GameCube controller adapter, which Android has no driver for. Each controller
 * plugged into it joins the game like any other pad, and leaves it when unplugged or when the
 * adapter goes away.
 */
object GameCubeAdapter {
    private const val VENDOR_ID = 0x057e
    private const val PRODUCT_ID = 0x0337

    // Kept clear of Nintendo's vendor ID, for which the automatic mapping swaps A and B
    private const val GUID = "00000000000003370000000000000c0c"

    private const val ACTION_USB_PERMISSION = "org.yuzu.yuzu_emu.GAMECUBE_ADAPTER_PERMISSION"

    private const val PORT_COUNT = 4
    private const val PAYLOAD_SIZE = 37
    private const val PAYLOAD_HEADER: Byte = 0x21
    private const val START_POLLING: Byte = 0x13
    private const val RUMBLE: Byte = 0x11
    private const val TIMEOUT_MS = 32

    // The adapter reports every 8 ms, so this many missed reports means it stopped polling
    private const val MAX_MISSED_REPORTS = 30

    private val mainHandler = Handler(Looper.getMainLooper())
    private val pads = Array(PORT_COUNT) { Pad(it) }

    private var context: Context? = null
    private var usbDevice: UsbDevice? = null
    private var usbInterface: UsbInterface? = null
    private var readThread: Thread? = null

    @Volatile
    private var connection: UsbDeviceConnection? = null

    @Volatile
    private var inEndpoint: UsbEndpoint? = null

    @Volatile
    private var outEndpoint: UsbEndpoint? = null

    @Volatile
    private var rumbleChanged = false

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

    fun start(context: Context) {
        if (this.context != null) {
            return
        }
        val appContext = context.applicationContext
        this.context = appContext
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
    }

    private fun isAdapter(device: UsbDevice): Boolean =
        device.vendorId == VENDOR_ID && device.productId == PRODUCT_ID

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

        if (device.interfaceCount == 0) {
            return
        }
        val usbInterface = device.getInterface(0)
        val endpoints = (0 until usbInterface.endpointCount).map(usbInterface::getEndpoint)
        val inEndpoint = endpoints.firstOrNull { it.direction == UsbConstants.USB_DIR_IN }
        val outEndpoint = endpoints.firstOrNull { it.direction == UsbConstants.USB_DIR_OUT }
        val connection = usbManager.openDevice(device)
        if (inEndpoint == null || outEndpoint == null || connection == null) {
            connection?.close()
            Log.error("[GameCubeAdapter] Could not open the adapter")
            return
        }
        if (!connection.claimInterface(usbInterface, true)) {
            connection.close()
            Log.error("[GameCubeAdapter] Could not claim the adapter")
            return
        }

        this.usbDevice = device
        this.usbInterface = usbInterface
        this.inEndpoint = inEndpoint
        this.outEndpoint = outEndpoint
        this.connection = connection
        readThread = Thread(::read, "GameCubeAdapter").apply { start() }
        Log.info("[GameCubeAdapter] Opened")
    }

    private fun close() {
        val connection = connection ?: return
        this.connection = null
        readThread?.join()
        readThread = null
        usbInterface?.let(connection::releaseInterface)
        connection.close()
        usbDevice = null
        usbInterface = null
        inEndpoint = null
        outEndpoint = null
        pads.forEach { it.connected = false }
        onPadsChanged()
        Log.info("[GameCubeAdapter] Closed")
    }

    private fun read() {
        val payload = ByteArray(PAYLOAD_SIZE)
        var missedReports = MAX_MISSED_REPORTS
        while (true) {
            val connection = connection ?: break
            val inEndpoint = inEndpoint ?: break
            val outEndpoint = outEndpoint ?: break

            if (missedReports >= MAX_MISSED_REPORTS) {
                connection.bulkTransfer(outEndpoint, byteArrayOf(START_POLLING), 1, TIMEOUT_MS)
                missedReports = 0
            }
            if (rumbleChanged) {
                rumbleChanged = false
                val rumble = byteArrayOf(RUMBLE) + pads.map { it.rumble }.toByteArray()
                connection.bulkTransfer(outEndpoint, rumble, rumble.size, TIMEOUT_MS)
            }

            val size = connection.bulkTransfer(inEndpoint, payload, PAYLOAD_SIZE, TIMEOUT_MS)
            if (size != PAYLOAD_SIZE || payload[0] != PAYLOAD_HEADER) {
                missedReports++
                continue
            }
            missedReports = 0

            var padsChanged = false
            pads.forEach { padsChanged = it.update(payload, 1 + it.getPort() * 9) || padsChanged }
            if (padsChanged) {
                mainHandler.post(::onPadsChanged)
            }
        }
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

    private class Pad(private val index: Int) : YuzuInputDevice {
        @Volatile
        var connected = false

        @Volatile
        var rumble: Byte = 0

        private var buttons = 0
        private val axes = IntArray(AXES.size)
        private val origins = IntArray(AXES.size)

        // Sticks and triggers report their resting position over the first reports
        private var originReports = 0

        override fun getName(): String = "GameCube Controller ${index + 1}"

        override fun getGUID(): String = GUID

        override fun getPort(): Int = index

        override fun getSupportsVibration(): Boolean = true

        override fun vibrate(intensity: Float) {
            val value: Byte = if (intensity > 0.1f) 1 else 0
            if (value != rumble) {
                rumble = value
                rumbleChanged = true
            }
        }

        override fun getAxes(): Array<Int> = AXES

        override fun hasKeys(keys: IntArray): BooleanArray =
            BooleanArray(keys.size) { index -> BUTTONS.any { it.second == keys[index] } }

        private fun setButtons(newButtons: Int) {
            BUTTONS.forEach { (mask, keyCode) ->
                if ((newButtons xor buttons) and mask != 0) {
                    NativeInput.onGamePadButtonEvent(
                        GUID,
                        index,
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
        }

        /** Reads this pad's part of a report. Returns whether the pad was plugged or unplugged. */
        fun update(payload: ByteArray, offset: Int): Boolean {
            val type = (payload[offset].toInt() and 0xFF) shr 4
            val plugged = type == WIRED || type == WIRELESS
            val newButtons = if (plugged) {
                (payload[offset + 1].toInt() and 0xFF) or
                    ((payload[offset + 2].toInt() and 0xFF) shl 8)
            } else {
                0
            }
            if (plugged != connected) {
                setButtons(0)
                connected = plugged
                originReports = 0
                return true
            }
            if (!plugged) {
                return false
            }

            val pressed = newButtons and buttons.inv()
            setButtons(newButtons)
            if (pressed != 0) {
                mainHandler.post { InputHandler.claimPlayer(this) }
            }

            for (axis in AXES.indices) {
                val raw = payload[offset + 3 + axis].toInt() and 0xFF
                if (originReports < ORIGIN_REPORTS) {
                    if (origins[axis] != raw) {
                        originReports = 0
                    }
                    origins[axis] = raw
                }
                if (raw == axes[axis]) {
                    continue
                }
                axes[axis] = raw
                val value = (raw - origins[axis]) / AXIS_RANGE
                NativeInput.onGamePadAxisEvent(
                    GUID,
                    index,
                    AXES[axis],
                    when (AXES[axis]) {
                        // Android's vertical axes point down
                        MotionEvent.AXIS_Y, MotionEvent.AXIS_RZ -> -value.coerceIn(-1f, 1f)
                        MotionEvent.AXIS_LTRIGGER,
                        MotionEvent.AXIS_RTRIGGER -> value.coerceIn(0f, 1f)
                        else -> value.coerceIn(-1f, 1f)
                    }
                )
            }
            if (originReports < ORIGIN_REPORTS) {
                originReports++
            }
            return false
        }

        companion object {
            private const val WIRED = 1
            private const val WIRELESS = 2
            private const val ORIGIN_REPORTS = 18
            private const val AXIS_RANGE = 100f

            // Main stick, C stick, then the analog L and R, in report order
            private val AXES = arrayOf(
                MotionEvent.AXIS_X,
                MotionEvent.AXIS_Y,
                MotionEvent.AXIS_Z,
                MotionEvent.AXIS_RZ,
                MotionEvent.AXIS_LTRIGGER,
                MotionEvent.AXIS_RTRIGGER
            )

            // Report bits and the keys they stand for. Z plays as R like on Nintendo's own
            // adapter support, and the digital L and R clicks are left to the analog triggers.
            private val BUTTONS = listOf(
                0x0001 to KeyEvent.KEYCODE_BUTTON_A,
                0x0002 to KeyEvent.KEYCODE_BUTTON_B,
                0x0004 to KeyEvent.KEYCODE_BUTTON_X,
                0x0008 to KeyEvent.KEYCODE_BUTTON_Y,
                0x0010 to KeyEvent.KEYCODE_DPAD_LEFT,
                0x0020 to KeyEvent.KEYCODE_DPAD_RIGHT,
                0x0040 to KeyEvent.KEYCODE_DPAD_DOWN,
                0x0080 to KeyEvent.KEYCODE_DPAD_UP,
                0x0100 to KeyEvent.KEYCODE_BUTTON_START,
                0x0200 to KeyEvent.KEYCODE_BUTTON_R1
            )
        }
    }
}
