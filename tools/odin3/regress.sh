#!/bin/zsh
# SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
# SPDX-License-Identifier: GPL-3.0-or-later

# regress.sh <run> [TITLE ...] : non-regression pass of the installed Eden build over every game of regress.txt (or
# the titles given): boot, menu presses, REC s of input while the TV is recorded and SurfaceFlinger sampled, then
# regress.py analyze into runs/regress/<run>/<TITLE>/. Compare builds with
# `regress.py cmp runs/regress/<old> runs/regress/<new>`, then look at every sheet.png.
#   regress.sh --bench-save <TITLE> [source folder name] : copies the player's save (or that folder of the same
#   save directory) to <TITLE>.bench, the fixed start point of every run; a game without one boots with no save.
# The player's save is never opened by a run: Eden stopped, its folder renamed to <TITLE>.regress-keep, a copy of the
# .bench folder (or nothing) put in its place, and after the run that copy is deleted and the folder renamed back.
# The save listing must come back identical, otherwise the pass stops.
#   PKG (default our relWithDebInfo build), REC (default 40 s)
A=~/Library/Android/sdk/platform-tools/adb
D=${0:A:h}
PKG=${PKG:-com.miHoYo.Yuanshen.relWithDebInfo}; REC=${REC:-40}
F=/storage/emulated/0/Android/data/$PKG/files
N=$($A shell cat $F/config/config.ini | tr -d '\r' | sed -n 's/^nand_directory=//p')
P=${N:-$F/nand}/user/save/0000000000000000/F87F57BFCE6345060247FB61C12F5AE7
ROMS='content://com.android.externalstorage.documents/tree/FBDD-1F17%3AEmulations%2FROMs%2Fswitch/document/FBDD-1F17%3AEmulations%2FROMs%2Fswitch%2F'

if [ "$1" = --bench-save ]; then
  S=$P/$2
  $A shell am force-stop $PKG
  if $A shell "rm -rf $S.bench; cp -r $P/${3:-$2} $S.bench && chmod -R 777 $S.bench"; then
    echo "$S.bench made"
  else
    $A shell rm -rf $S.bench; echo "save not readable by adb: $2 will boot without one"
  fi
  exit
fi

RUN=$D/runs/regress/$1; shift
mkdir -p $RUN || exit 1
if $A shell dumpsys activity activities | grep -q 'topResumedActivity.*EmulationActivity'; then
  echo "a game is open on the console: not now"; exit 2
fi

CUR=
prepare() {  # Eden stopped, player's save renamed away, bench copy in place
  local S=$P/$1
  $A shell am force-stop $PKG
  if $A shell "[ -e $S.regress-keep ] || [ -e $S.regress-nosave ]"; then
    echo "$1: leftover of an unfinished pass, restore $S.regress-keep by hand"; exit 3
  fi
  $A shell "ls -lnR $S 2>&1" > $OUT/save_before.txt
  $A shell "if [ -e $S ]; then mv $S $S.regress-keep; else touch $S.regress-nosave; fi" || exit 3
  CUR=$1
  if $A shell "[ -d $S.bench ]"; then
    $A shell "cp -r $S.bench $S && chmod -R 777 $S" || { restore $1; echo "$1: bench copy failed"; exit 3; }
    SAVE=bench
  else
    SAVE=none
  fi
}
restore() {  # Eden stopped, run copy deleted, player's save renamed back, listing checked
  local S=$P/$1
  $A shell am force-stop $PKG
  $A shell "if [ -e $S.regress-keep ]; then rm -rf $S && mv $S.regress-keep $S; elif [ -e $S.regress-nosave ]; then rm -rf $S && rm $S.regress-nosave; fi"
  CUR=
  $A shell "ls -lnR $S 2>&1" > $OUT/save_after.txt
  if ! cmp -s $OUT/save_before.txt $OUT/save_after.txt; then
    echo "$1: SAVE LISTING CHANGED, pass stopped"; diff $OUT/save_before.txt $OUT/save_after.txt | head; exit 3
  fi
}
trap '[ -n "$CUR" ] && restore $CUR; exit 4' INT TERM

DISP=$($A shell dumpsys SurfaceFlinger --display-id | tr -d '\r' | grep -v 'pnpId=QCM' | sed -n 's/^Display \([0-9]*\).*/\1/p' | head -1)
BUILD="$($A shell dumpsys package $PKG | tr -d '\r' | sed -n 's/^ *versionName=//p' | head -1) $(git -C $D rev-parse --short HEAD)"
# adb reads stdin, so the list is loaded first rather than piped into the loop
for G in "${(@f)$(grep -v '^#' $D/regress.txt)}"; do
  IFS='|' read T ROM WAIT PRESSES MODE <<< "$G"
  [ -z "$T" ] && continue
  [ $# -gt 0 ] && [ ${@[(Ie)$T]} -eq 0 ] && continue
  OUT=$RUN/$T; rm -rf $OUT; mkdir -p $OUT
  prepare $T
  U=$ROMS$(python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$ROM")
  $A logcat -c
  $A shell am start -a android.intent.action.VIEW -t application/octet-stream -d "\"$U\"" \
    -n $PKG/org.yuzu.yuzu_emu.activities.EmulationActivity >/dev/null
  $A forward tcp:6560 tcp:6560 >/dev/null
  sleep $WAIT
  for p in ${(s: :)PRESSES}; do python3 $D/regress_input.py press ${p%@*} ${p#*@}; done
  L=$($A shell "dumpsys SurfaceFlinger --list" | tr -d '\r' | grep -oE 'SurfaceView\[[^]]*EmulationActivity\]\(BLAST\)#[0-9]+' | head -1)
  $A shell "screenrecord ${DISP:+--display-id $DISP} --time-limit $((REC + 2)) --size 1920x1080 --bit-rate 20000000 /data/local/tmp/regress.mp4" &
  python3 $D/regress_input.py $MODE $REC &
  t0=$SECONDS
  while [ $((SECONDS - t0)) -lt $REC ]; do echo @; $A shell "dumpsys SurfaceFlinger --latency '$L'"; sleep 1; done > $OUT/sf.txt
  wait
  ALIVE=$($A shell dumpsys activity activities | grep -c 'topResumedActivity.*EmulationActivity')
  $A logcat -d | grep -E 'Fatal signal|PANIC|Assertion|rejected|Out of memory|Driver:|GPU:' | tail -50 > $OUT/logcat.txt
  $A pull /data/local/tmp/regress.mp4 $OUT/ >/dev/null && $A shell rm /data/local/tmp/regress.mp4
  ffmpeg -v error -y -i $OUT/regress.mp4 -c copy -bsf:v h264_mp4toannexb -f h264 $OUT/tv.h264 && rm $OUT/regress.mp4
  restore $T
  printf '{"title": "%s", "name": "%s", "secs": %s, "alive": %s, "save": "%s", "build": "%s", "mode": "%s"}\n' \
    $T "${ROM%%[\[.]*}" $REC $ALIVE $SAVE "$BUILD" $MODE > $OUT/meta.json
  python3 $D/regress.py analyze $OUT | tee -a $RUN/summary.txt
done
$A shell am start -n $PKG/org.yuzu.yuzu_emu.ui.main.MainActivity >/dev/null
