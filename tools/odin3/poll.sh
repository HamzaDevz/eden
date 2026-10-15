# SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
# SPDX-License-Identifier: GPL-3.0-or-later

# Runs on the device: sh poll.sh <seconds>
K=/sys/class/kgsl/kgsl-3d0; B=/sys/class/power_supply/battery; P=/sys/devices/system/cpu/cpufreq
U=/sys/class/power_supply/usb
M=/sys/devices/system/cpu/bus_dcvs
CZ=""; GZ=""; SK=""
for z in /sys/class/thermal/thermal_zone*; do
  t=$(cat $z/type 2>/dev/null)
  case $t in cpu-*) CZ="$CZ $z/temp";; gpuss-*) GZ="$GZ $z/temp";; sys-therm-7) SK=$z/temp;; esac
done
echo t,gpu_busy,gpu_mhz,gpu_temp,thermal_pwrlevel,throttling,c0_mhz,c6_mhz,cpu_tmax,gpu_tmax,skin,batt_ma,batt_mv,batt_temp,ddr_mhz,llcc_mhz,usb_ma,usb_mv
i=0
while [ $i -lt $1 ]; do
  u=$(cut -d" " -f1 /proc/uptime)
  gb=$(cut -d" " -f1 $K/gpu_busy_percentage)
  gc=$(( $(cat $K/gpuclk) / 1000000 ))
  c0=$(( $(cat $P/policy0/scaling_cur_freq) / 1000 ))
  c6=$(( $(cat $P/policy6/scaling_cur_freq) / 1000 ))
  ctm=$(cat $CZ 2>/dev/null | sort -n | tail -1)
  gtm=$(cat $GZ 2>/dev/null | sort -n | tail -1)
  echo $u,$gb,$gc,$(cat $K/temp),$(cat $K/thermal_pwrlevel),$(cat $K/throttling),$c0,$c6,$ctm,$gtm,$(cat $SK),$(( $(cat $B/current_now) / 1000 )),$(( $(cat $B/voltage_now) / 1000 )),$(cat $B/temp),$(( $(cat $M/DDR/cur_freq) / 1000 )),$(( $(cat $M/LLCC/cur_freq) / 1000 )),$(( $(cat $U/online) * $(cat $U/current_now) / 1000 )),$(( $(cat $U/voltage_now) / 1000 ))
  sleep 1
  i=$((i+1))
done
