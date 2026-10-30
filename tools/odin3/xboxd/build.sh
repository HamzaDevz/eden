#!/bin/sh
# Builds the Xbox Wireless Adapter daemon and PadOff's dex and puts them on the console, with
# odin3-xbox.sh in Download: Hamza runs that one from Odin settings, Advanced, "Run script as Root".
# That runs each line on its own and fails lines longer than about 255 characters.
set -e
cd "$(dirname "$0")"
SDK=${ANDROID_HOME:-$HOME/Library/Android/sdk}
NDK=${ANDROID_NDK:-$SDK/ndk/28.2.13676358}/toolchains/llvm/prebuilt/darwin-x86_64/bin
OUT=$(mktemp -d)
"$NDK/clang++" --target=aarch64-linux-android33 -std=c++20 -O2 -Wall -Wno-unused-result \
    -static-libstdc++ -pthread -s \
    xboxd.cpp dongle.cpp mt76.cpp gip.cpp usb.cpp controller.cpp pads.cpp -o "$OUT/xboxd"
# d8 runs on the java in PATH
PATH=$(/usr/libexec/java_home -v 17+)/bin:$PATH
javac --release 11 -cp "$SDK/platforms/android-35/android.jar" -d "$OUT" PadOff.java
"$SDK/build-tools/36.1.0/d8" --min-api 33 --output "$OUT" "$OUT/PadOff.class"
adb shell mkdir -p /data/local/tmp/odin3-xbox
adb push "$OUT/xboxd" keep.sh /data/local/tmp/odin3-xbox/
adb push "$OUT/classes.dex" /data/local/tmp/odin3-xbox/padoff.dex
adb push odin3-xbox.sh /sdcard/Download/odin3-xbox.sh
rm -r "$OUT"
