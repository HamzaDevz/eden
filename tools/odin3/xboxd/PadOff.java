// SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
// SPDX-License-Identifier: GPL-3.0-or-later

// Drops a Bluetooth controller's link, which turns a DualShock 4 off. Run by xboxd through
// app_process as the shell user, whose BLUETOOTH_PRIVILEGED covers the system-only disconnect().

import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothDevice;
import android.os.Looper;

public final class PadOff {
    public static void main(String[] args) throws Exception {
        // What an app process gets before onCreate: without it getDefaultAdapter() is null
        Looper.prepareMainLooper();
        Class.forName("android.app.ActivityThread")
                .getMethod("initializeMainlineModules")
                .invoke(null);
        final BluetoothDevice device =
                BluetoothAdapter.getDefaultAdapter().getRemoteDevice(args[0].toUpperCase());
        final Object status = BluetoothDevice.class.getMethod("disconnect").invoke(device);
        System.out.println(status);
        System.exit(Integer.valueOf(0).equals(status) ? 0 : 1);
    }
}
