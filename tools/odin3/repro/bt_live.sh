#!/bin/zsh
# bt_live.sh RUN prep|measure|restore : new game (Hamza's save renamed away like regress.sh), A until the beach field, route.sh +
# recover.sh to the first battle, then 120 s of TV capture + SurfaceFlinger while mashing A; save put back and checked.
export PATH=$HOME/Library/Android/sdk/platform-tools:$PATH
A=adb; D=${0:A:h}; T=~/PhpstormProjects/eden/tools/odin3
PKG=com.miHoYo.Yuanshen.relWithDebInfo
F=/storage/emulated/0/Android/data/$PKG/files
P=$F/nand/user/save/0000000000000000/F87F57BFCE6345060247FB61C12F5AE7
S=$P/01006D0017F7A000
OUT=$T/runs/regress/$1/01006D0017F7A000.battle; [ $2 = prep ] && rm -rf $OUT; mkdir -p $OUT
U='content://com.android.externalstorage.documents/tree/FBDD-1F17%3AEmulations%2FROMs%2Fswitch/document/FBDD-1F17%3AEmulations%2FROMs%2Fswitch%2FMario%20and%20Luigi%20Brothership%20%5B01006D0017F7A000%5D%5Bv0%5D%5BUS%5D.nsp'
if [ $2 = prep ]; then
$A shell dumpsys activity activities | grep -q 'topResumedActivity.*EmulationActivity' && { echo "game open, not now"; exit 2; }
$A shell "[ -d $S ] && [ ! -e $S.regress-keep ]" || { echo "save state unexpected, abort"; exit 3; }
$A shell am force-stop $PKG
$A shell "ls -lnR $S 2>&1" > $OUT/save_before.txt
$A shell "mv $S $S.regress-keep" || exit 3
else
$A shell "[ -d $S.regress-keep ]" && [ -s $OUT/save_before.txt ] || { echo "no kept save / listing, abort"; exit 3; }
fi
restore() {
  $A shell am force-stop $PKG
  $A shell "rm -rf $S 2>/dev/null; if [ -e $S ]; then mv $S $S.regress-trash-$(date +%s) || exit 1; fi; mv $S.regress-keep $S"
  $A shell "ls -lnR $S 2>&1" > $OUT/save_after.txt
  cmp -s $OUT/save_before.txt $OUT/save_after.txt && echo "save restored identical" || { echo "SAVE LISTING CHANGED"; diff $OUT/save_before.txt $OUT/save_after.txt | head; }
  $A shell am start -n $PKG/org.yuzu.yuzu_emu.ui.main.MainActivity >/dev/null
}
[ $2 = restore ] && { restore; exit 0; }
if [ $2 = prep ]; then
trap 'restore; exit 4' INT TERM
$A logcat -c
$A shell am start -a android.intent.action.VIEW -t application/octet-stream -d "\"$U\"" -n $PKG/org.yuzu.yuzu_emu.activities.EmulationActivity >/dev/null
$A forward tcp:6560 tcp:6560 >/dev/null
sleep 40
# A until the field hint shows (checked every 5th press after 150 s), at most 300 s
t0=$SECONDS; i=0; field=0
while [ $((SECONDS - t0)) -lt 300 ]; do
  python3 $T/bridge.py pad 0 1 0 0 0 0 120 >/dev/null; python3 $T/bridge.py pad 0 0 0 0 0 0 50 >/dev/null; sleep 1.1
  i=$((i + 1))
  if [ $((SECONDS - t0)) -gt 150 ] && [ $((i % 5)) -eq 0 ]; then
    zsh $D/luma.sh >/dev/null; sips -Z 540 $D/tv.png --out $D/tvs.png >/dev/null
    python3 $D/infield.py $D/tvs.png && { field=1; break; }
  fi
done
echo "field=$field after $((SECONDS - t0)) s"; cp $D/tvs.png $OUT/field.png
[ $field = 1 ] || { restore; exit 5; }
[ -n "$NOROUTE" ] || zsh $D/route.sh; zsh $D/luma.sh >/dev/null; sips -Z 540 $D/tv.png --out $D/tvs.png >/dev/null; cp $D/tvs.png $OUT/after_route.png; echo "left in field, game running, save aside"; exit 0
fi
R=battle
if [ $2 = measure ]; then
  DISP=$($A shell dumpsys SurfaceFlinger --display-id | tr -d '\r' | grep -v 'pnpId=QCM' | sed -n 's/^Display \([0-9]*\).*/\1/p' | head -1)
  L=$($A shell "dumpsys SurfaceFlinger --list" | tr -d '\r' | grep -oE 'SurfaceView\[[^]]*EmulationActivity\]\(BLAST\)#[0-9]+' | head -1)
  $A shell "screenrecord ${DISP:+--display-id $DISP} --time-limit 122 --size 1920x1080 --bit-rate 20000000 /data/local/tmp/regress.mp4" &
  python3 $T/regress_input.py mash 120 &
  t1=$SECONDS
  while [ $((SECONDS - t1)) -lt 120 ]; do echo @; $A shell "dumpsys SurfaceFlinger --latency '$L'"; sleep 1; done > $OUT/sf.txt
  wait
  $A logcat -d | grep -E 'Fatal signal|PANIC|Assertion|rejected|Out of memory|Driver:|GPU:' | tail -50 > $OUT/logcat.txt
  $A pull /data/local/tmp/regress.mp4 $OUT/ >/dev/null && $A shell rm /data/local/tmp/regress.mp4
  ffmpeg -v error -y -i $OUT/regress.mp4 -c copy -bsf:v h264_mp4toannexb -f h264 $OUT/tv.h264 && rm $OUT/regress.mp4
fi
restore
if [ $2 = measure ]; then
  printf '{"title": "01006D0017F7A000", "name": "Mario and Luigi Brothership", "secs": 120, "alive": 1, "save": "none", "build": "live", "mode": "battle"}\n' > $OUT/meta.json
  python3 $T/regress.py analyze $OUT
fi
