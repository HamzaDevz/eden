#!/bin/zsh
# wonder_ab.sh RUN NAME[:Section.key=value,...] ... : Super Mario Bros. Wonder A/B of per-game INI variants on the TV.
# Each variant is Hamza's INI plus its edits, then wonder.sh: boot on the bench save to the world map (M5, Marais aux
# Népendars under the cursor), MAP_S s standing still on the map, into that level, LEVEL_S s of LEVEL_MODE (right, or a
# regress_input.py mode; red Yoshi takes no damage); his save and INI are put back after each variant and checked, Eden left on MainActivity.
export PATH=$HOME/Library/Android/sdk/platform-tools:$PATH
A=adb; D=${0:A:h}; T=${D:h}
PKG=com.miHoYo.Yuanshen.relWithDebInfo
I=/storage/emulated/0/Android/data/$PKG/files/config/custom/010015100B514000.ini
PRE=${PRE:-c0@5 1@3 1@3 1@3 1@10}
W=$T/runs/regress/$1.ini; mkdir -p $W
$A shell am force-stop $PKG
[ -s $W/orig.ini ] || $A pull $I $W/orig.ini >/dev/null || exit 3
O=$(md5 -q $W/orig.ini)
back() {
  $A shell am force-stop $PKG; $A push $W/orig.ini $I >/dev/null
  [ "$($A shell md5sum $I | cut -c1-32)" = $O ] && echo "ini restored identical" || echo "INI NOT RESTORED"
  $A shell am start -n $PKG/org.yuzu.yuzu_emu.ui.main.MainActivity >/dev/null
}
trap 'zsh $D/wonder.sh $run restore; back; exit 4' INT TERM
n=0
for v in ${@:2}; do
  n=$((n + 1)); name=${v%%:*}; edits=(); [[ $v == *:* ]] && edits=(${(s:,:)${v#*:}})
  run=$1-$n-$name
  if [ -n "$edits" ]; then python3 $T/setini.py $W/orig.ini $W/$name.ini $edits || break; else cp $W/orig.ini $W/$name.ini; fi
  $A shell am force-stop $PKG; $A push $W/$name.ini $I >/dev/null
  zsh $D/wonder.sh $run prep "$PRE" || { echo "$run prep failed, once more"; zsh $D/wonder.sh $run prep "$PRE"; } || { echo "$run prep failed"; break; }
  zsh $D/wonder.sh $run measure map idle ${MAP_S:-30}
  zsh $D/wonder.sh $run enter && sleep 4 && zsh $D/wonder.sh $run measure level ${LEVEL_MODE:-right} ${LEVEL_S:-45}
  zsh $D/wonder.sh $run restore
  echo "== $run ${edits[*]}"
done
back
