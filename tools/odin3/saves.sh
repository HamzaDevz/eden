#!/bin/zsh
# SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
# SPDX-License-Identifier: GPL-3.0-or-later

# saves.sh pull|push <name>: copies the game's save folder between PKG's NAND and saves/<name>/, so every
# run starts from the same point instead of wherever the last run's autosave left it. Eden must be stopped.
A=~/Library/Android/sdk/platform-tools/adb
D=${0:A:h}
PKG=${PKG:-com.miHoYo.Yuanshen}; TITLE=${TITLE:-01008CF01BAAC000}
F=/storage/emulated/0/Android/data/$PKG/files
# Builds can share one NAND (the test builds point theirs at the official build's).
N=$($A shell cat $F/config/config.ini | tr -d '\r' | sed -n 's/^nand_directory=//p')
S=${N:-$F/nand}/user/save/0000000000000000/F87F57BFCE6345060247FB61C12F5AE7/$TITLE
case $1 in
  pull) rm -rf $D/saves/$2 && mkdir -p $D/saves && $A pull $S $D/saves/$2 >/dev/null;;
  # adb creates the folders 770 shell:shell, which the app cannot open: the game then panics on its save.
  push) [ -f $D/saves/$2/save.meta ] && $A shell rm -rf $S && $A push $D/saves/$2 $S >/dev/null && $A shell chmod -R 777 $S;;
  *) echo "usage: saves.sh pull|push <name>" >&2; exit 2;;
esac
