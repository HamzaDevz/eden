#!/bin/sh
# Builds the Xbox Wireless Adapter daemon and puts it on the console, with odin3-xbox.sh in
# Download: Hamza runs that one from Odin settings, Advanced, "Run script as Root".
set -e
cd "$(dirname "$0")"
NDK=${ANDROID_NDK:-$HOME/Library/Android/sdk/ndk/28.2.13676358}/toolchains/llvm/prebuilt/darwin-x86_64/bin
OUT=$(mktemp -d)
"$NDK/clang++" --target=aarch64-linux-android33 -std=c++20 -O2 -Wall -Wno-unused-result \
    -static-libstdc++ -pthread -s \
    xboxd.cpp dongle.cpp mt76.cpp gip.cpp usb.cpp controller.cpp -o "$OUT/xboxd"
adb shell mkdir -p /data/local/tmp/odin3-xbox
adb push "$OUT/xboxd" keep.sh /data/local/tmp/odin3-xbox/
adb push odin3-xbox.sh /sdcard/Download/odin3-xbox.sh
rm -r "$OUT"
