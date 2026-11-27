#!/bin/zsh
# smg2.sh RUN explore SECS|route PERIOD NAME[:edits] ... | RUN ab REC NAME[:Section.key=value,...] ... : Super Mario Galaxy 2 (update
# mounted, launched by URI like ES-DE) on a fresh save; Hamza's save folder is renamed away before the first boot and
# renamed back at the end, listing compared. explore: shots in runs/regress/RUN/shots/. ab: each variant is Hamza's
# INI plus its edits; new game, smg2.py route to the first playable scene, then REC s of regress_input.py roam with the TV
# recorded, SurfaceFlinger and poll.sh sampled, regress.py analyze into runs/regress/RUN-n-NAME/; his INI is put back
# at the end and its md5 checked. Run it under lockf -k ~/.odin3-console.lock.
export PATH=$HOME/Library/Android/sdk/platform-tools:$PATH
A=adb; D=${0:A:h}; T=${D:h}
PKG=com.miHoYo.Yuanshen.relWithDebInfo; TID=0100FD8022DAA000
F=/storage/emulated/0/Android/data/$PKG/files
P=$F/nand/user/save/0000000000000000/F87F57BFCE6345060247FB61C12F5AE7; S=$P/$TID
ROMS='content://com.android.externalstorage.documents/tree/FBDD-1F17%3AEmulations%2FROMs%2Fswitch/document/FBDD-1F17%3AEmulations%2FROMs%2Fswitch%2F'
ROM='Super Mario Galaxy 2 [0100FD8022DAA000][v0].NSP'
OUT=$T/runs/regress/$1; MODE=$2; mkdir -p $OUT || exit 1

if $A shell dumpsys activity activities | grep -q 'topResumedActivity.*EmulationActivity'; then
  echo "a game is open on the console: not now"; exit 2
fi
$A shell am force-stop $PKG
if $A shell "[ -e $S.smg2-keep ]"; then echo "leftover $S.smg2-keep: restore by hand"; exit 3; fi
$A shell "ls -lnR $S 2>&1" > $OUT/save_before.txt
$A shell "mv $S $S.smg2-keep" || exit 3
restore() {
  $A shell am force-stop $PKG
  # a save Eden wrote cannot be deleted by adb: set aside instead
  $A shell "rm -rf $S 2>/dev/null; if [ -e $S ]; then mv $S $S.smg2-trash-\$(date +%s); fi; mv $S.smg2-keep $S"
  $A shell "ls -lnR $S 2>&1" > $OUT/save_after.txt
  cmp -s $OUT/save_before.txt $OUT/save_after.txt && echo "save listing identical" || echo "SAVE LISTING CHANGED"
  $A shell am start -n $PKG/org.yuzu.yuzu_emu.ui.main.MainActivity >/dev/null
}
trap 'restore; exit 4' INT TERM

