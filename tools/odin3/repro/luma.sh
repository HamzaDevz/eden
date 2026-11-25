#!/bin/zsh
# luma.sh : screenshot of the TV (internal screen when undocked) to tv.png and its mean luma (0-255)
export PATH=$HOME/Library/Android/sdk/platform-tools:$PATH
D=${0:A:h}
ID=$(adb shell dumpsys SurfaceFlinger --display-id | tr -d '\r' | grep -v 'pnpId=QCM' | sed -n 's/^Display \([0-9]*\).*/\1/p' | head -1)
adb exec-out screencap -p ${ID:+-d $ID} > $D/tv.png
ffmpeg -loglevel error -i $D/tv.png -vf "scale=160:-1,signalstats,metadata=print:key=lavfi.signalstats.YAVG:file=-" -f null - | grep -o 'YAVG=[0-9]*'
