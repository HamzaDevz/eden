#!/bin/zsh
# mvdk_ab.sh RUN NAME[:Section.key=value,...] ... : Mario vs. Donkey Kong (0100B99019412000) A/B of per-game INI
# variants on the TV. Each variant is Hamza's INI plus its edits, booted on a copy of his save (mvdk_save.sh), PRESSES
# ("hex@wait_s[@hold_ms]") into a level, WARM s of play (pipelines built, not measured), then REC s of TV capture,
# SurfaceFlinger and poll.sh while roaming; his save is put back after each run and his INI at the end, both checked.
export PATH=$HOME/Library/Android/sdk/platform-tools:$PATH
A=adb; D=${0:A:h}; T=${D:h}
PKG=com.miHoYo.Yuanshen.relWithDebInfo; TID=0100B99019412000
F=/storage/emulated/0/Android/data/$PKG/files; I=$F/config/custom/$TID.ini
BOOT=${BOOT:-55}; WARM=${WARM:-25}; REC=${REC:-80}; MODE=${MODE:-roam}
PRESSES=${PRESSES:-"1@4 1@6 c0@5@300 c0@5@300 1@4 1@4 1@5"}  # intro, title, L+R, file 1, 8-3 on the map, start
U='content://com.android.externalstorage.documents/tree/FBDD-1F17%3AEmulations%2FROMs%2Fswitch/document/FBDD-1F17%3AEmulations%2FROMs%2Fswitch%2FMario%20vs.%20Donkey%20Kong%20v1.0.0%20%5B0100B99019412000%5D%5Bv0%5D%5BAPP%5D.nsp'
W=$T/runs/regress/$1.ini; mkdir -p $W
$A shell dumpsys activity activities | grep -q 'topResumedActivity.*EmulationActivity' && { echo "game open, not now"; exit 2; }
$A shell am force-stop $PKG
[ -s $W/orig.ini ] || $A pull $I $W/orig.ini >/dev/null || exit 3
O=$(md5 -q $W/orig.ini)
back() {
  $A shell am force-stop $PKG; $A push $W/orig.ini $I >/dev/null
  [ "$($A shell md5sum $I | cut -c1-32)" = $O ] && echo "ini restored identical" || echo "INI NOT RESTORED"
  $A shell am start -n $PKG/org.yuzu.yuzu_emu.ui.main.MainActivity >/dev/null
}
CUR=
trap '[ -n "$CUR" ] && zsh $D/mvdk_save.sh back $CUR; back; exit 4' INT TERM
$A push $T/poll.sh /data/local/tmp/poll.sh >/dev/null
DISP=$($A shell dumpsys SurfaceFlinger --display-id | tr -d '\r' | grep -v 'pnpId=QCM' | sed -n 's/^Display \([0-9]*\).*/\1/p' | head -1)
n=0
for v in ${@:2}; do
  n=$((n + 1)); name=${v%%:*}; edits=(); [[ $v == *:* ]] && edits=(${(s:,:)${v#*:}})
  run=$1-$n-$name; out=$T/runs/regress/$run/$TID.$MODE; rm -rf $out; mkdir -p $out
  if [ -n "$edits" ]; then python3 $T/setini.py $W/orig.ini $W/$name.ini $edits || break; else cp $W/orig.ini $W/$name.ini; fi
  $A shell am force-stop $PKG; $A push $W/$name.ini $I >/dev/null
  zsh $D/mvdk_save.sh aside $out || break
  CUR=$out
  $A logcat -c
  $A shell am start -a android.intent.action.VIEW -t application/octet-stream -d "\"$U\"" -n $PKG/org.yuzu.yuzu_emu.activities.EmulationActivity >/dev/null
  $A forward tcp:6560 tcp:6560 >/dev/null
  sleep $BOOT
  for p in ${(s: :)PRESSES}; do
    b=${p%%@*}; r=${p#*@}; w=${r%%@*}; h=${r#*@}; [ "$h" = "$r" ] && h=150
    python3 $T/bridge.py pad 0 $b 0 0 0 0 $h >/dev/null; python3 $T/bridge.py pad 0 0 0 0 0 0 50 >/dev/null; sleep $w
  done
  zsh $D/luma.sh >/dev/null; sips -Z 540 $D/tv.png --out $out/entry.png >/dev/null
  python3 $T/regress_input.py $MODE $WARM
  L=$($A shell "dumpsys SurfaceFlinger --list" | tr -d '\r' | grep -oE 'SurfaceView\[[^]]*EmulationActivity\]\(BLAST\)#[0-9]+' | head -1)
  $A shell "screenrecord ${DISP:+--display-id $DISP} --time-limit $((REC + 2)) --size 1920x1080 --bit-rate 20000000 /data/local/tmp/regress.mp4" &
  $A shell "sh /data/local/tmp/poll.sh $REC > /data/local/tmp/poll.csv" &
  python3 $T/regress_input.py $MODE $REC &
  t0=$SECONDS
  while [ $((SECONDS - t0)) -lt $REC ]; do echo @; $A shell "dumpsys SurfaceFlinger --latency '$L'"; sleep 1; done > $out/sf.txt
  wait
  ALIVE=$($A shell dumpsys activity activities | grep -c 'topResumedActivity.*EmulationActivity')
  zsh $D/luma.sh >/dev/null; sips -Z 540 $D/tv.png --out $out/exit.png >/dev/null
  { $A logcat -d | grep -E 'LogSettings: (M.|-C) '; $A logcat -d | grep -E 'Fatal signal|PANIC|Assertion|rejected|Out of memory|Driver:|GPU:' | tail -100; } > $out/logcat.txt
  $A pull /data/local/tmp/regress.mp4 $out/ >/dev/null && $A shell rm /data/local/tmp/regress.mp4
  ffmpeg -v error -y -i $out/regress.mp4 -c copy -bsf:v h264_mp4toannexb -f h264 $out/tv.h264 && rm $out/regress.mp4
  $A pull /data/local/tmp/poll.csv $out/poll.csv >/dev/null
  zsh $D/mvdk_save.sh back $out || { back; exit 3; }
  CUR=
  printf '{"title": "%s", "name": "Mario vs. Donkey Kong", "secs": %s, "alive": %s, "save": "copy", "build": "live", "mode": "%s"}\n' \
    $TID $REC $ALIVE $MODE > $out/meta.json
  python3 $T/regress.py analyze $out
  python3 -c "import csv,sys; r=list(csv.DictReader(open(sys.argv[1]))); m=lambda k: sum(float(x[k]) for x in r)/len(r)
print(f'gpu_busy={m(\"gpu_busy\"):.0f}% gpu_mhz={m(\"gpu_mhz\"):.0f} cpu_tmax={max(float(x[\"cpu_tmax\"]) for x in r)/1000:.0f}C gpu_tmax={max(float(x[\"gpu_tmax\"]) for x in r)/1000:.0f}C')" $out/poll.csv
  grep -h 'Report: Driver:' $out/logcat.txt | tail -1 | sed 's/.*Report: //'
  echo "== $run ${edits[*]}"
done
back
