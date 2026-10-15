# SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
# SPDX-License-Identifier: GPL-3.0-or-later

# Runs on the device. Injects events into the Odin Controller.
#   sh pad.sh btn <a|b|x|y|l|r|zl|zr|minus|plus|home|up|down|left|right> [hold_ms]
#   sh pad.sh stick <x -32767..32767> <y> <hold_ms>      (left stick, y<0 = up)
#   sh pad.sh rstick <x> <y> <hold_ms>
E=/dev/input/event8
syn() { sendevent $E 0 0 0; }
ms() { usleep $(( $1 * 1000 )); }
case $1 in
  btn)
    case $2 in
      a) k=304;; b) k=305;; x) k=307;; y) k=308;; l) k=310;; r) k=311;; zl) k=312;; zr) k=313;;
      minus) k=314;; plus) k=315;; home) k=316;;
      up) h=17; v=-1;; down) h=17; v=1;; left) h=16; v=-1;; right) h=16; v=1;;
    esac
    if [ -n "$h" ]; then
      sendevent $E 3 $h $v; syn; ms ${3:-120}; sendevent $E 3 $h 0; syn
    else
      sendevent $E 1 $k 1; syn; ms ${3:-120}; sendevent $E 1 $k 0; syn
    fi;;
  stick|rstick)
    if [ $1 = stick ]; then ax=0; ay=1; else ax=2; ay=5; fi
    sendevent $E 3 $ax $2; sendevent $E 3 $ay $3; syn; ms $4
    sendevent $E 3 $ax 0; sendevent $E 3 $ay 0; syn;;
esac
