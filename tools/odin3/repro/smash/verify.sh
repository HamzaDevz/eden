#!/bin/zsh
# verify.sh <outdir> : under the console lock, one boot of Smash with the INI as it is on the console (bench save in,
# Termux AI paused) to check the loaded driver and the per-game settings (LogSettings lines flagged C), then all back.
source ${0:A:h}/env.sh
R=$1; mkdir -p $R
adb shell am force-stop $PKG
echo "ai before: $(ai_pause)"
save_aside $R || { ai_resume; exit 3; }
launch
perl -e 'alarm 150; exec @ARGV' adb logcat -m 1 -e 'Report: Driver' | sed 's/.*Report: /Report: /'
adb logcat -d | grep -E 'LogSettings: .C ' | sed 's/.*LogSettings/LogSettings/' > $R/logsettings.txt; cat $R/logsettings.txt
adb shell am force-stop $PKG
save_back $R
echo "ai after: $(ai_resume | tail -1) termux loop(s)"
adb shell am start -n $PKG/org.yuzu.yuzu_emu.ui.main.MainActivity >/dev/null
adb shell dumpsys activity activities | grep -m1 topResumed
echo "DONE $(date +%T)"
