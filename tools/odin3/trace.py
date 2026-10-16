# SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
# SPDX-License-Identifier: GPL-3.0-or-later

# trace.py <outdir>: reads the atrace.txt run_variant.sh records with TRACE=1 and explains the drops to 30 fps.
# The game queues a frame with swap interval 1 at 60 fps and switches to 2 for a while after a frame it thinks was
# late. For every switch, the frames before it are compared with ordinary 60 fps frames: running time of each Eden
# thread, time in each of Eden's trace sections (common/trace.h), CPUs and their clocks. Writes trace.txt.
import bisect, collections, os, re, sys

out = sys.argv[1]
LINE = re.compile(r"^\s*(.*)-(\d+)\s+\(\s*[\d-]*\)\s+\[(\d+)\]\s+\S+\s+([\d.]+):\s+(\w+):\s+(.*)$")
SWITCH = re.compile(r"prev_comm=(.*) prev_pid=(\d+) .* prev_state=(\S+) ==> next_comm=(.*) next_pid=(\d+) ")

names = {}  # tid -> thread name
running = {}  # cpu -> (tid, since)
runs = collections.defaultdict(list)  # tid -> [(start, end, cpu)]
stacks = collections.defaultdict(list)  # tid -> open sections [(name, start)]
sections = []  # (tid, name, start, end)
queues = []  # (time, swap interval)
freq = collections.defaultdict(list)  # cpu -> [(time, kHz)]
pending = {}  # (name, cookie) -> start of an async section, which may end on another thread
swap, pid = 1, None
with open(os.path.join(out, "atrace.txt"), errors="replace") as f:
    for line in f:
        m = LINE.match(line)
        if not m:
            continue
        comm, tid, cpu, ts, event, payload = m.group(1), int(m.group(2)), int(m.group(3)), float(m.group(4)), \
            m.group(5), m.group(6)
        if event == "sched_switch":
            s = SWITCH.search(payload)
            if not s:
                continue
            prev, nxt = int(s.group(2)), int(s.group(5))
            names[prev], names[nxt] = s.group(1), s.group(4)
            if cpu in running and running[cpu][0] == prev:
                runs[prev].append((running[cpu][1], ts, cpu))
            running[cpu] = (nxt, ts)
        elif event == "cpu_frequency":
            kv = dict(p.split("=") for p in payload.split())
            freq[int(kv["cpu_id"])].append((ts, int(kv["state"])))
        elif event == "tracing_mark_write":
            p = payload.split("|")
            if p[0] == "B" and len(p) >= 3:
                stacks[tid].append((p[2], ts))
                if p[2] == "Queue":
                    pid = int(p[1])
                    queues.append([ts, swap])
            elif p[0] == "E" and stacks[tid]:
                name, start = stacks[tid].pop()
                sections.append((tid, name, start, ts))
            elif p[0] in ("S", "F") and len(p) >= 4:
                if p[0] == "S":
                    pending[(p[2], p[3])] = ts
                elif (p[2], p[3]) in pending:
                    sections.append(("async", p[2], pending.pop((p[2], p[3])), ts))
            elif p[0] == "C" and len(p) >= 4 and p[2] == "swap_interval":
                swap = int(p[3])
                if queues:
                    queues[-1][1] = swap  # the counter follows the Queue section it belongs to

if len(queues) < 10:
    sys.exit(f"trace: only {len(queues)} frames queued, is the build instrumented and the app traced?")
t0, t1 = queues[0][0], queues[-1][0]


def clock(cpu, t):
    v = 0
    for ts, khz in freq.get(cpu, []):
        if ts > t:
            break
        v = khz
    return v // 1000


# Only Eden's threads: the named emulator threads.
own = re.compile(r"^(CPUCore_[\dN]|GPU|VulkanWorker|VulkanPresent|HostTiming|VSyncThread|VkPipelineBuil|"
                 r"ServiceThread|nvnflinger|DSP|FS|yuzu)")
# Sorted run and section intervals with their ends, so a window only visits what overlaps it.
tracks = []
for tid, rs in runs.items():
    name = names.get(tid, "?")
    if own.match(name):
        rs.sort()
        tracks.append(("t", re.sub(r"\d+$", "N", name) if name.startswith("CPUCore") else name, rs, [r[1] for r in rs]))
