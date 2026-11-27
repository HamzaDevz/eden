#!/bin/zsh
# sk_live.sh RUN prep|measure|restore : Silksong on a new game (Hamza's save renamed away like regress.sh). prep
# launches by URI and walks the menus to a new game; measure films the TV, samples SurfaceFlinger and poll.sh while
# SKMODE (regress_input.py mode, default silksong) plays SECS s; restore puts the save back and checks its listing.
export PATH=$HOME/Library/Android/sdk/platform-tools:$PATH
A=adb; D=${0:A:h}; T=${D:h}
PKG=com.miHoYo.Yuanshen.relWithDebInfo
F=/storage/emulated/0/Android/data/$PKG/files
S=$F/nand/user/save/0000000000000000/F87F57BFCE6345060247FB61C12F5AE7/010013C00E930000
OUT=$T/runs/regress/$1/010013C00E930000.sk; [ $2 = prep ] && rm -rf $OUT; mkdir -p $OUT
U='content://com.android.externalstorage.documents/tree/FBDD-1F17%3AEmulations%2FROMs%2Fswitch/document/FBDD-1F17%3AEmulations%2FROMs%2Fswitch%2FHollow%20Knight%20Silksong%20%5B010013C00E930000%5D%5Bv0%5D%5BBase%5D.nsp'
press() { if [ $1 = 0 ]; then sleep $2; else python3 $T/regress_input.py press $1 $2; fi; }
# title menu, slot 1, intro text, opening cinematic (not skippable), Act 1 card, Hornet lying in Moss Grotto
SKPRESS=${SKPRESS:-1@3 1@4 0@6 1@2 1@2 1@6 0@20 0@15 0@30}
restore() {
  $A shell "[ -d $S.sk-keep ]" || { echo "no kept save, nothing to restore"; return 3; }
  $A shell am force-stop $PKG
  $A shell "rm -rf $S 2>/dev/null; if [ -e $S ]; then mv $S $S.sk-trash-$(date +%s) || exit 1; fi; mv $S.sk-keep $S"
  $A shell "ls -lnR $S 2>&1" > $OUT/save_after.txt
  cmp -s $OUT/save_before.txt $OUT/save_after.txt && echo "save restored identical" || { echo "SAVE LISTING CHANGED"; diff $OUT/save_before.txt $OUT/save_after.txt | head; }
  $A shell am start -n $PKG/org.yuzu.yuzu_emu.ui.main.MainActivity >/dev/null
}
case $2 in
restore) restore; exit;;
prep)
  $A shell dumpsys activity activities | grep -q 'topResumedActivity.*EmulationActivity' && { echo "game open, not now"; exit 2; }
  $A shell "[ -d $S ] && [ ! -e $S.sk-keep ]" || { echo "save state unexpected, abort"; exit 3; }
  $A shell am force-stop $PKG
  $A shell "ls -lnR $S 2>&1" > $OUT/save_before.txt
  $A shell "mv $S $S.sk-keep" || exit 3
  trap 'restore; exit 4' INT TERM
  $A logcat -c
  $A shell am start -a android.intent.action.VIEW -t application/octet-stream -d "\"$U\"" -n $PKG/org.yuzu.yuzu_emu.activities.EmulationActivity >/dev/null
  $A forward tcp:6560 tcp:6560 >/dev/null
  sleep ${BOOT:-75}
  for p in ${(s: :)SKPRESS}; do press ${p%@*} ${p#*@}; done
  # the cinematic's length on screen varies with the boot: wait for the green Moss Grotto (mean R G B of the TV
  # ~50 75 48; the cinematic's white flashes are grey, its cards black)
  t0=$SECONDS
  while zsh $D/luma.sh >/dev/null; rgb=($(ffmpeg -v error -i $D/tv.png -vf scale=1:1 -f rawvideo -pix_fmt rgb24 - | od -An -tu1))
    ! { [ ${rgb[2]:-0} -gt 55 ] && [ ${rgb[2]} -gt $((rgb[1] + 15)) ] && [ ${rgb[2]} -gt $((rgb[3] + 15)) ]; } && [ $((SECONDS - t0)) -lt 120 ]; do sleep 2; done
  echo "grotto rgb=${rgb[*]} after $((SECONDS - t0)) s"; sleep 3; cp $D/tv.png $OUT/prep.png
  echo "prep done, game running, save aside";;
measure)
  $A shell "[ -d $S.sk-keep ]" && [ -s $OUT/save_before.txt ] || { echo "no kept save / listing, abort"; exit 3; }
  trap 'restore; exit 4' INT TERM
  R=${SECS:-90}
  DISP=$($A shell dumpsys SurfaceFlinger --display-id | tr -d '\r' | grep -v 'pnpId=QCM' | sed -n 's/^Display \([0-9]*\).*/\1/p' | head -1)
  L=$($A shell "dumpsys SurfaceFlinger --list" | tr -d '\r' | grep -oE 'SurfaceView\[[^]]*EmulationActivity\]\(BLAST\)#[0-9]+' | head -1)
  $A push $T/poll.sh /data/local/tmp/poll.sh >/dev/null
  $A shell "sh /data/local/tmp/poll.sh $R > /data/local/tmp/poll.csv" &
  $A shell "screenrecord ${DISP:+--display-id $DISP} --time-limit $((R + 2)) --size 1920x1080 --bit-rate 20000000 /data/local/tmp/regress.mp4" &
  python3 $T/regress_input.py ${SKMODE:-silksong} $R &
  t1=$SECONDS
  while [ $((SECONDS - t1)) -lt $R ]; do echo @; $A shell "dumpsys SurfaceFlinger --latency '$L'"; sleep 1; done > $OUT/sf.txt
  wait
  ALIVE=$($A shell dumpsys activity activities | grep -c 'topResumedActivity.*EmulationActivity')
  $A logcat -d | grep -E 'Fatal signal|PANIC|Assertion|rejected|Out of memory|Driver:|GPU:' | tail -50 > $OUT/logcat.txt
  $A pull /data/local/tmp/poll.csv $OUT/poll.csv >/dev/null
  $A pull /data/local/tmp/regress.mp4 $OUT/ >/dev/null && $A shell rm /data/local/tmp/regress.mp4
  ffmpeg -v error -y -i $OUT/regress.mp4 -c copy -bsf:v h264_mp4toannexb -f h264 $OUT/tv.h264 && rm $OUT/regress.mp4
  restore
  printf '{"title": "010013C00E930000", "name": "Hollow Knight Silksong", "secs": %s, "alive": %s, "save": "none", "build": "live", "mode": "%s"}\n' $R $ALIVE ${SKMODE:-silksong} > $OUT/meta.json
  python3 $T/regress.py analyze $OUT
  python3 -c "import csv,sys; r=list(csv.DictReader(open(sys.argv[1]))); m=lambda k: sum(float(x[k]) for x in r)/len(r)
print(f'gpu_busy={m(\"gpu_busy\"):.0f}% gpu_mhz={m(\"gpu_mhz\"):.0f} cpu_tmax={max(float(x[\"cpu_tmax\"]) for x in r)/1000:.0f}C gpu_tmax={max(float(x[\"gpu_tmax\"]) for x in r)/1000:.0f}C')" $OUT/poll.csv;;
esac
