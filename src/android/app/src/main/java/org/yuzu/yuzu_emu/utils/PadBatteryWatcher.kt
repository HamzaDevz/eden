// SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
// SPDX-License-Identifier: GPL-3.0-or-later

package org.yuzu.yuzu_emu.utils

import android.app.Activity
import android.os.Handler
import android.os.Looper
import android.widget.Toast
import java.io.File

/**
 * Tells a controller's battery as it connects, runs low or drops on a flat battery, from the line
 * the console's xboxd writes next to Eden's files. Its notifications reach the handheld's own
 * screen only, so this is what shows it on the TV when docked.
 */
object PadBatteryWatcher {
    private const val POLL_MS = 1500L
    private const val ALERT_LIFE_MS = 20000L

    private val handler = Handler(Looper.getMainLooper())
    private var activity: Activity? = null
    private var lastModified = 0L
    private var lastAlert = 0L

    private val poll = object : Runnable {
        override fun run() {
            check(activity ?: return)
            handler.postDelayed(this, POLL_MS)
        }
    }

    fun start(activity: Activity) {
        this.activity = activity
        handler.removeCallbacks(poll)
        handler.post(poll)
    }

    fun stop(activity: Activity) {
        if (this.activity === activity) {
            this.activity = null
            handler.removeCallbacks(poll)
        }
    }

    private fun check(activity: Activity) {
        val file = File(activity.getExternalFilesDir(null), "pads.txt")
        val modified = file.lastModified()
        if (modified == 0L || modified == lastModified) {
            return
        }
        lastModified = modified
        val alert = runCatching { file.readText().trim().split('\t') }.getOrNull() ?: return
        val time = alert.firstOrNull()?.toLongOrNull() ?: return
        if (alert.size < 3 || time <= lastAlert ||
            System.currentTimeMillis() - time > ALERT_LIFE_MS
        ) {
            return
        }
        lastAlert = time
        Toast.makeText(activity, "${alert[1]} (${alert[2]})", Toast.LENGTH_LONG).show()
    }
}
