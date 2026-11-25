#!/bin/zsh
# trk.sh start|restore : Brothership on a working copy of Hamza's save (original moved to .trk-keep, put back
# untouched by restore and checked against its listing; the copy comes from saves/brothership-hamza-20261124 (Eden save export, slot 2 = 24/11 19:59); start launches the game by URI.
export PATH=$HOME/Library/Android/sdk/platform-tools:$PATH
A=adb; D=${0:A:h}
PKG=com.miHoYo.Yuanshen.relWithDebInfo
F=/storage/emulated/0/Android/data/$PKG/files
S=$F/nand/user/save/0000000000000000/F87F57BFCE6345060247FB61C12F5AE7/01006D0017F7A000
U='content://com.android.externalstorage.documents/tree/FBDD-1F17%3AEmulations%2FROMs%2Fswitch/document/FBDD-1F17%3AEmulations%2FROMs%2Fswitch%2FMario%20and%20Luigi%20Brothership%20%5B01006D0017F7A000%5D%5Bv0%5D%5BUS%5D.nsp'
if [ $1 = start ]; then
  $A shell "[ -d $S ] && [ ! -e $S.trk-keep ]" || { echo "save state unexpected, abort"; exit 3; }
  $A shell am force-stop $PKG
  $A shell "ls -lnR $S 2>&1" > $D/trk_save_before.txt
  $A shell "mv $S $S.trk-keep && mkdir $S && chmod 777 $S" || exit 3
  for f in $D/../saves/brothership-hamza-20261124/01006D0017F7A000/*; do $A push $f $S/ >/dev/null || exit 3; done
  $A shell "ls -l $S" | tail -3
  $A logcat -c
  $A shell am start -a android.intent.action.VIEW -t application/octet-stream -d "\"$U\"" -n $PKG/org.yuzu.yuzu_emu.activities.EmulationActivity >/dev/null
  $A forward tcp:6560 tcp:6560 >/dev/null
  echo started
elif [ $1 = restore ]; then
  $A shell am force-stop $PKG
  $A shell "[ -d $S.trk-keep ]" || { echo "no kept save"; exit 3; }
  $A shell "rm -rf $S 2>/dev/null; if [ -e $S ]; then mv $S $S.trk-trash-$(date +%s) || exit 1; fi; mv $S.trk-keep $S"
  $A shell "ls -lnR $S 2>&1" > $D/trk_save_after.txt
  cmp -s $D/trk_save_before.txt $D/trk_save_after.txt \
    && echo "save restored identical" || { echo "SAVE CHANGED"; diff $D/trk_save_before.txt $D/trk_save_after.txt | head; }
  $A shell am start -n $PKG/org.yuzu.yuzu_emu.ui.main.MainActivity >/dev/null
fi
