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
#   SCENARIO    play scenarios/<SCENARIO>.txt from the save it names instead (sets MODE; DUR is then a limit)
#   DISMISS     press A this many times once the save is loaded, to close a popup the route does not expect
#   SAVE        restore saves/<SAVE>/ before launching (see saves.sh)
#   BOOT_WAIT   seconds between the ROM loading and the first press (default 18; more with a new driver)
#   TRACE=1     record an atrace of the measurement (sched, freq, gfx and Eden's sections) and run trace.py on it
#   ADDONS      add-ons (mods, update) to switch on for the game, "name;name" as Eden lists them; every other
#               run gets the player's own list back from base/<PKG>/config.ini
#   GLOBAL      "Section.key=value ..." for the global config, for the settings a game cannot override
A=~/Library/Android/sdk/platform-tools/adb
D=${0:A:h}
NAME=$1; shift
OUT=$D/runs/$NAME
export PKG=${PKG:-com.miHoYo.Yuanshen}
TITLE=${TITLE:-01008CF01BAAC000}
F=/storage/emulated/0/Android/data/$PKG/files
mkdir -p $OUT $D/base/$PKG || exit 1
if [ -n "$SCENARIO" ]; then
  export SCENARIO=$D/scenarios/$SCENARIO.txt MODE=scenario
  SAVE=$(sed -n 's/^save \([^ #]*\).*/\1/p' $SCENARIO)
fi
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

# Every Eden build, not only PKG: another one left in front takes the focus and the pad input.
for p in $($A shell pm list packages com.miHoYo.Yuanshen | tr -d '\r' | sed 's/^package://'); do $A shell am force-stop $p; done
[ -z "$SAVE" ] || PKG=$PKG TITLE=$TITLE $D/saves.sh push $SAVE || { echo "ABORT: save $SAVE" | tee $OUT/summary.txt; exit 1; }
# A killed Eden leaves the kgsl turbo state behind; start every run from normal DCVS.
$A shell /data/local/tmp/kgslturbo 0
for i in $(seq 45); do [ $($A shell cat /sys/class/kgsl/kgsl-3d0/temp | tr -d '\r') -lt 42000 ] && break; sleep 2; done
[ -f $D/base/$PKG/$TITLE.ini ] || $A pull $F/config/custom/$TITLE.ini $D/base/$PKG/$TITLE.ini >/dev/null || exit 1
python3 -I $D/setini.py $D/base/$PKG/$TITLE.ini $OUT/game.ini "$@" || { echo "ABORT: ini"; exit 1; }
$A push $OUT/game.ini $F/config/custom/$TITLE.ini >/dev/null
[ -f $D/base/$PKG/config.ini ] || $A pull $F/config/config.ini $D/base/$PKG/config.ini >/dev/null || exit 1
python3 -I $D/addons.py $D/base/$PKG/config.ini $OUT/config.ini $TITLE "$ADDONS" ||
  { echo "ABORT: addons" | tee $OUT/summary.txt; exit 1; }
[ -z "$GLOBAL" ] || python3 -I $D/setini.py --global $OUT/config.ini $OUT/config.ini ${=GLOBAL} ||
  { echo "ABORT: global ini" | tee $OUT/summary.txt; exit 1; }
$A push $OUT/config.ini $F/config/config.ini >/dev/null
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
sleep ${BOOT_WAIT:-18}
# What Eden loaded (driver, patches, settings): the log buffer turns over before the run ends.
$A logcat -d -s YuzuNative > $OUT/logcat.txt && $A logcat -c
# Zelda EoW: title screen, then continue the last save.
pad btn a 150; sleep 5
pad btn a 150; sleep 5
pad btn a 150; sleep 4
pad btn up 150; sleep 1
pad btn a 150; sleep 30
# A popup a game version shows over the save (Zelda 1.1.0: the Favorites tutorial, 2 pages).
for i in $(seq ${DISMISS:-0}); do pad btn a 150; sleep 2; done
shot ingame
# A load that went wrong leaves a black playfield: the bot would idle and the numbers mean nothing.
python3 -I -c "import sys; from PIL import Image, ImageStat
sys.exit(ImageStat.Stat(Image.open(sys.argv[1]).convert('L').crop((0, 100, 900, 506))).mean[0] < 5)" $OUT/ingame.png ||
  { echo "ABORT: black screen after loading the save" | tee $OUT/summary.txt; exit 1; }
[ "$TURBO" = 1 ] && $A shell /data/local/tmp/kgslturbo 1
# Only once in game: tracing slows the boot enough for the pad presses above to land in the wrong menus. The app
# notices the tags changing on its own.
[ "$TRACE" = 1 ] && $A shell atrace --async_start -c -b 65536 -a $PKG sched freq >/dev/null
$D/measure.sh $OUT ${DUR:-$([ -n "$SCENARIO" ] && echo 900 || echo 90)} ${MODE:-play} | tee $OUT/summary.txt
if [ "$TRACE" = 1 ]; then
  $A shell atrace --async_stop -o /data/local/tmp/atrace.txt >/dev/null
  $A pull /data/local/tmp/atrace.txt $OUT/ >/dev/null && $A shell rm /data/local/tmp/atrace.txt
  python3 -I $D/trace.py $OUT | tee -a $OUT/summary.txt
fi
shot end
$A logcat -d -s YuzuNative >> $OUT/logcat.txt
# A driver_path the game's own ini overrides is ignored without a word: record the driver that ran.
grep -o 'Driver: .*' $OUT/logcat.txt | tail -1 | tee -a $OUT/summary.txt
$A shell /data/local/tmp/kgslturbo 0
[ -f $OUT/stream.h264 ] && python3 -I $D/glitch.py $OUT | tee -a $OUT/summary.txt
