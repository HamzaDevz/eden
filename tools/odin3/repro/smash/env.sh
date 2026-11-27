# Smash replay bench (sourced): console paths, Hamza's save moved aside / bench save in, Termux AI pause.
export PATH=$HOME/Library/Android/sdk/platform-tools:$PATH ANDROID_SERIAL=${ANDROID_SERIAL:-192.168.1.13:5555}
SP=${0:A:h}
T=${SP:h:h}
BLOBS=${BLOBS:-$T/runs/smash-blobs}
PKG=com.miHoYo.Yuanshen.relWithDebInfo
F=/storage/emulated/0/Android/data/$PKG/files
P=$F/nand/user/save/0000000000000000/F87F57BFCE6345060247FB61C12F5AE7
S=$P/01006A800016E000
I=$F/config/custom/01006A800016E000.ini
C=$F/cache/shader/01006a800016e000
TV=${TV:-4620250715947099413}
URI='content://com.android.externalstorage.documents/tree/FBDD-1F17%3AEmulations%2FROMs%2Fswitch/document/FBDD-1F17%3AEmulations%2FROMs%2Fswitch%2FSuper%20Smash%20Bros%20Ultimate.xci'
SSHO=(-o ConnectTimeout=8 -o HostName=${ANDROID_SERIAL%:*} odin3)
launch() { adb logcat -c; adb shell am start -a android.intent.action.VIEW -t application/octet-stream -d "\"$URI\"" -n $PKG/org.yuzu.yuzu_emu.activities.EmulationActivity >/dev/null; adb forward tcp:6560 tcp:6560 >/dev/null; }
ai_pause() {  # pause file dated 3 h ahead (termux loop skips while find -mmin -5 matches); remembers whether one existed
  ssh $SSHO 'ls ~/smash-ai/pause ~/smash-ai/train 2>/dev/null; touch -d "$(date -d "+3 hours" "+%Y-%m-%d %H:%M:%S")" ~/smash-ai/pause; pkill -f "^python smash_record"; true'
}
ai_resume() { ssh $SSHO 'rm -f ~/smash-ai/pause ~/smash-ai/train; ls ~/smash-ai/ | tr "\n" " "; echo; ps -ef | grep -c "[t]ermux_ultimus.sh"'; }
save_aside() {
  adb shell "[ -e $S.regress-keep ]" && { echo "leftover $S.regress-keep, abort"; return 3; }
  adb shell "ls -lnd --full-time $S; ls -ln --full-time $P | grep ' 01006A800016E000\$'" > $1/save_before.txt
  adb shell "mv $S $S.regress-keep" || return 3
  adb shell "[ -d $S.bench ] && mv $S.bench $S"; adb shell "ls -lnd $S 2>&1" > $1/bench_in.txt; return 0
}
save_back() {
  adb shell am force-stop $PKG
  adb shell "if [ -e $S ]; then if [ -e $S.bench ]; then mv $S.bench $S.bench-old-\$(date +%s); fi; mv $S $S.bench; fi; [ -d $S.regress-keep ] && mv $S.regress-keep $S"
  adb shell "ls -lnd --full-time $S; ls -ln --full-time $P | grep ' 01006A800016E000\$'" > $1/save_after.txt
  cmp -s $1/save_before.txt $1/save_after.txt && echo "save restored identical" || { echo "SAVE LISTING CHANGED"; diff $1/save_before.txt $1/save_after.txt; }
}
tvshot() { adb exec-out screencap -p -d $TV > $1 && sips -Z 960 -s format jpeg -s formatOptions 60 $1 --out ${1:r}.jpg >/dev/null; }
