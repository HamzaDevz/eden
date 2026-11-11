// SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
// SPDX-License-Identifier: GPL-3.0-or-later

package org.yuzu.yuzu_emu.utils

import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Handler
import android.os.Looper

/**
 * Programs that drive the game through its bridge can live in Termux (on the Odin 3, the Smash
 * AIs and everyone's points), which Android kills when the game needs the memory. While a game
 * plays, Termux is asked every minute to run ~/odin3/eden_hook.sh, which starts them again when
 * they are gone. Only with Termux's RUN_COMMAND permission granted to Eden, and Termux's
 * allow-external-apps on.
 */
object TermuxHook {
    private const val PACKAGE = "com.termux"
    private const val PERMISSION = "com.termux.permission.RUN_COMMAND"
    private const val HOME = "/data/data/com.termux/files/home"
    private const val SHELL = "/data/data/com.termux/files/usr/bin/sh"
    private const val EVERY_MS = 60_000L

    private val handler = Handler(Looper.getMainLooper())
    private var context: Context? = null
    private val tick = object : Runnable {
        override fun run() {
            ask()
            handler.postDelayed(this, EVERY_MS)
        }
    }

    fun start(context: Context) {
        if (context.checkSelfPermission(PERMISSION) != PackageManager.PERMISSION_GRANTED) {
            return
        }
        this.context = context.applicationContext
        handler.removeCallbacks(tick)
        handler.post(tick)
    }

    fun stop() {
        handler.removeCallbacks(tick)
        context = null
    }

    private fun ask() {
        val context = context ?: return
        val intent = Intent("$PACKAGE.RUN_COMMAND")
            .setClassName(PACKAGE, "$PACKAGE.app.RunCommandService")
            .putExtra("$PACKAGE.RUN_COMMAND_PATH", SHELL)
            .putExtra("$PACKAGE.RUN_COMMAND_ARGUMENTS", arrayOf("$HOME/odin3/eden_hook.sh"))
            .putExtra("$PACKAGE.RUN_COMMAND_WORKDIR", HOME)
            .putExtra("$PACKAGE.RUN_COMMAND_BACKGROUND", true)
        try {
            context.startForegroundService(intent)
        } catch (e: Exception) {
            Log.warning("[TermuxHook] Termux not asked: ${e.message}")
        }
    }
}
