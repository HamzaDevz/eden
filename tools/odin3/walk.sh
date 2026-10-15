# SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
# SPDX-License-Identifier: GPL-3.0-or-later

# Runs on the device: sh walk.sh <seconds>. Walks Link back and forth with the left stick.
P=/data/local/tmp/pad.sh
end=$(( $(cut -d. -f1 /proc/uptime) + $1 ))
while [ $(cut -d. -f1 /proc/uptime) -lt $end ]; do
  sh $P stick 0 32767 1200
  sh $P stick 32767 0 800
  sh $P stick 0 -32767 1200
  sh $P stick -32767 0 800
done
