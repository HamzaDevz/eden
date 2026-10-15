#!/bin/zsh

# SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
# SPDX-License-Identifier: GPL-3.0-or-later

# run_variant.sh <name> [Section.key=value ...]: relaunch a game with these per-game overrides and
# measure DUR s (default 90) of gameplay driven by play.py into runs/<name>/.
#   PKG         Eden package (default: the official "Optimized" build)
#   TITLE       title ID (default: Zelda Echoes of Wisdom); base/<PKG>/<TITLE>.ini is pulled from the device once
#   LAUNCH_URI  boot a ROM from the package's own DocumentProvider instead of its library
#   TURBO=1     pin the GPU clock during the measurement
#   MODE        play (default), walk or none: see measure.sh
A=~/Library/Android/sdk/platform-tools/adb
D=${0:A:h}
NAME=$1; shift
OUT=$D/runs/$NAME
export PKG=${PKG:-com.miHoYo.Yuanshen}
TITLE=${TITLE:-01008CF01BAAC000}
F=/storage/emulated/0/Android/data/$PKG/files
mkdir -p $OUT $D/base/$PKG || exit 1
# Never send input unless Eden's emulation window has focus (a locked screen once sent it to Settings).
focused() { $A shell dumpsys window | grep mCurrentFocus | grep -q "$PKG/org.yuzu.yuzu_emu.activities.EmulationActivity"; }
pad() { focused || { echo "ABORT: Eden not focused" | tee $OUT/summary.txt; $A shell input keyevent KEYCODE_HOME; exit 1; }; $A shell sh /data/local/tmp/pad.sh "$@"; }
unlock() { $A shell input keyevent KEYCODE_WAKEUP; sleep 1; $A shell wm dismiss-keyguard; sleep 1; }
shot() { $A exec-out screencap -p > $OUT/$1.png; sips -Z 900 $OUT/$1.png --out $OUT/$1.png >/dev/null; }

if [ ! -f $D/kgslturbo ]; then
  ~/Library/Android/sdk/ndk/*/toolchains/llvm/prebuilt/darwin-x86_64/bin/aarch64-linux-android30-clang \
    -O2 -o $D/kgslturbo $D/kgslturbo.c || exit 1
fi
$A push $D/kgslturbo $D/poll.sh $D/pad.sh $D/walk.sh /data/local/tmp/ >/dev/null
$A shell chmod 755 /data/local/tmp/kgslturbo

$A shell am force-stop $PKG
# A killed Eden leaves the kgsl turbo state behind; start every run from normal DCVS.
$A shell /data/local/tmp/kgslturbo 0
for i in $(seq 45); do [ $($A shell cat /sys/class/kgsl/kgsl-3d0/temp | tr -d '\r') -lt 42000 ] && break; sleep 2; done
[ -f $D/base/$PKG/$TITLE.ini ] || $A pull $F/config/custom/$TITLE.ini $D/base/$PKG/$TITLE.ini >/dev/null || exit 1
python3 -I $D/setini.py $D/base/$PKG/$TITLE.ini $OUT/game.ini "$@" || { echo "ABORT: ini"; exit 1; }
$A push $OUT/game.ini $F/config/custom/$TITLE.ini >/dev/null
unlock
$A logcat -c
if [ -n "$LAUNCH_URI" ]; then
  # Test builds have no library: boot the ROM straight from their own DocumentProvider.
  $A shell am start -a android.intent.action.VIEW -t application/octet-stream -d "$LAUNCH_URI" \
    -n $PKG/org.yuzu.yuzu_emu.activities.EmulationActivity >/dev/null
else
  $A shell am start -a dev.eden.eden_emulator.LAUNCH_WITH_CUSTOM_CONFIG \
    -n $PKG/org.yuzu.yuzu_emu.activities.EmulationActivity --es title_id $TITLE >/dev/null
  for i in $(seq 30); do $A logcat -d -s YuzuNative | grep -q "Found game" && break; sleep 1; done
  sleep 2
  $A shell dumpsys window | grep mCurrentFocus | grep -q "$PKG/" || { echo "ABORT: no dialog" | tee $OUT/summary.txt; exit 1; }
  # Confirm button of the custom-config dialog, landscape 1920x1080.
  $A shell input tap 1395 672
  $A logcat -c
fi
for i in $(seq 60); do $A logcat -d -s YuzuNative | grep -q "Load: Loading" && break; sleep 1; done
sleep 18
# Zelda EoW: title screen, then continue the last save.
pad btn a 150; sleep 5
pad btn a 150; sleep 5
pad btn a 150; sleep 4
pad btn up 150; sleep 1
pad btn a 150; sleep 30
shot ingame
# A load that went wrong leaves a black playfield: the bot would idle and the numbers mean nothing.
python3 -I -c "import sys; from PIL import Image, ImageStat
sys.exit(ImageStat.Stat(Image.open(sys.argv[1]).convert('L').crop((0, 100, 900, 506))).mean[0] < 5)" $OUT/ingame.png ||
  { echo "ABORT: black screen after loading the save" | tee $OUT/summary.txt; exit 1; }
[ "$TURBO" = 1 ] && $A shell /data/local/tmp/kgslturbo 1
$D/measure.sh $OUT ${DUR:-90} ${MODE:-play} | tee $OUT/summary.txt
shot end
$A shell /data/local/tmp/kgslturbo 0
