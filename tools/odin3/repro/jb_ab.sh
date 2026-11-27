#!/bin/zsh
# jb_ab.sh RUN NAME[:Section.key=value,...] ... : Super Mario Party Jamboree A/B of per-game INI variants on the TV, run
# under the console lock (lockf -k ~/.odin3-console.lock). Needs a test save beside Hamza's, <save>.jbbench: a party
# on the Mega Wiggler board with CPU players and dynamic minigames off, saved in slot 1. Hamza's save is moved aside
# for the batch and its listing compared at the end. Each variant: Hamza's INI + edits pushed, game launched by URI,
# route to the party (intro skip, L+R, 1 player, Mario, balloon, Mario Party, CPU, slot 1, resume; jb_scr.py
# recovers a missed step), then REC s of autoplay (board turns + minigames) with TV capture, SurfaceFlinger and
# poll.sh. COMMIT=<apk commit> also writes each run to results/<TITLEID>.csv (results.py).
export PATH=$HOME/Library/Android/sdk/platform-tools:$PATH ANDROID_SERIAL=192.168.1.13:5555
A=adb; D=${0:A:h}; T=${D:h}; W=$T/runs/jamboree; mkdir -p $W
PKG=com.miHoYo.Yuanshen.relWithDebInfo; REC=${REC:-150}
I=/storage/emulated/0/Android/data/$PKG/files/config/custom/0100965017338000.ini
IW=$W/ini; mkdir -p $IW
[ -s $IW/orig.ini ] || { $A shell am force-stop $PKG; $A pull $I $IW/orig.ini >/dev/null || exit 3; }
O=$(md5 -q $IW/orig.ini)
U='content://com.android.externalstorage.documents/tree/FBDD-1F17%3AEmulations%2FROMs%2Fswitch/document/FBDD-1F17%3AEmulations%2FROMs%2Fswitch%2FSuper%20Mario%20Party%20Jamboree%20v1.0.0%5B0100965017338000%5D%5B0%5D%5BAPP%5D.nsp'
S=/storage/emulated/0/Android/data/$PKG/files/nand/user/save/0000000000000000/F87F57BFCE6345060247FB61C12F5AE7/0100965017338000
$A shell dumpsys activity activities | grep -q 'topResumedActivity.*EmulationActivity' && { echo "game open, not now"; exit 2; }
$A shell "[ -d $S ] && [ -d $S.jbbench ] && [ ! -e $S.regress-keep ]" || { echo "save state unexpected, abort"; exit 3; }
$A shell am force-stop $PKG
$A shell "ls -lnid $S; ls -lnR $S 2>&1" > $IW/save_before.txt
$A shell "mv $S $S.regress-keep" || exit 3; $A shell "mv $S.jbbench $S" || { $A shell "mv $S.regress-keep $S"; exit 3; }
back() {
  $A shell am force-stop $PKG; $A push $IW/orig.ini $I >/dev/null
  [ "$($A shell md5sum $I | cut -c1-32)" = $O ] && echo "ini restored identical" || echo "INI NOT RESTORED"
  $A shell "mv $S $S.jbbench && mv $S.regress-keep $S"
  $A shell "ls -lnid $S; ls -lnR $S 2>&1" > $IW/save_after.txt
  cmp -s $IW/save_before.txt $IW/save_after.txt && echo "save restored identical" || { echo "SAVE LISTING CHANGED"; diff $IW/save_before.txt $IW/save_after.txt; }
  $A shell am start -n $PKG/org.yuzu.yuzu_emu.ui.main.MainActivity >/dev/null
}
trap 'back; exit 4' INT TERM
$A push $T/poll.sh /data/local/tmp/poll.sh >/dev/null
P() { python3 $T/regress_input.py press $1 $2; }
n=0
for v in ${@:2}; do
  n=$((n + 1)); name=${v%%:*}; edits=(); [[ $v == *:* ]] && edits=(${(s:,:)${v#*:}})
  out=$W/runs/$1-$n-$name; rm -rf $out; mkdir -p $out
  if [ -n "$edits" ]; then python3 $T/setini.py $IW/orig.ini $IW/$name.ini $edits || break; else cp $IW/orig.ini $IW/$name.ini; fi
  $A shell am force-stop $PKG; $A push $IW/$name.ini $I >/dev/null
  $A logcat -c
  $A shell am start -a android.intent.action.VIEW -t application/octet-stream -d "\"$U\"" -n $PKG/org.yuzu.yuzu_emu.activities.EmulationActivity >/dev/null
  $A forward tcp:6560 tcp:6560 >/dev/null
  sleep ${BOOT:-45}
  P 400 3; P c0 4; P 1 5; P 1 1.5; P 1 6; P 1 3; P 400 4; P 1 3; P 1 3; P 1 3; P 1 8
  for k in 1 2 3 4; do  # route fell short: map or resume screen still up
    python3 $T/bridge.py shot $out/chk.png 270; st=$(python3 $D/jb_scr.py $out/chk.png); echo "check $k: $st"
    case $st in map) P 1 3; P 1 3; P 1 3; P 1 8;; reprendre) P 1 8;; title) P c0 4; P 1 5; P 1 1.5; P 1 6; P 1 3; P 400 4; P 1 3; P 1 3; P 1 3; P 1 8;; *) break;; esac
  done
  python3 $T/bridge.py shot $out/start.png 270
  DISP=$($A shell dumpsys SurfaceFlinger --display-id | tr -d '\r' | grep -v 'pnpId=QCM' | sed -n 's/^Display \([0-9]*\).*/\1/p' | head -1)
  L=$($A shell "dumpsys SurfaceFlinger --list" | tr -d '\r' | grep -oE 'SurfaceView\[[^]]*EmulationActivity\]\(BLAST\)#[0-9]+' | head -1)
  $A shell "screenrecord ${DISP:+--display-id $DISP} --time-limit $((REC + 2)) --size 1920x1080 --bit-rate 20000000 /data/local/tmp/regress.mp4" &
  $A shell "sh /data/local/tmp/poll.sh $REC > /data/local/tmp/poll.csv" &
  python3 $D/jb_input.py $REC 2>/dev/null &
  t0=$SECONDS
  while [ $((SECONDS - t0)) -lt $REC ]; do echo @; $A shell "dumpsys SurfaceFlinger --latency '$L'"; sleep 1; done > $out/sf.txt
  wait
  python3 $T/bridge.py shot $out/end.png 270
  ALIVE=$($A shell dumpsys activity activities | grep -c 'topResumedActivity.*EmulationActivity')
  { $A logcat -d | grep -m4 -E 'Report: Driver:|GPU:'; $A logcat -d | grep -E 'Fatal signal|PANIC|Assertion|rejected|Out of memory|LogSettings' | tail -60; } > $out/logcat.txt
  $A pull /data/local/tmp/poll.csv $out/poll.csv >/dev/null
  $A pull /data/local/tmp/regress.mp4 $out/ >/dev/null && $A shell rm /data/local/tmp/regress.mp4
  ffmpeg -v error -y -i $out/regress.mp4 -c copy -bsf:v h264_mp4toannexb -f h264 $out/tv.h264 && rm $out/regress.mp4
  printf '{"title": "0100965017338000", "name": "Super Mario Party Jamboree", "secs": %s, "alive": %s, "save": "party", "build": "live", "mode": "party"}\n' $REC $ALIVE > $out/meta.json
  python3 $T/regress.py analyze $out
  python3 -c "import csv,sys; r=list(csv.DictReader(open(sys.argv[1]))); m=lambda k: sum(float(x[k]) for x in r)/len(r)
print(f'gpu_busy={m(\"gpu_busy\"):.0f}% gpu_mhz={m(\"gpu_mhz\"):.0f} cpu_tmax={max(float(x[\"cpu_tmax\"]) for x in r)/1000:.0f}C gpu_tmax={max(float(x[\"gpu_tmax\"]) for x in r)/1000:.0f}C')" $out/poll.csv
  [ -n "$COMMIT" ] && python3 $T/results.py $out --commit $COMMIT --scene "${SCENE:-plateau Mega Wiggler, autoplay $REC s, télé}" --settings "${edits[*]}" --skip 5 >/dev/null
  echo "== $1-$n-$name $(grep -o 'Driver:.*' $out/logcat.txt | head -1 | cut -c1-70) ${edits[*]}"
done
back
