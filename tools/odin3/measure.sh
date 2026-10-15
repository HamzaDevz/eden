#!/bin/zsh
# SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
# SPDX-License-Identifier: GPL-3.0-or-later

# measure.sh <outdir> [seconds] [walk]: records metrics on the running Eden session.
A=~/Library/Android/sdk/platform-tools/adb
D=${0:A:h}
OUT=$1; DUR=${2:-60}; WALK=${3:-walk}
mkdir -p $OUT
$A push $D/poll.sh $D/pad.sh $D/walk.sh /data/local/tmp/ >/dev/null
PID=$($A shell pidof ${PKG:-com.miHoYo.Yuanshen} | tr -d '\r')
L=$($A shell "dumpsys SurfaceFlinger --list" | tr -d '\r' | grep -oE 'SurfaceView\[[^]]*EmulationActivity\]\(BLAST\)#[0-9]+' | head -1)
echo "pid=$PID layer=$L start=$(date +%T)" > $OUT/context.txt

[ $WALK = walk ] && { $A shell sh /data/local/tmp/walk.sh $DUR > /dev/null & }
$A shell sh /data/local/tmp/poll.sh $DUR > $OUT/poll.csv &
POLL=$!
$A shell "cat /proc/$PID/task/*/stat" > $OUT/threads_start.txt

s=0
while [ $s -lt $DUR ]; do
  { echo "@ $s"; $A shell "dumpsys SurfaceFlinger --latency '$L'"; } >> $OUT/sf_latency.txt
  # SF keeps only the last 128 presents (~2.1 s at 60 fps), so sample faster than that.
  sleep 1
  s=$((s+2))
done
$A shell "cat /proc/$PID/task/*/stat" > $OUT/threads_end.txt
wait $POLL
python3 -I $D/summarize.py $OUT
