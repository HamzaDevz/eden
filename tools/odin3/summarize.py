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
print(f"power_w avg={sum(power)/len(power):.2f} max={max(power):.2f}" if power else "power_w n/a")
print(f"gpu busy={avg('gpu_busy'):.0f}% clk={avg('gpu_mhz'):.0f}MHz  cpu0={avg('c0_mhz'):.0f}MHz cpu6={avg('c6_mhz'):.0f}MHz")
print(f"temp gpu_max={mx('gpu_tmax')/1000:.1f}C cpu_max={mx('cpu_tmax')/1000:.1f}C skin_end={float(rows[-1]['skin'])/1000:.1f}C batt_end={float(rows[-1]['batt_temp'])/10:.1f}C thermal_pwrlevel_max={mx('thermal_pwrlevel'):.0f}")
print("threads %cpu (sys%): " + ", ".join(f"{n}:{c:.0f}({s:.0f})" for c, s, n, _ in top[:8]))
