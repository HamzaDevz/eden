#!/bin/zsh
# wonder.sh RUN prep [PRESSES] | keys PRESSES | enter | measure NAME MODE SECS | restore : Super Mario Bros. Wonder on a
# copy of Hamza's save. prep makes the .bench copy once (cp of his folder), renames his folder to .regress-keep like
# regress.sh, puts a copy of the bench in its place, boots by URI (BOOT s, default 60), plays PRESSES like keys, then
# presses on (L+R on the title, A elsewhere) until wonder_where.py sees the world map; enter: A until in the level.
# keys: "hex@wait" presses (bridge.py bits, a=1 plus=400 l+r=c0, left=1000 right=4000), a TV shot after each in
# runs/regress/RUN/stepN.png. measure: SECS s of TV recording, SurfaceFlinger and poll.sh while regress_input.py MODE
# runs (or idle: no input; right: walks right, A held 0.4 s every second), then regress.py analyze into runs/regress/RUN/010015100B514000.NAME. restore: Eden stopped, copy deleted, his
# folder renamed back and its listing checked, Eden on MainActivity.
export PATH=$HOME/Library/Android/sdk/platform-tools:$PATH
A=adb; D=${0:A:h}; T=${D:h}
PKG=com.miHoYo.Yuanshen.relWithDebInfo
F=/storage/emulated/0/Android/data/$PKG/files
S=$F/nand/user/save/0000000000000000/F87F57BFCE6345060247FB61C12F5AE7/010015100B514000
RUN=$T/runs/regress/$1; mkdir -p $RUN
ROM='Super Mario Bros. Wonder[010015100B514000][1.0.0][0][16.0.3].nsp'
U='content://com.android.externalstorage.documents/tree/FBDD-1F17%3AEmulations%2FROMs%2Fswitch/document/FBDD-1F17%3AEmulations%2FROMs%2Fswitch%2F'$(python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$ROM")

restore() {
  $A shell am force-stop $PKG
  $A shell "[ -d $S.regress-keep ]" || { echo "no kept save, nothing restored"; exit 3; }
  $A shell "rm -rf $S 2>/dev/null; if [ -e $S ]; then mv $S $S.regress-trash-$(date +%s) || exit 1; fi; mv $S.regress-keep $S"
  $A shell "ls -lnR $S 2>&1" > $RUN/save_after.txt
  cmp -s $RUN/save_before.txt $RUN/save_after.txt && echo "save restored identical" \
    || { echo "SAVE LISTING CHANGED"; diff $RUN/save_before.txt $RUN/save_after.txt | head; }
  $A shell am start -n $PKG/org.yuzu.yuzu_emu.ui.main.MainActivity >/dev/null
}

keys() {
  local f=($RUN/step*.png(N)) i; i=${#f}
  for p in ${(s: :)1}; do
    i=$((i + 1)); python3 $T/regress_input.py press ${p%@*} ${p#*@}
    zsh $D/luma.sh >/dev/null; sips -Z 540 $D/tv.png --out $RUN/step$i.png >/dev/null
  done
}

where() { local y=$(zsh $D/luma.sh | cut -d= -f2); [ ${y:-0} -lt 20 ] && echo "dark $y" || python3 $D/wonder_where.py $D/tv.png; }
# reach SCREEN (closer than MAX to its reference): L+R on the title, wait while the screen is dark (loading), else A
# and WAIT s; at most 6 tries
reach() {
  local w
  for k in 1 2 3 4 5 6; do
    w=($(where)); [ ${w[1]} = $1 ] && [ ${w[2]%.*} -lt $2 ] && { echo "at $1 (${w[2]})"; return 0; }
    case ${w[1]} in
      title) python3 $T/regress_input.py press c0 5;;
      dark) sleep 3;;
      *) python3 $T/regress_input.py press 1 $3;;
    esac
  done
  echo "not at $1: ${w[*]}"; return 1
}

