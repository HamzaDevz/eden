# SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
# SPDX-License-Identifier: GPL-3.0-or-later

import csv, sys, os

out = sys.argv[1]

# FPS and frame pacing from SurfaceFlinger present timestamps. Each dump lists the layer's last 128 presents; two
# presents are consecutive frames only when one dump lists them next to each other, so the time between two dumps
# that do not overlap is a hole in the record, however long, and a long stall inside a dump is a real frame.
dumps = [[]]
for line in open(os.path.join(out, "sf_latency.txt")):
    if line.startswith("@"):
        dumps.append([])
        continue
    p = line.split()
    if len(p) == 3 and p[1].isdigit() and 0 < int(p[1]) < 9e18:
        dumps[-1].append(int(p[1]))
pairs = set()
for dump in dumps:
    s = sorted(set(dump))
    pairs.update(zip(s, s[1:]))
ts = sorted({t for dump in dumps for t in dump})
frames = [(a, (b - a) / 1e6) for a, b in zip(ts, ts[1:]) if (a, b) in pairs]  # (start, ms on screen)

# A frame on screen for 3 vsyncs of the 120 Hz panel or more missed the 60 fps target: the game either ran late or
# switched itself to 30 fps. A drop is a run of such frames.
SLOW_MS = 1.25 * 1000 / 60


def pacing(fr, span_s):
    """span_s: how long the frames should cover; seen= is the part of it the dumps did cover."""
    d = [ms for _, ms in fr]
    if not d:
        return "fps=0 frames=0"
    slow = [x > SLOW_MS for x in d]
    drops, longest, run = 0, 0.0, 0.0
    for x, late_frame in zip(d, slow):
        drops += late_frame and run == 0
        run = run + x if late_frame else 0.0
        longest = max(longest, run)
    late = sum(x for x in d if x > SLOW_MS)
    return (f"fps={len(d) / (sum(d) / 1000):.1f} below60={100 * late / sum(d):.1f}% drops={drops} longest_drop_ms={longest:.0f} "
            f"slow_frames={sum(slow)} ({100 * sum(slow) / len(d):.1f}%) worst_ms={max(d):.0f} "
            f"p99_frame_ms={sorted(d)[int(len(d) * 0.99) - 1]:.1f} frames={len(d)} seen={min(100, sum(d) / 10 / span_s):.0f}%")


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

print(pacing(frames, (ts[-1] - ts[0]) / 1e9 if ts else 1))
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

# Scenario runs: whether the route held, then each segment play.py marked, from the same presents and polls.
if os.path.exists(os.path.join(out, "route.txt")):
    print(open(os.path.join(out, "route.txt")).read().strip())
if os.path.exists(os.path.join(out, "segments.csv")):
    for r in csv.DictReader(open(os.path.join(out, "segments.csv"))):
        m0, m1 = float(r["mono_start"]) * 1e9, float(r["mono_end"]) * 1e9
        b0, b1 = float(r["boot_start"]), float(r["boot_end"])
        pw = [w for row, w in zip(rows, power) if b0 <= float(row["t"]) <= b1]
        print(f"  {r['segment']:<12s} {b1 - b0:4.0f}s {pacing([f for f in frames if m0 <= f[0] < m1], b1 - b0)} "
              f"power_w={sum(pw) / len(pw) if pw else 0:.2f}")
