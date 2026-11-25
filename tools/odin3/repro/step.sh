#!/bin/zsh
# step.sh BUTTONS_HEX HOLD_MS WAIT_S [LX LY] : press, wait, TV screenshot to tvs.png + luma
D=${0:A:h}; B=/Users/hamzatei/PhpstormProjects/eden/tools/odin3/bridge.py
python3 $B pad 0 $1 ${4:-0} ${5:-0} 0 0 $2 >/dev/null; python3 $B pad 0 0 0 0 0 0 50 >/dev/null
sleep $3; $D/luma.sh; sips -Z 540 $D/tv.png --out $D/tvs.png >/dev/null
