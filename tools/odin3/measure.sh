#!/bin/zsh
# SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
# SPDX-License-Identifier: GPL-3.0-or-later

# measure.sh <outdir> [seconds] [play|scenario|walk|none]: records metrics on the running Eden session while
# play.py plays the game (default) or the route in $SCENARIO (until it ends, at most <seconds>), walk.sh walks
# Link back and forth, or nobody touches the pad.
A=~/Library/Android/sdk/platform-tools/adb
D=${0:A:h}
OUT=$1; DUR=${2:-60}; MODE=${3:-play}
mkdir -p $OUT
$A push $D/poll.sh $D/pad.sh $D/walk.sh /data/local/tmp/ >/dev/null
PID=$($A shell pidof ${PKG:-com.miHoYo.Yuanshen} | tr -d '\r')
L=$($A shell "dumpsys SurfaceFlinger --list" | tr -d '\r' | grep -oE 'SurfaceView\[[^]]*EmulationActivity\]\(BLAST\)#[0-9]+' | head -1)
echo "pid=$PID layer=$L start=$(date +%T)" > $OUT/context.txt

[ $MODE = walk ] && { $A shell sh /data/local/tmp/walk.sh $DUR > /dev/null & }
[ $MODE = play -o $MODE = scenario ] && { python3 -I $D/play.py $DUR $OUT > /dev/null & BOT=$! }
$A shell sh /data/local/tmp/poll.sh $DUR > $OUT/poll.csv &
$A shell "cat /proc/$PID/task/*/stat" > $OUT/threads_start.txt
$A shell "cat /proc/stat; cat /proc/[0-9]*/stat 2>/dev/null" > $OUT/procs_start.txt

t0=$SECONDS
while [ $((SECONDS - t0)) -lt $DUR ] && { [ $MODE != scenario ] || kill -0 $BOT 2>/dev/null; }; do
  { echo "@ $((SECONDS - t0))"; $A shell "dumpsys SurfaceFlinger --latency '$L'"; } >> $OUT/sf_latency.txt
  # SF keeps only the last 128 presents (~2.1 s at 60 fps), so sample faster than that.
  sleep 1
done
[ $MODE = scenario ] && $A shell pkill -f poll.sh
$A shell "cat /proc/$PID/task/*/stat" > $OUT/threads_end.txt
$A shell "cat /proc/stat; cat /proc/[0-9]*/stat 2>/dev/null" > $OUT/procs_end.txt
wait
python3 -I $D/summarize.py $OUT