launch() {
  local U=$ROMS$(python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$ROM")
  $A logcat -c
  $A shell am start -a android.intent.action.VIEW -t application/octet-stream -d "\"$U\"" \
    -n $PKG/org.yuzu.yuzu_emu.activities.EmulationActivity >/dev/null
  $A forward tcp:6560 tcp:6560 >/dev/null
}

I=$F/config/custom/$TID.ini; W=$OUT/ini; mkdir -p $W
[ -s $W/orig.ini ] || $A pull $I $W/orig.ini >/dev/null || { restore; exit 3; }
O=$(md5 -q $W/orig.ini)
back() {
  $A shell am force-stop $PKG; $A push $W/orig.ini $I >/dev/null
  [ "$($A shell md5sum $I | cut -c1-32)" = $O ] && echo "ini restored identical" || echo "INI NOT RESTORED"
}
trap 'back; restore; exit 4' INT TERM

measure() {  # $1 out dir, $2 secs
  local OUT=$1 R=$2 DISP L t1 ALIVE
  mkdir -p $OUT
  DISP=$($A shell dumpsys SurfaceFlinger --display-id | tr -d '\r' | grep -v 'pnpId=QCM' | sed -n 's/^Display \([0-9]*\).*/\1/p' | head -1)
  L=$($A shell "dumpsys SurfaceFlinger --list" | tr -d '\r' | grep -oE 'SurfaceView\[[^]]*EmulationActivity\]\(BLAST\)#[0-9]+' | head -1)
  $A shell "sh /data/local/tmp/poll.sh $R > /data/local/tmp/spoll.csv" &
  $A shell "screenrecord ${DISP:+--display-id $DISP} --time-limit $((R + 2)) --size 1920x1080 --bit-rate 20000000 /data/local/tmp/smg2.mp4" &
  python3 $T/regress_input.py roam $R &
  t1=$SECONDS
  while [ $((SECONDS - t1)) -lt $R ]; do echo @; $A shell "dumpsys SurfaceFlinger --latency '$L'"; sleep 1; done > $OUT/sf.txt
  wait
  ALIVE=$($A shell dumpsys activity activities | grep -c 'topResumedActivity.*EmulationActivity')
  $A logcat -d | grep -E 'Fatal signal|PANIC|Assertion|rejected|build failed|Device loss|Out of memory|Driver:|GPU:|LogSettings' | tail -80 > $OUT/logcat.txt
  echo "alive=$ALIVE build_failed=$(grep -c 'build failed' $OUT/logcat.txt)"
  $A pull /data/local/tmp/spoll.csv $OUT/poll.csv >/dev/null
  $A pull /data/local/tmp/smg2.mp4 $OUT/ >/dev/null && $A shell rm /data/local/tmp/smg2.mp4
  ffmpeg -v error -y -i $OUT/smg2.mp4 -c copy -bsf:v h264_mp4toannexb -f h264 $OUT/tv.h264 && rm $OUT/smg2.mp4
  printf '{"title": "%s", "name": "Super Mario Galaxy 2", "secs": %s, "alive": %s, "save": "new", "build": "live", "mode": "play"}\n' \
    $TID $R $ALIVE > $OUT/meta.json
  python3 $T/regress.py analyze $OUT
  python3 -c "import csv,sys; r=list(csv.DictReader(open(sys.argv[1]))); m=lambda k: sum(float(x[k]) for x in r)/len(r)
print(f'gpu_busy={m(\"gpu_busy\"):.0f}% gpu_mhz={m(\"gpu_mhz\"):.0f} cpu_tmax={max(float(x[\"cpu_tmax\"]) for x in r)/1000:.0f}C gpu_tmax={max(float(x[\"gpu_tmax\"]) for x in r)/1000:.0f}C')" $OUT/poll.csv
}

case $MODE in
  explore)
    n=0
    for v in ${@:5}; do
      n=$((n + 1)); name=${v%%:*}; edits=(); [[ $v == *:* ]] && edits=(${(s:,:)${v#*:}})
      out=$T/runs/regress/$1-$n-$name; mkdir -p $out
      if [ -n "$edits" ]; then python3 $T/setini.py $W/orig.ini $W/$name.ini $edits || break; else cp $W/orig.ini $W/$name.ini; fi
      $A shell am force-stop $PKG; $A push $W/$name.ini $I >/dev/null
      $A shell "if [ -e $S ]; then mv $S $S.smg2-trash-\$(date +%s); fi"
      launch
      if [ "$3" = route ]; then python3 $D/smg2.py route $out/shots; else python3 $D/smg2.py explore $3 $out/shots $4; fi
      $A logcat -d > $out/logcat-full.txt
      grep -E 'Fatal signal|PANIC|Assertion|rejected|build failed|Driver:|LogSettings' $out/logcat-full.txt | cut -c1-250 | tail -40 > $out/logcat.txt
      echo "== $1-$n-$name ${edits[*]}: $(grep -cE 'Fatal signal|build failed' $out/logcat-full.txt) fatal, $(grep -m1 -o 'Driver: .*' $out/logcat-full.txt)"
    done
    back
    ;;
  ab)
    $A push $T/poll.sh /data/local/tmp/poll.sh >/dev/null
    n=0
    for v in ${@:4}; do
      n=$((n + 1)); name=${v%%:*}; edits=(); [[ $v == *:* ]] && edits=(${(s:,:)${v#*:}})
      out=$T/runs/regress/$1-$n-$name
      if [ -n "$edits" ]; then python3 $T/setini.py $W/orig.ini $W/$name.ini $edits || break; else cp $W/orig.ini $W/$name.ini; fi
      $A shell am force-stop $PKG; $A push $W/$name.ini $I >/dev/null
      $A shell "if [ -e $S ]; then mv $S $S.smg2-trash-\$(date +%s); fi"
      launch
      python3 $D/smg2.py route $out/route || { echo "$name: route failed"; continue; }
      $A logcat -d | grep -E 'Report: Driver:' | tail -1
      measure $out $3
      echo "== $1-$n-$name ${edits[*]}"
    done
    back
    ;;
esac
restore
