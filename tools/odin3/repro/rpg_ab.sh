#!/bin/zsh
# rpg_ab.sh RUN explore | RUN NAME[:Section.key=value,...] ... : Super Mario RPG on the TV, on a copy of Hamza's save
# (his folder moved to .rpg-keep, the adb-readable files copied in its place, folder put back and its listing checked).
# explore: timed TV shots while pressing A from the title. Otherwise an A/B of per-game INI variants: Hamza's INI plus
# the edits, boot, START presses to the field (autosave loaded), REC s of roam with SurfaceFlinger, TV recording and
# poll.sh, then regress.py analyze (THUMB=1 also relaunches and shoots the Load menu); his INI is put back at the end and its md5 checked. Run it under lockf ~/.odin3-console.lock.
export PATH=$HOME/Library/Android/sdk/platform-tools:$PATH
A=adb; D=${0:A:h}; T=${D:h}
PKG=com.miHoYo.Yuanshen.relWithDebInfo; TID=0100BC0018138000; REC=${REC:-75}; BOOT=${BOOT:-75}
F=/storage/emulated/0/Android/data/$PKG/files
I=$F/config/custom/$TID.ini
S=$F/nand/user/save/0000000000000000/F87F57BFCE6345060247FB61C12F5AE7/$TID
U="content://com.android.externalstorage.documents/tree/FBDD-1F17%3AEmulations%2FROMs%2Fswitch/document/FBDD-1F17%3AEmulations%2FROMs%2Fswitch%2FSuper%20Mario%20RPG%20%5B${TID}%5D%5Bv0%5D.nsp"
# boot presses to the field, "buttons@wait_s" (bridge bits: a=1 b=2 plus=400)
START=${START:-1@7 1@7 1@7 1@7 1@12}
W=$T/runs/rpg/$1; mkdir -p $W
DISP=$($A shell dumpsys SurfaceFlinger --display-id | tr -d '\r' | grep -v 'pnpId=QCM' | sed -n 's/^Display \([0-9]*\).*/\1/p' | head -1)
$A shell dumpsys activity activities | grep -q 'topResumedActivity.*EmulationActivity' && { echo "game open, not now"; exit 2; }
$A shell am force-stop $PKG
[ -s $W/orig.ini ] || $A pull $I $W/orig.ini >/dev/null || exit 3
O=$(md5 -q $W/orig.ini)

shot() { $A exec-out screencap -p ${DISP:+-d $DISP} > $OUT/$1.png; [ -n "$2" ] || sips -Z 540 $OUT/$1.png >/dev/null; }
press() { python3 $T/bridge.py pad 0 $1 0 0 0 0 150 >/dev/null; python3 $T/bridge.py pad 0 0 0 0 0 0 50 >/dev/null; }
prep() {
  $A shell am force-stop $PKG
  $A shell "[ -d $S ] && [ ! -e $S.rpg-keep ]" || { echo "save state unexpected, abort"; exit 3; }
  $A shell "ls -lnR $S 2>&1" > $OUT/save_before.txt
  $A shell "mv $S $S.rpg-keep && mkdir $S && chmod 777 $S" || exit 3
  $A shell "for f in $S.rpg-keep/*; do cp \$f $S/ 2>/dev/null; done; chmod 666 $S/*; ls -l $S" > $OUT/save_copy.txt
}
restore() {
  $A shell am force-stop $PKG
  $A shell "[ -d $S.rpg-keep ]" || { echo "no kept save"; exit 3; }
  $A shell "rm -rf $S 2>/dev/null; if [ -e $S ]; then mv $S $S.rpg-trash-$(date +%s) || exit 1; fi; mv $S.rpg-keep $S"
  $A shell "ls -lnR $S 2>&1" > $OUT/save_after.txt
  cmp -s $OUT/save_before.txt $OUT/save_after.txt && echo "save restored identical" || { echo "SAVE LISTING CHANGED"; diff $OUT/save_before.txt $OUT/save_after.txt | head; exit 3; }
}
back() {
  $A shell am force-stop $PKG; $A push $W/orig.ini $I >/dev/null
  [ "$($A shell md5sum $I | cut -c1-32)" = $O ] && echo "ini restored identical" || echo "INI NOT RESTORED"
  $A shell am start -n $PKG/org.yuzu.yuzu_emu.ui.main.MainActivity >/dev/null
}
launch() {
  $A logcat -c
  $A shell am start -a android.intent.action.VIEW -t application/octet-stream -d "\"$U\"" -n $PKG/org.yuzu.yuzu_emu.activities.EmulationActivity >/dev/null
  $A forward tcp:6560 tcp:6560 >/dev/null
}
trap '[ -n "$OUT" ] && restore; back; exit 4' INT TERM

