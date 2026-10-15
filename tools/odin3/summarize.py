# SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
# SPDX-License-Identifier: GPL-3.0-or-later

import csv, sys, os, statistics as st

out = sys.argv[1]

# FPS and frame pacing from SurfaceFlinger present timestamps (deduplicated across dumps).
presents = set()
for line in open(os.path.join(out, "sf_latency.txt")):
    p = line.split()
    if len(p) == 3 and p[1].isdigit():
        v = int(p[1])
        if 0 < v < 9e18:
            presents.add(v)
ts = sorted(presents)
fps = slow = p99 = 0; d = []
if len(ts) > 2:
    d = [(b - a) / 1e6 for a, b in zip(ts, ts[1:])]
    # Gaps longer than 200 ms are holes between SF dumps, not frames.
    d = [x for x in d if x < 200]
    fps = len(d) / (sum(d) / 1000)
    ds = sorted(d)
    p99 = ds[int(len(ds) * 0.99) - 1]
    slow = sum(1 for x in d if x > 1.5 * st.median(d))

rows = list(csv.DictReader(open(os.path.join(out, "poll.csv"))))
def avg(k, f=float):
    v = [f(r[k]) for r in rows if r.get(k) not in (None, "")]
    return sum(v) / len(v) if v else 0
def mx(k):
    v = [float(r[k]) for r in rows if r.get(k) not in (None, "")]
    return max(v) if v else 0
power = [(-float(r["batt_ma"])) * float(r["batt_mv"]) / 1e6 for r in rows if r.get("batt_ma")]
# On the charger the battery only takes what the device leaves of the USB input, so the device draws
# input minus charge (the charger's own losses are not counted: compare runs, not absolute values).
usb = [float(r["usb_ma"]) * float(r["usb_mv"]) / 1e6 for r in rows if r.get("usb_ma")]
charging = len(usb) == len(power) and sum(usb) > 0
if charging:
    power = [u + p for u, p in zip(usb, power)]

# Per-thread CPU over the run from /proc/<pid>/task/*/stat (utime+stime, 100 Hz ticks).
def threads(fn):
    t = {}
    for line in open(os.path.join(out, fn)):
        if "(" not in line:
            continue
        tid = line.split()[0]
        name = line[line.index("(") + 1:line.rindex(")")]
        f = line[line.rindex(")") + 2:].split()
        t[tid] = (name, int(f[11]), int(f[12]))
    return t
a, b = threads("threads_start.txt"), threads("threads_end.txt")
secs = len(rows) or 1
top = []
for tid, (name, u, s) in b.items():
    u0, s0 = (a[tid][1], a[tid][2]) if tid in a else (0, 0)
    top.append(((u - u0 + s - s0) / secs, (s - s0) / secs, name, tid))
top.sort(reverse=True)

print(f"fps={fps:.1f} p99_frame_ms={p99:.1f} slow_frames={slow} ({100*slow/max(len(d),1):.1f}%) frames={len(ts)}")
print(f"power_w avg={sum(power)/len(power):.2f} max={max(power):.2f}{' (charging: usb in - battery in)' if charging else ''}" if power else "power_w n/a")
print(f"gpu busy={avg('gpu_busy'):.0f}% clk={avg('gpu_mhz'):.0f}MHz  cpu0={avg('c0_mhz'):.0f}MHz cpu6={avg('c6_mhz'):.0f}MHz")
print(f"temp gpu_max={mx('gpu_tmax')/1000:.1f}C cpu_max={mx('cpu_tmax')/1000:.1f}C skin_end={float(rows[-1]['skin'])/1000:.1f}C batt_end={float(rows[-1]['batt_temp'])/10:.1f}C thermal_pwrlevel_max={mx('thermal_pwrlevel'):.0f}")
print("threads %cpu (sys%): " + ", ".join(f"{n}:{c:.0f}({s:.0f})" for c, s, n, _ in top[:8]))
print(f"bus ddr={avg('ddr_mhz'):.0f}MHz llcc={avg('llcc_mhz'):.0f}MHz")

# System-wide view from /proc/stat and every process, when measure.sh captured them.
def procs(fn):
    cpus, p = {}, {}
    for line in open(os.path.join(out, fn)):
        f = line.split()
        if f and f[0].startswith("cpu"):
            v = list(map(int, f[1:8]))
            cpus[f[0]] = (sum(v), v[3] + v[4])
        elif "(" in line:
            name = line[line.index("(") + 1:line.rindex(")")]
            g = line[line.rindex(")") + 2:].split()
            p[f[0]] = (name, int(g[11]) + int(g[12]))
    return cpus, p
if os.path.exists(os.path.join(out, "procs_end.txt")):
    (c0, p0), (c1, p1) = procs("procs_start.txt"), procs("procs_end.txt")
    busy = {k: 100 * (1 - (c1[k][1] - c0[k][1]) / max(c1[k][0] - c0[k][0], 1)) for k in c1 if k in c0 and k != "cpu"}
    elapsed = (c1["cpu"][0] - c0["cpu"][0]) / max(len(busy), 1)
    print("cpu busy%: " + " ".join(f"{k[3:]}:{v:.0f}" for k, v in sorted(busy.items(), key=lambda kv: int(kv[0][3:]))))
    pt = sorted(((t - p0.get(pid, (n, 0))[1]) * 100 / max(elapsed, 1), n) for pid, (n, t) in p1.items())[::-1]
    print("procs %cpu: " + ", ".join(f"{n}:{c:.0f}" for c, n in pt[:8]))