case $2 in
prep)
  $A shell dumpsys activity activities | grep -q 'topResumedActivity.*EmulationActivity' && { echo "game open, not now"; exit 2; }
  $A shell "[ -d $S ] && [ ! -e $S.regress-keep ]" || { echo "save state unexpected, abort"; exit 3; }
  $A shell am force-stop $PKG
  $A shell "[ -d $S.bench ] || { cp -r $S $S.bench && chmod -R 777 $S.bench; }" || exit 3
  $A shell "ls -lnR $S 2>&1" > $RUN/save_before.txt
  $A shell "mv $S $S.regress-keep" || exit 3
  $A shell "cp -r $S.bench $S && chmod -R 777 $S" || { restore; exit 3; }
  rm -f $RUN/step*.png(N)
  $A logcat -c
  $A shell am start -a android.intent.action.VIEW -t application/octet-stream -d "\"$U\"" \
    -n $PKG/org.yuzu.yuzu_emu.activities.EmulationActivity >/dev/null
  $A forward tcp:6560 tcp:6560 >/dev/null
  sleep ${BOOT:-60}
  keys "$3"
  $A logcat -d | grep -E 'Report: Driver:' | tail -1
  reach map 15 6 || { restore; exit 5; };;
keys) keys "$3";;
enter) reach level 30 12;;
measure)
  OUT=$RUN/010015100B514000.$3; rm -rf $OUT; mkdir -p $OUT; R=$5
  DISP=$($A shell dumpsys SurfaceFlinger --display-id | tr -d '\r' | grep -v 'pnpId=QCM' | sed -n 's/^Display \([0-9]*\).*/\1/p' | head -1)
  L=$($A shell "dumpsys SurfaceFlinger --list" | tr -d '\r' | grep -oE 'SurfaceView\[[^]]*EmulationActivity\]\(BLAST\)#[0-9]+' | head -1)
  $A push $T/poll.sh /data/local/tmp/poll.sh >/dev/null
  $A shell "sh /data/local/tmp/poll.sh $R > /data/local/tmp/wpoll.csv" &
  $A shell "screenrecord ${DISP:+--display-id $DISP} --time-limit $((R + 2)) --size 1920x1080 --bit-rate 20000000 /data/local/tmp/wonder.mp4" &
  case $4 in
    idle) sleep $R &;;
    right) python3 -c "import sys, time; sys.path.insert(0, sys.argv[1]); import bridge
b = bridge.Bridge(); end = time.time() + float(sys.argv[2]); j = 0
while time.time() < end:
    b.pad(0, 1 if j % 20 < 8 else 0, 32767, 0); j += 1; time.sleep(0.05)
b.pad(0); b.release(0)" $T $R &;;
    *) python3 $T/regress_input.py $4 $R &;;
  esac
  t1=$SECONDS
  while [ $((SECONDS - t1)) -lt $R ]; do echo @; $A shell "dumpsys SurfaceFlinger --latency '$L'"; sleep 1; done > $OUT/sf.txt
  wait
  ALIVE=$($A shell dumpsys activity activities | grep -c 'topResumedActivity.*EmulationActivity')
  $A logcat -d | grep -E 'Fatal signal|PANIC|Assertion|rejected|Out of memory|Driver:|GPU:' | tail -50 > $OUT/logcat.txt
  $A pull /data/local/tmp/wpoll.csv $OUT/poll.csv >/dev/null
  $A pull /data/local/tmp/wonder.mp4 $OUT/ >/dev/null && $A shell rm /data/local/tmp/wonder.mp4
  ffmpeg -v error -y -i $OUT/wonder.mp4 -c copy -bsf:v h264_mp4toannexb -f h264 $OUT/tv.h264 && rm $OUT/wonder.mp4
  printf '{"title": "010015100B514000", "name": "Super Mario Bros. Wonder", "secs": %s, "alive": %s, "save": "bench", "build": "live", "mode": "%s"}\n' \
    $R $ALIVE $3 > $OUT/meta.json
  python3 $T/regress.py analyze $OUT
  python3 -c "import csv,sys; r=list(csv.DictReader(open(sys.argv[1]))); m=lambda k: sum(float(x[k]) for x in r)/len(r)
print(f'gpu_busy={m(\"gpu_busy\"):.0f}% gpu_mhz={m(\"gpu_mhz\"):.0f} cpu_tmax={max(float(x[\"cpu_tmax\"]) for x in r)/1000:.0f}C gpu_tmax={max(float(x[\"gpu_tmax\"]) for x in r)/1000:.0f}C')" $OUT/poll.csv;;
restore) restore;;
esac
