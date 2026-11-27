#!/bin/zsh
# meas.sh <outdir> <secs> : TV recording + SurfaceFlinger + poll.sh on the running Smash, then regress.py analyze + stats.py
source ${0:A:h}/env.sh
OUT=$1; N=$2; mkdir -p $OUT
L=$(adb shell "dumpsys SurfaceFlinger --list" | tr -d '\r' | grep -oE 'SurfaceView\[[^]]*EmulationActivity\]\(BLAST\)#[0-9]+' | head -1)
adb push $T/poll.sh /data/local/tmp/poll.sh >/dev/null
adb shell "screenrecord --display-id $TV --time-limit $((N + 1)) --size 1280x720 --bit-rate 8000000 /data/local/tmp/smash.mp4" &
adb shell "sh /data/local/tmp/poll.sh $N > /data/local/tmp/poll.csv" &
t1=$SECONDS
while [ $((SECONDS - t1)) -lt $N ]; do echo @; adb shell "dumpsys SurfaceFlinger --latency '$L'"; sleep 1; done > $OUT/sf.txt
wait
adb pull /data/local/tmp/poll.csv $OUT/poll.csv >/dev/null
adb pull /data/local/tmp/smash.mp4 $OUT/ >/dev/null && adb shell rm /data/local/tmp/smash.mp4
ffmpeg -v error -y -i $OUT/smash.mp4 -c copy -bsf:v h264_mp4toannexb -f h264 $OUT/tv.h264 && rm $OUT/smash.mp4
adb logcat -d | grep -E 'Fatal signal|PANIC|Assertion|rejected|Out of memory|Driver:|GPU:' | tail -50 > $OUT/logcat.txt
printf '{"title": "01006A800016E000", "name": "Smash Ultimate", "secs": %d, "alive": 1, "save": "bench", "build": "live", "mode": "replay"}\n' $N > $OUT/meta.json
[ -n "$NOANALYZE" ] && exit 0
python3 -I $T/regress.py analyze $OUT | sed 's/^/  /'
python3 -I ${0:A:h}/stats.py $OUT
