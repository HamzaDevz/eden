#!/bin/zsh
# luma.sh : TV screenshot to tv.png and its mean luma (0-255)
export PATH=$HOME/Library/Android/sdk/platform-tools:$PATH
D=${0:A:h}
adb exec-out screencap -p -d 4620250715947099413 > $D/tv.png
ffmpeg -loglevel error -i $D/tv.png -vf "scale=160:-1,signalstats,metadata=print:key=lavfi.signalstats.YAVG:file=-" -f null - | grep -o 'YAVG=[0-9]*'
