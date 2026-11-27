#!/bin/zsh
# sk_ab.sh RUN NAME[:Section.key=value,...] ... : Silksong A/B of per-game INI variants on the TV. Each variant is
# Hamza's INI plus its edits, then sk_live.sh prep (new game, Moss Grotto) + measure (silksong route, 90 s) with
# poll.sh alongside; his save comes back after every run, his INI at the end with its md5 checked.
export PATH=$HOME/Library/Android/sdk/platform-tools:$PATH
A=adb; D=${0:A:h}; T=${D:h}
PKG=com.miHoYo.Yuanshen.relWithDebInfo
I=/storage/emulated/0/Android/data/$PKG/files/config/custom/010013C00E930000.ini
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
trap 'back; exit 4' INT TERM
n=0
for v in ${@:2}; do
  n=$((n + 1)); name=${v%%:*}; edits=(); [[ $v == *:* ]] && edits=(${(s:,:)${v#*:}})
  run=$1-$n-$name
  if [ -n "$edits" ]; then python3 $T/setini.py $W/orig.ini $W/$name.ini $edits || break; else cp $W/orig.ini $W/$name.ini; fi
  $A shell am force-stop $PKG; $A push $W/$name.ini $I >/dev/null
  zsh $D/sk_live.sh $run prep || { echo "$run prep failed"; zsh $D/sk_live.sh $run restore; break; }
  zsh $D/sk_live.sh $run measure
  grep -h 'Driver:' $T/runs/regress/$run/010013C00E930000.sk/logcat.txt | tail -1 | sed 's/.*Driver:/Driver:/'
  echo "== $run ${edits[*]} $(date +%T)"
done
back