by_section = collections.defaultdict(list)
for tid, name, s, e in sections:
    by_section[(f"{name}@{names.get(tid, tid)}", tid)].append((s, e, -1))
for (key, _), rs in by_section.items():
    rs.sort()
    tracks.append(("s", key, rs, [r[1] for r in rs]))


def profile(a, b):
    """Running ms per thread name and ms per section name inside [a, b]."""
    th, se, cpus = collections.Counter(), collections.Counter(), collections.defaultdict(collections.Counter)
    for kind, name, rs, ends in tracks:
        for k in range(bisect.bisect_right(ends, a), len(rs)):
            s, e, c = rs[k]
            if s >= b:
                break
            o = (min(e, b) - max(s, a)) * 1000
            if o <= 0:
                continue  # a section that closed before a: lost events leave the ends out of order
            if kind == "t":
                th[name] += o
                cpus[name][c] += o
            else:
                se[name] += o
    return th, se, cpus


# Frames: interval between two queued frames; a switch is a frame queued with 2 after one queued with 1.
frames = [(queues[k][0] - queues[k - 1][0], queues[k - 1][0], queues[k][0], queues[k][1]) for k in range(1, len(queues))]
normal = [f for f in frames if f[3] == 1 and 0.0150 < f[0] < 0.0185]
switches = [k for k in range(1, len(queues)) if queues[k - 1][1] == 1 and queues[k][1] == 2]

base_th, base_se = collections.Counter(), collections.Counter()
for dt, a, b, _ in normal:
    th, se, _ = profile(a, b)
    base_th.update(th)
    base_se.update(se)
n = max(len(normal), 1)
lines = [f"trace: frames={len(frames)} at_60={len(normal)} drops_to_30={len(switches)} "
         f"span={t1 - t0:.0f}s"]
lines.append("ordinary 60 fps frame, ms per frame: threads " + " ".join(
    f"{k}:{v / n:.1f}" for k, v in base_th.most_common() if own.match(k) and v / n >= 0.3))
lines.append("  sections " + " ".join(f"{k}:{v / n:.2f}" for k, v in base_se.most_common(12) if v / n >= 0.05))

late = collections.Counter()
for k in switches:
    # The frame the game found late is the last one queued with 1: show it with the two before it.
    a, b = queues[max(k - 3, 0)][0], queues[k - 1][0]
    dts = [f"{(queues[j][0] - queues[j - 1][0]) * 1000:.1f}" for j in range(max(k - 3, 1), k + 1)]
    worst = max(range(max(k - 3, 1), k), key=lambda j: queues[j][0] - queues[j - 1][0])
    wa, wb = queues[worst - 1][0], queues[worst][0]
    th, se, cpus = profile(wa, wb)
    extra_th = sorted(((v - base_th[x] / n, x) for x, v in th.items() if own.match(x)), reverse=True)[:4]
    extra_se = sorted(((v - base_se[x] / n, x) for x, v in se.items()), reverse=True)[:5]
    for v, x in extra_se[:2]:
        if v > 1:
            late[x.split("@")[0]] += 1
    core = max(cpus.get("CPUCore_N", {"-": 0}).items(), key=lambda kv: kv[1])[0]
    lines.append(f"{wa - t0:7.2f}s frames(ms) {' '.join(dts)} | late {(wb - wa) * 1000:.1f} ms | threads " +
                 " ".join(f"{x}:{th[x]:.1f}({v:+.1f})" for v, x in extra_th) + " | sections " +
                 " ".join(f"{x}:{se[x]:.1f}({v:+.1f})" for v, x in extra_se if se[x] >= 0.5) +
                 f" | guest core cpu{core}@{clock(core, wa) if core != '-' else 0}MHz")
lines.insert(1, "late frames led by: " + " ".join(f"{k}:{v}" for k, v in late.most_common()))
open(os.path.join(out, "trace.txt"), "w").write("\n".join(lines) + "\n")
print("\n".join(lines[:4]))
