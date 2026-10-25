// SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
// SPDX-License-Identifier: GPL-3.0-or-later

package org.yuzu.yuzu_emu.activities

import android.app.Activity
import android.os.Bundle

/**
 * Lets Android offer to always hand the GameCube adapter over to Eden when it is plugged in.
 * The running game picks the adapter up by itself.
 */
class GameCubeAdapterActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        finish()
    }
}
