#!/bin/zsh
# bt_ab.sh RUN NAME[:Section.key=value,...] ... : Brothership A/B of per-game INI variants on the TV. Each variant is
# Hamza's INI plus its edits, then bt_live.sh NOROUTE=1 prep + ROUTE=1 measure (field walk, battle start, battle)
# with poll.sh (GPU busy/clock, temps) sampled alongside; his INI is put back at the end and its md5 checked.
export PATH=$HOME/Library/Android/sdk/platform-tools:$PATH
A=adb; D=${0:A:h}; T=${D:h}
PKG=com.miHoYo.Yuanshen.relWithDebInfo
I=/storage/emulated/0/Android/data/$PKG/files/config/custom/01006D0017F7A000.ini
W=$T/runs/regress/$1.ini; mkdir -p $W
$A shell am force-stop $PKG
[ -s $W/orig.ini ] || $A pull $I $W/orig.ini >/dev/null || exit 3
O=$(md5 -q $W/orig.ini)
back() {
  $A shell am force-stop $PKG; $A push $W/orig.ini $I >/dev/null
  [ "$($A shell md5sum $I | cut -c1-32)" = $O ] && echo "ini restored identical" || echo "INI NOT RESTORED"
  $A shell am start -n $PKG/org.yuzu.yuzu_emu.ui.main.MainActivity >/dev/null
}
trap 'back; exit 4' INT TERM
$A push $T/poll.sh /data/local/tmp/poll.sh >/dev/null
n=0
for v in ${@:2}; do
  n=$((n + 1)); name=${v%%:*}; edits=(); [[ $v == *:* ]] && edits=(${(s:,:)${v#*:}})
  run=$1-$n-$name; out=$T/runs/regress/$run/01006D0017F7A000.battle
  if [ -n "$edits" ]; then python3 $T/setini.py $W/orig.ini $W/$name.ini $edits || break; else cp $W/orig.ini $W/$name.ini; fi
  $A shell am force-stop $PKG; $A push $W/$name.ini $I >/dev/null
  NOROUTE=1 zsh $D/bt_live.sh $run prep || { echo "$run prep failed"; break; }
  $A shell "sh /data/local/tmp/poll.sh 120 > /data/local/tmp/poll.csv" &
  ROUTE=1 zsh $D/bt_live.sh $run measure
  wait
  $A pull /data/local/tmp/poll.csv $out/poll.csv >/dev/null
  python3 -c "import csv,sys; r=list(csv.DictReader(open(sys.argv[1]))); m=lambda k: sum(float(x[k]) for x in r)/len(r)
print(f'gpu_busy={m(\"gpu_busy\"):.0f}% gpu_mhz={m(\"gpu_mhz\"):.0f} cpu_tmax={max(float(x[\"cpu_tmax\"]) for x in r)/1000:.0f}C gpu_tmax={max(float(x[\"gpu_tmax\"]) for x in r)/1000:.0f}C')" $out/poll.csv
  echo "== $run ${edits[*]}"
done
back
