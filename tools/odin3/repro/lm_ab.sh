#!/bin/zsh
# lm_ab.sh RUN NAME[:Section.key=value,...] ... : Luigi's Mansion 2 HD A/B of per-game INI variants on the TV. Each
# variant is Hamza's INI plus its edits; the game boots by URI on a copy of his save (his folder renamed away), A is
# mashed through the intro into mission A-1, then MEAS s of roam are measured (TV capture, SurfaceFlinger, poll.sh).
# His save and INI are put back at the end and checked. INTRO (s of A after the title, default 200), SHOTS=1 (TV
# screenshots every 15 s of the intro, to time it), MEAS (default 90).
export PATH=$HOME/Library/Android/sdk/platform-tools:$PATH
A=adb; D=${0:A:h}; T=${D:h}
PKG=com.miHoYo.Yuanshen.relWithDebInfo; TID=010048701995E000
F=/storage/emulated/0/Android/data/$PKG/files
I=$F/config/custom/$TID.ini
S=$F/nand/user/save/0000000000000000/F87F57BFCE6345060247FB61C12F5AE7/$TID
U='content://com.android.externalstorage.documents/tree/FBDD-1F17%3AEmulations%2FROMs%2Fswitch/document/FBDD-1F17%3AEmulations%2FROMs%2Fswitch%2F'$(python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "Luigi's Mansion 2 HD [$TID][v0].nsp")
INTRO=${INTRO:-200}; MEAS=${MEAS:-90}
W=$T/runs/lm/$1; mkdir -p $W
$A shell dumpsys activity activities | grep -q 'topResumedActivity.*EmulationActivity' && { echo "game open, not now"; exit 2; }
$A shell am force-stop $PKG
[ -s $W/orig.ini ] || $A pull $I $W/orig.ini >/dev/null || exit 3
O=$(md5 -q $W/orig.ini)
$A shell "[ -d $S ] && [ ! -e $S.lm-keep ]" || { echo "save state unexpected, abort"; exit 3; }
$A shell "ls -lnR $S 2>&1" > $W/save_before.txt
$A shell "mv $S $S.lm-keep" || exit 3
back() {
  $A shell am force-stop $PKG
  $A shell "rm -rf $S 2>/dev/null; if [ -e $S ]; then mv $S $S.lm-trash-$(date +%s); fi; mv $S.lm-keep $S"
  $A shell "ls -lnR $S 2>&1" > $W/save_after.txt
  cmp -s $W/save_before.txt $W/save_after.txt && echo "save restored identical" || { echo "SAVE LISTING CHANGED"; diff $W/save_before.txt $W/save_after.txt | head; }
  $A push $W/orig.ini $I >/dev/null
  [ "$($A shell md5sum $I | cut -c1-32)" = $O ] && echo "ini restored identical" || echo "INI NOT RESTORED"
  $A shell am start -n $PKG/org.yuzu.yuzu_emu.ui.main.MainActivity >/dev/null
}
trap 'back; exit 4' INT TERM
$A push $T/poll.sh /data/local/tmp/poll.sh >/dev/null
DISP=$($A shell dumpsys SurfaceFlinger --display-id | tr -d '\r' | grep -v 'pnpId=QCM' | sed -n 's/^Display \([0-9]*\).*/\1/p' | head -1)
shot() { $A exec-out screencap -p ${DISP:+-d $DISP} > $1.png && sips -Z 480 $1.png --out $1.png >/dev/null; }
n=0
for v in ${@:2}; do
  n=$((n + 1)); name=${v%%:*}; edits=(); [[ $v == *:* ]] && edits=(${(s:,:)${v#*:}})
  OUT=$W/$n-$name; rm -rf $OUT; mkdir -p $OUT
  if [ -n "$edits" ]; then python3 $T/setini.py $W/orig.ini $OUT/game.ini $edits || break; else cp $W/orig.ini $OUT/game.ini; fi
  $A shell am force-stop $PKG; $A push $OUT/game.ini $I >/dev/null
  $A shell "rm -rf $S 2>/dev/null; if [ -e $S ]; then mv $S $S.lm-trash-$(date +%s); fi; cp -r $S.lm-keep $S && chmod -R 777 $S" || break
  for try in 1 2; do  # a boot crash (seen once: heap corruption in EmulatedController::LoadDevices at HID init) is counted and retried once
    $A logcat -c
    $A shell am start -a android.intent.action.VIEW -t application/octet-stream -d "\"$U\"" -n $PKG/org.yuzu.yuzu_emu.activities.EmulationActivity >/dev/null
    $A forward tcp:6560 tcp:6560 >/dev/null
    sleep 20
    $A logcat -d | grep -q 'Fatal signal' || break
    echo "BOOT CRASH try $try"; $A logcat -d | grep -E 'Fatal signal' | head -2 > $OUT/bootcrash-$try.txt; $A shell am force-stop $PKG
  done
  sleep 40
  $A logcat -d | grep -E 'Driver:|GPU:|LogSettings' > $OUT/boot_logcat.txt
  for w in 3 3 3; do python3 $T/regress_input.py press 1 $w; done
  t0=$SECONDS; k=0
  while [ $((SECONDS - t0)) -lt $INTRO ]; do
    python3 $T/regress_input.py press 1 1.0
    if [ -n "$SHOTS" ] && [ $((SECONDS - t0)) -ge $((k * 15)) ]; then shot $OUT/intro-$(printf %03d $((SECONDS - t0))); k=$((k + 1)); fi
  done
  L=$($A shell "dumpsys SurfaceFlinger --list" | tr -d '\r' | grep -oE 'SurfaceView\[[^]]*EmulationActivity\]\(BLAST\)#[0-9]+' | head -1)
  $A shell "screenrecord ${DISP:+--display-id $DISP} --time-limit $((MEAS + 2)) --size 1920x1080 --bit-rate 20000000 /data/local/tmp/lm.mp4" &
  $A shell "sh /data/local/tmp/poll.sh $MEAS > /data/local/tmp/lm_poll.csv" &
  python3 $T/regress_input.py roam $MEAS &
  t1=$SECONDS
  while [ $((SECONDS - t1)) -lt $MEAS ]; do echo @; $A shell "dumpsys SurfaceFlinger --latency '$L'"; sleep 1; done > $OUT/sf.txt
  wait
  ALIVE=$($A shell dumpsys activity activities | grep -c 'topResumedActivity.*EmulationActivity')
  $A logcat -d | grep -E 'Fatal signal|PANIC|Assertion|rejected|Out of memory|Driver:|GPU:' | tail -50 > $OUT/logcat.txt
  [ -n "$SHOTS" ] && shot $OUT/end
  $A shell am force-stop $PKG
  $A pull /data/local/tmp/lm_poll.csv $OUT/poll.csv >/dev/null
  $A pull /data/local/tmp/lm.mp4 $OUT/ >/dev/null && $A shell rm /data/local/tmp/lm.mp4
  ffmpeg -v error -y -i $OUT/lm.mp4 -c copy -bsf:v h264_mp4toannexb -f h264 $OUT/tv.h264 && rm $OUT/lm.mp4
  printf '{"title": "%s", "name": "%s", "secs": %s, "alive": %s, "save": "copy", "build": "live", "mode": "roam"}\n' $TID $name $MEAS $ALIVE > $OUT/meta.json
  python3 $T/regress.py analyze $OUT
  python3 -c "import csv,sys; r=list(csv.DictReader(open(sys.argv[1]))); m=lambda k: sum(float(x[k]) for x in r)/len(r)
print(f'gpu_busy={m(\"gpu_busy\"):.0f}% gpu_mhz={m(\"gpu_mhz\"):.0f} cpu_tmax={max(float(x[\"cpu_tmax\"]) for x in r)/1000:.0f}C gpu_tmax={max(float(x[\"gpu_tmax\"]) for x in r)/1000:.0f}C')" $OUT/poll.csv
  grep -h -m1 -o 'Report: Driver: .*' $OUT/boot_logcat.txt | head -1
  echo "== $n-$name ${edits[*]}"
done
back
