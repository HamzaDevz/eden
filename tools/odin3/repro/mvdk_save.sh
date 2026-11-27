#!/bin/zsh
# mvdk_save.sh aside|back OUTDIR : Mario vs. Donkey Kong (0100B99019412000) bench save handling, like regress.sh.
#   aside : Eden stopped, Hamza's save listed then renamed to .regress-keep, a copy of it put in its place
#   back  : Eden stopped, the copy deleted (or set aside when Eden wrote it), his save renamed back, listing compared
export PATH=$HOME/Library/Android/sdk/platform-tools:$PATH
A=adb; PKG=com.miHoYo.Yuanshen.relWithDebInfo
S=/storage/emulated/0/Android/data/$PKG/files/nand/user/save/0000000000000000/F87F57BFCE6345060247FB61C12F5AE7/0100B99019412000
O=$2; mkdir -p $O
$A shell am force-stop $PKG
if [ $1 = aside ]; then
  $A shell "[ -d $S ] && [ ! -e $S.regress-keep ]" || { echo "save state unexpected, abort"; exit 3; }
  $A shell "ls -lnR $S 2>&1" > $O/save_before.txt
  $A shell "mv $S $S.regress-keep && cp -r $S.regress-keep $S && chmod -R 777 $S" || { echo "copy failed"; exit 3; }
else
  $A shell "[ -d $S.regress-keep ]" && [ -s $O/save_before.txt ] || { echo "no kept save / listing, abort"; exit 3; }
  $A shell "rm -rf $S 2>/dev/null; if [ -e $S ]; then mv $S $S.regress-trash-\$(date +%s) || exit 1; fi; mv $S.regress-keep $S"
  $A shell "ls -lnR $S 2>&1" > $O/save_after.txt
  cmp -s $O/save_before.txt $O/save_after.txt && echo "save restored identical" || { echo "SAVE LISTING CHANGED"; diff $O/save_before.txt $O/save_after.txt | head; exit 3; }
fi
