# SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
# SPDX-License-Identifier: GPL-3.0-or-later

# regress.py analyze <game dir> : result.json from one regress.sh game run (fps from SurfaceFlinger, one-frame black
#                                 flashes and other one-frame outliers in the TV video, fatal log lines) + sheet.png
# regress.py cmp <run A> <run B> : per game, B against A; flags crashes, fps losses and more flashes
import json, os, subprocess, sys
import numpy as np

W, H = 640, 360


def fps(path, secs):
    # Each dump holds the layer's last 128 presents: only presents listed next to each other in one dump are
    # consecutive frames, so holes between dumps are not counted as long frames.
    dumps, pairs = [[]], set()
    for line in open(path):
        if line.startswith("@"):
            dumps.append([])
            continue
        p = line.split()
        if len(p) == 3 and p[1].isdigit() and 0 < int(p[1]) < 9e18:
            dumps[-1].append(int(p[1]))
    for d in dumps:
        s = sorted(set(d))
        pairs.update(zip(s, s[1:]))
    ms = [(b - a) / 1e6 for a, b in pairs]
    if not ms:
        return {"fps": 0, "slow_pct": 100, "longest_ms": 0}
    return {"fps": round(len(ms) / (sum(ms) / 1000), 1), "slow_pct": round(100 * sum(m > 20 for m in ms) / len(ms), 1),
            "longest_ms": round(max(ms))}


def video(out):
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-f", "h264", "-i", os.path.join(out, "tv.h264"), "-vf",
                          f"scale={W}:{H}", "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         stdout=subprocess.PIPE)
    fr, idx, k = [], [], 0
    while len(b := p.stdout.read(W * H * 3)) == W * H * 3:
        f = np.frombuffer(b, np.uint8).reshape(H, W, 3).astype(np.int16)
        if not fr or np.abs(f - fr[-1]).mean() > 0.6:
            fr.append(f); idx.append(k)
        k += 1
    black, flick = [], []
    for i in range(1, len(fr) - 1):
        a, c, n = fr[i - 1], fr[i], fr[i + 1]
        same = np.abs(a - n).max(axis=2)
        m = (c.max(axis=2) < 28) & (a.max(axis=2) > 70) & (n.max(axis=2) > 70) & (same < 40)
        if (cnt := int(m.sum())) >= 800:
            black.append((idx[i], cnt))
        d1, d2 = c - a, c - n
        out_any = ((d1.min(axis=2) > 40) & (d2.min(axis=2) > 40)) | ((d1.max(axis=2) < -40) & (d2.max(axis=2) < -40))
        if (cnt := int((out_any & (same < 24)).sum())) >= 2000:
            flick.append((idx[i], cnt))
    return {"frames": k, "unique": len(fr), "black": len(black), "black_px": sum(c for _, c in black),
            "flick": len(flick), "worst": [f for f, _ in sorted(black + flick, key=lambda e: -e[1])[:6]]}


def analyze(out):
    meta = json.load(open(os.path.join(out, "meta.json")))
    r = dict(meta)
    r.update(fps(os.path.join(out, "sf.txt"), meta["secs"]))
    r.update(video(out))
    log = open(os.path.join(out, "logcat.txt"), errors="replace").read().splitlines()
    fatal = [l for l in log if any(k in l for k in ("Fatal signal", "PANIC", "Assertion", "rejected", "Out of memory"))]
    r["fatal"] = len(fatal)
    r["fatal_first"] = fatal[0][-160:] if fatal else ""
    json.dump(r, open(os.path.join(out, "result.json"), "w"), indent=1)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "h264", "-i", os.path.join(out, "tv.h264"), "-vf",
                    f"fps={20 / max(meta['secs'], 1)},scale=320:180,tile=5x4", "-frames:v", "1",
                    os.path.join(out, "sheet.png")])
    print(line(r))


def line(r):
    return (f"{r['title']} {r['name'][:22]:22} alive={r['alive']} fatal={r['fatal']} fps={r['fps']} slow={r['slow_pct']}% "
            f"longest={r['longest_ms']}ms unique={r['unique']} black={r['black']} flick={r['flick']} save={r['save']}")


def load(run):
    res = {}
    for g in sorted(os.listdir(run)):
        p = os.path.join(run, g, "result.json")
        if os.path.exists(p):
            res[g] = json.load(open(p))
    return res


def cmp(a, b):
    A, B = load(a), load(b)
    bad = 0
    for g, rb in B.items():
        ra = A.get(g)
        flags = []
        if not rb["alive"] or rb["fatal"] > (ra["fatal"] if ra else 0):
            flags.append("CRASH/FATAL")
        if ra:
            if rb["fps"] < ra["fps"] - 1.5:
                flags.append(f"fps {ra['fps']}->{rb['fps']}")
            if rb["slow_pct"] > ra["slow_pct"] + 5:
                flags.append(f"slow {ra['slow_pct']}->{rb['slow_pct']}%")
            if rb["black"] > 2 * ra["black"] + 5:
                flags.append(f"black {ra['black']}->{rb['black']}")
            if rb["unique"] < 0.5 * ra["unique"]:
                flags.append(f"frozen? unique {ra['unique']}->{rb['unique']}")
        bad += bool(flags)
        print(f"{'REGRESSION ' if flags else 'ok         '}{line(rb)}  {' '.join(flags)}")
    print(f"{bad} game(s) flagged out of {len(B)}; check each sheet.png before calling it clean")
    return bad


if __name__ == "__main__":
    if sys.argv[1] == "analyze":
        analyze(sys.argv[2])
    else:
        sys.exit(1 if cmp(sys.argv[2], sys.argv[3]) else 0)
