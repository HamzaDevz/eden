// SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
// SPDX-License-Identifier: GPL-3.0-or-later

// Shows the controllers' batteries among the console's notifications: an ongoing list of the
// pads connected and, as one connects, runs low or drops, a heads-up alert. Run by xboxd through
// app_process as the shell user, which posts as com.android.shell; there is no Context there, so
// the notifications go straight to the notification service.
//
// Arguments: the alert's title ("" for none), the alert's battery, then one entry per pad. A
// battery is "text|address": an empty text is read from the Bluetooth stack for that address.
// An entry is "label|text|address". The alert also goes to Eden, which shows it where it plays:
// on the TV when docked, where the console's notifications never show.

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.bluetooth.BluetoothAdapter;
import android.graphics.drawable.Icon;
import android.os.IBinder;
import android.os.Looper;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

public final class PadNotify {
    private static final String PACKAGE = "com.android.shell";
    private static final String LIST_CHANNEL = "odin3_pads";
    private static final String ALERT_CHANNEL = "odin3_pads_alert";
    private static final String TAG = "odin3_pads";
    private static final int LIST_ID = 1;
    private static final int ALERT_ID = 2;
    private static final long ALERT_LIFE_MS = 15000;
    private static final String EDEN_FILES =
            "/sdcard/Android/data/com.miHoYo.Yuanshen.relWithDebInfo/files";

    private static Object manager;

    public static void main(String[] args) throws Exception {
        // What an app process gets before onCreate: without it getDefaultAdapter() is null
        Looper.prepareMainLooper();
        Class.forName("android.app.ActivityThread")
                .getMethod("initializeMainlineModules")
                .invoke(null);
        final IBinder binder = (IBinder) Class.forName("android.os.ServiceManager")
                .getMethod("getService", String.class)
                .invoke(null, "notification");
        manager = Class.forName("android.app.INotificationManager$Stub")
                .getMethod("asInterface", IBinder.class)
                .invoke(null, binder);

        final NotificationChannel list = new NotificationChannel(
                LIST_CHANNEL, "Batterie des manettes", NotificationManager.IMPORTANCE_LOW);
        final NotificationChannel alert = new NotificationChannel(
                ALERT_CHANNEL, "Manette connectée, faible ou partie",
                NotificationManager.IMPORTANCE_HIGH);
        alert.setSound(null, null);
        alert.enableVibration(false);
        call("createNotificationChannels", PACKAGE,
                Class.forName("android.content.pm.ParceledListSlice")
                        .getConstructor(List.class)
                        .newInstance(Arrays.asList(list, alert)));

        final List<String> lines = new ArrayList<>();
        for (int i = 2; i < args.length; ++i) {
            final String[] fields = args[i].split("\\|", -1);
            lines.add(fields[0] + " : " + battery(fields[1], fields.length > 2 ? fields[2] : ""));
        }
        if (lines.isEmpty()) {
            call("cancelNotificationWithTag", PACKAGE, PACKAGE, TAG, LIST_ID, 0);
        } else {
            final Notification notification =
                    build(LIST_CHANNEL, "Manettes", String.join(" · ", lines));
            notification.extras.putCharSequenceArray(
                    Notification.EXTRA_TEXT_LINES, lines.toArray(new CharSequence[0]));
            notification.extras.putString(
                    Notification.EXTRA_TEMPLATE, "android.app.Notification$InboxStyle");
            notification.flags |= Notification.FLAG_ONGOING_EVENT | Notification.FLAG_NO_CLEAR;
            notification.flags |= Notification.FLAG_ONLY_ALERT_ONCE;
            call("enqueueNotificationWithTag", PACKAGE, PACKAGE, TAG, LIST_ID, notification, 0);
        }

        if (args.length > 1 && !args[0].isEmpty()) {
            final String[] fields = args[1].split("\\|", -1);
            final String level = battery(fields[0], fields.length > 1 ? fields[1] : "");
            final Notification notification =
                    build(ALERT_CHANNEL, args[0], "Batterie : " + level);
            notification.flags |= Notification.FLAG_AUTO_CANCEL;
            set(notification, "mTimeout", ALERT_LIFE_MS);
            call("enqueueNotificationWithTag", PACKAGE, PACKAGE, TAG, ALERT_ID, notification, 0);
            tellEden(args[0], level);
        }
        System.exit(0);
    }

    // One line Eden polls for: when, the alert's title and the battery
    private static void tellEden(String title, String level) {
        final File folder = new File(EDEN_FILES);
        if (!folder.isDirectory()) {
            return;
        }
        final File temporary = new File(folder, "pads.tmp");
        final String line = System.currentTimeMillis() + "\t" + title + "\t" + level + "\n";
        try (FileOutputStream out = new FileOutputStream(temporary)) {
            out.write(line.getBytes(StandardCharsets.UTF_8));
        } catch (IOException e) {
            return;
        }
        temporary.renameTo(new File(folder, "pads.txt"));
    }

    private static String battery(String text, String address) {
        if (!text.isEmpty() || address.isEmpty()) {
            return text;
        }
        try {
            final Object device =
                    BluetoothAdapter.getDefaultAdapter().getRemoteDevice(address.toUpperCase());
            final int level = (Integer) device.getClass().getMethod("getBatteryLevel").invoke(device);
            return level >= 0 ? level + " %" : "inconnue";
        } catch (ReflectiveOperationException | RuntimeException e) {
            return "inconnue";
        }
    }

    private static Notification build(String channel, String title, String text) throws Exception {
        final Notification notification = new Notification();
        notification.when = System.currentTimeMillis();
        notification.icon = android.R.drawable.stat_sys_data_bluetooth;
        notification.visibility = Notification.VISIBILITY_PUBLIC;
        notification.category = Notification.CATEGORY_STATUS;
        notification.extras.putCharSequence(Notification.EXTRA_TITLE, title);
        notification.extras.putCharSequence(Notification.EXTRA_TEXT, text);
        notification.extras.putBoolean(Notification.EXTRA_SHOW_WHEN, false);
        set(notification, "mChannelId", channel);
        set(notification, "mSmallIcon",
                Icon.createWithResource("android", android.R.drawable.stat_sys_data_bluetooth));
        return notification;
    }

    private static void set(Object target, String name, Object value) throws Exception {
        final Field field = target.getClass().getDeclaredField(name);
        field.setAccessible(true);
        field.set(target, value);
    }

    private static void call(String name, Object... args) throws Exception {
        for (final Method method : manager.getClass().getMethods()) {
            if (method.getName().equals(name) && method.getParameterCount() == args.length) {
                method.invoke(manager, args);
                return;
            }
        }
        throw new NoSuchMethodException(name);
    }
}
