#!/bin/zsh
# cache.sh keep|cold|restore [TITLEID] : test runs on an empty shader cache without losing Hamza's.
# keep : the title's cache dir moved to .cache-keep (listing saved); cold : the test cache Eden wrote is moved
# to a trash dir (shell cannot delete the app's files) so the next launch compiles everything again;
# restore : test cache out, kept cache back, listing compared. Eden must be closed (force-stopped here).
export PATH=$HOME/Library/Android/sdk/platform-tools:$PATH
A=adb; D=${0:A:h}; T=${2:-01006D0017F7A000}
PKG=com.miHoYo.Yuanshen.relWithDebInfo
C=/storage/emulated/0/Android/data/$PKG/files/cache/shader/$T
$A shell am force-stop $PKG
out() { $A shell "if [ -e $C ]; then rm -rf $C 2>/dev/null; [ -e $C ] && mv $C $C.cache-trash-\$(date +%s); fi; true"; }
case $1 in
keep)
  $A shell "[ -d $C ] && [ ! -e $C.cache-keep ]" || { echo "cache state unexpected, abort"; exit 3; }
  $A shell "ls -ln $C" > $D/cache_before.txt
  $A shell "mv $C $C.cache-keep" && echo "cache kept, next launch starts empty";;
cold)
  $A shell "[ -d $C.cache-keep ]" || { echo "no kept cache, abort"; exit 3; }
  out; echo "test cache cleared";;
restore)
  $A shell "[ -d $C.cache-keep ]" || { echo "no kept cache, abort"; exit 3; }
  out; $A shell "mv $C.cache-keep $C"
  $A shell "ls -ln $C" > $D/cache_after.txt
  cmp -s $D/cache_before.txt $D/cache_after.txt && echo "cache restored identical" \
    || { echo "CACHE LISTING CHANGED"; diff $D/cache_before.txt $D/cache_after.txt | head; };;
esac
