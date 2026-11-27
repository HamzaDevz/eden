#!/bin/zsh
# ab.sh <outroot> <name:Section.key=value,...> ... : Smash replay A/B, run as one block under the console lock
# (lockf -k ~/.odin3-console.lock). Hamza's save moved aside for the bench save (two replays in Vault > Videos:
# 1v1 then 8 CPUs, alternating), Termux AI paused. Per session: INI variant + that driver's pipeline blob
# (@TU@ = Turnip R3), cold boot, navreplay.py to the first replay, MATCHES loops measured from 2:18 for 115 s
# (odd = 1v1, even = 8 CPUs). Then INI, save, AI and MainActivity restored; with CAND_EDITS/BASE_EDITS (and
# CANDP/BASEP session prefixes) decide.py picks one, which is pushed, booted once and checked, and left in place.
source ${0:A:h}/env.sh
export TV
R=$1; shift; mkdir -p $R $BLOBS/q $BLOBS/t
MATCHES=${MATCHES:-4}
TU=$F/gpu_drivers/Turnip_v27.0.0-R3.zip
adb shell am force-stop $PKG
echo "ai before: $(ai_pause)"
adb pull $I $R/ini-orig.ini >/dev/null; md5 -q $R/ini-orig.ini > $R/ini.md5
save_aside $R || { ai_resume; exit 3; }
cat $R/bench_in.txt
orig=q; grep -q '^driver_path=.*Turnip' $R/ini-orig.ini && orig=t
adb pull $C/vulkan_pipelines.bin $BLOBS/$orig/vulkan_pipelines.bin >/dev/null
for spec in "$@"; do
  name=${spec%%:*}; edits=${spec#*:}; [ "$edits" = "$spec" ] && edits=
  drv=q; [[ $edits == *@TU@* ]] && drv=t
  O=$R/$name; mkdir -p $O
  cp $R/ini-orig.ini $O/run.ini
  [ -n "$edits" ] && python3 -I $T/setini.py $R/ini-orig.ini $O/run.ini ${(s:,:)${edits//@TU@/$TU}}
  adb push $O/run.ini $I >/dev/null
  if [ -f $BLOBS/$drv/vulkan_pipelines.bin ]; then adb push $BLOBS/$drv/vulkan_pipelines.bin $C/ >/dev/null; else adb shell rm -f $C/vulkan_pipelines.bin; fi
  adb shell dumpsys battery | grep -E 'level|temperature' | tr -d ' ' | tr '\n' ' ' > $O/battery.txt
  echo "== $name ($drv) $(date +%T) $(cat $O/battery.txt)"
  launch
  if python3 -I $SP/navreplay.py $O/nav 600; then
    adb logcat -d | grep -E 'Driver:|LogSettings|Total Pipeline Count|GPU:' | tail -40 > $O/boot.txt
    grep -m1 'Driver:' $O/boot.txt
    for k in $(seq 1 $MATCHES); do
      if python3 -I $SP/align.py $O/m$k-align.png 420 > $O/m$k-align.txt; then
        sips -Z 480 -s format jpeg $O/m$k-align.png --out $O/m$k-align.jpg >/dev/null; rm $O/m$k-align.png*
        echo "m$k $(cat $O/m$k-align.txt)"; NOANALYZE=1 zsh $SP/meas.sh $O/m$k 115
        ( python3 -I $T/regress.py analyze $O/m$k > $O/m$k/analyze.txt 2>&1; python3 -I $SP/stats.py $O/m$k > $O/m$k/stats.txt 2>&1 ) &
      else echo "m$k ABORT no timer"; break; fi
    done
  else echo "nav ABORT"; fi
  adb shell am force-stop $PKG
  adb pull $C/vulkan_pipelines.bin $BLOBS/$drv/vulkan_pipelines.bin >/dev/null 2>&1
  wait; for k in $(seq 1 $MATCHES); do [ -f $O/m$k/stats.txt ] && echo "m$k $(cat $O/m$k/stats.txt) | $(grep -oE 'black=[0-9]+ flick=[0-9]+' $O/m$k/analyze.txt)"; done
done
FIN=$R/ini-orig.ini; fd=$orig
if [ -n "$BASE_EDITS" ]; then
  pick=$(python3 -I $SP/decide.py $R $CANDP $BASEP 2>$R/decide.txt); cat $R/decide.txt; echo "pick: $pick"
  [ "$pick" = cand ] && fe=$CAND_EDITS || fe=$BASE_EDITS
  fd=q; [[ $fe == *@TU@* ]] && fd=t
  FIN=$R/final.ini
  python3 -I $T/setini.py $R/ini-orig.ini $FIN ${(s:,:)${fe//@TU@/$TU}}
  adb push $FIN $I >/dev/null
  adb push $BLOBS/$fd/vulkan_pipelines.bin $C/ >/dev/null
  launch
  perl -e 'alarm 150; exec @ARGV' adb logcat -m 1 -e 'Report: Driver' | cut -c1-200
  adb logcat -d | grep -E 'LogSettings: .C ' | sed 's/.*LogSettings/LogSettings/' > $R/final-logsettings.txt; cat $R/final-logsettings.txt
  adb shell am force-stop $PKG
  adb pull $C/vulkan_pipelines.bin $BLOBS/$fd/vulkan_pipelines.bin >/dev/null 2>&1
fi
adb push $BLOBS/$fd/vulkan_pipelines.bin $C/ >/dev/null
save_back $R
adb push $FIN $I >/dev/null; [ "$(adb shell md5sum $I | cut -c1-32)" = "$(md5 -q $FIN)" ] && echo "ini on console: ${FIN:t}" || echo "INI NOT AS EXPECTED"
echo "ai after: $(ai_resume | tail -1) termux loop(s)"
adb shell am start -n $PKG/org.yuzu.yuzu_emu.ui.main.MainActivity >/dev/null
adb shell dumpsys activity activities | grep -m1 topResumed
echo "DONE $(date +%T)"