if [ "$2" = explore ]; then
  OUT=$W/explore; mkdir -p $OUT; prep; launch
  for t in 30 15 15 15; do sleep $t; shot boot$SECONDS; done
  for w in 4 6 6 8 15 15; do press 1; sleep $w; shot a$SECONDS; done
  $A logcat -d | grep -E 'Driver:|GPU:|Fatal signal' | tail -5 > $OUT/logcat.txt
  restore; back; exit 0
fi

$A push $T/poll.sh /data/local/tmp/poll.sh >/dev/null
n=0
for v in ${@:2}; do
  n=$((n + 1)); name=${v%%:*}; edits=(); [[ $v == *:* ]] && edits=(${(s:,:)${v#*:}})
  OUT=$W/$n-$name; rm -rf $OUT; mkdir -p $OUT
  if [ -n "$edits" ]; then python3 $T/setini.py $W/orig.ini $W/$name.ini $edits || break; else cp $W/orig.ini $W/$name.ini; fi
  $A shell am force-stop $PKG; $A push $W/$name.ini $I >/dev/null
  prep; launch; sleep $BOOT
  for p in ${(s: :)START}; do press ${p%@*}; sleep ${p#*@}; done
  shot field
  L=$($A shell "dumpsys SurfaceFlinger --list" | tr -d '\r' | grep -oE 'SurfaceView\[[^]]*EmulationActivity\]\(BLAST\)#[0-9]+' | head -1)
  $A shell "screenrecord ${DISP:+--display-id $DISP} --time-limit $((REC + 2)) --size 1920x1080 --bit-rate 20000000 /data/local/tmp/rpg.mp4" &
  $A shell "sh /data/local/tmp/poll.sh $REC > /data/local/tmp/poll.csv" &
  python3 $T/regress_input.py roam $REC &
  t0=$SECONDS
  while [ $((SECONDS - t0)) -lt $REC ]; do echo @; $A shell "dumpsys SurfaceFlinger --latency '$L'"; sleep 1; done > $OUT/sf.txt
  wait
  shot end
  ALIVE=$($A shell dumpsys activity activities | grep -c 'topResumedActivity.*EmulationActivity')
  $A logcat -d | grep -E 'Fatal signal|PANIC|Assertion|rejected|Out of memory|Driver:|GPU:' | tail -50 > $OUT/logcat.txt
  $A pull /data/local/tmp/poll.csv $OUT/poll.csv >/dev/null
  $A pull /data/local/tmp/rpg.mp4 $OUT/ >/dev/null && $A shell rm /data/local/tmp/rpg.mp4
  ffmpeg -v error -y -i $OUT/rpg.mp4 -c copy -bsf:v h264_mp4toannexb -f h264 $OUT/tv.h264 && rm $OUT/rpg.mp4
  # THUMB=1: relaunch on the same save copy and shoot the Load menu at full size (autosave thumbnail of this run)
  if [ -n "$THUMB" ]; then
    $A shell am force-stop $PKG; launch; sleep $BOOT
    for k in 1 2 3; do press 1; sleep 7; shot load$k full; done
  fi
  restore; OUT=
  printf '{"title": "%s", "name": "Super Mario RPG %s", "secs": %s, "alive": %s, "save": "copy", "build": "live", "mode": "roam"}\n' $TID $name $REC $ALIVE > $W/$n-$name/meta.json
  python3 $T/regress.py analyze $W/$n-$name
  python3 -c "import csv,sys; r=list(csv.DictReader(open(sys.argv[1]))); m=lambda k: sum(float(x[k]) for x in r)/len(r)
print(f'gpu_busy={m(\"gpu_busy\"):.0f}% gpu_mhz={m(\"gpu_mhz\"):.0f} cpu_tmax={max(float(x[\"cpu_tmax\"]) for x in r)/1000:.0f}C gpu_tmax={max(float(x[\"gpu_tmax\"]) for x in r)/1000:.0f}C')" $W/$n-$name/poll.csv
  grep -h 'Driver:' $W/$n-$name/logcat.txt | tail -1 | sed 's/.*Driver:/Driver:/'
  echo "== $n-$name ${edits[*]}"
done
back
