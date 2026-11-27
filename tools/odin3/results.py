# SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
# SPDX-License-Identifier: GPL-3.0-or-later

# Keeps every measured run, one CSV per game, so patterns across games and drivers can be looked for later.
#   results.py <run dir>... --commit C --scene S [--settings "Sec.key=v ..."] [--note N] [--skip K] [--driver D]
# A run dir is what regress.sh (or a bench built on it) leaves: meta.json, result.json, sf.txt, poll.csv,
# logcat.txt. Rows go to tools/odin3/results/<TITLEID>.csv; --skip drops the first SurfaceFlinger dumps (boot).
import argparse, csv, datetime, json, os, re

COLS = ["date", "title", "name", "run", "commit", "driver", "settings", "scene", "secs", "alive", "fatal", "fps",
        "slow_pct", "over34", "over100", "longest_ms", "black", "flick", "gpu_busy", "gpu_mhz", "cpu_c_avg",
        "cpu_c_max", "gpu_c_avg", "gpu_c_max", "c0_mhz", "c6_mhz", "ddr_mhz", "usb_w", "note"]


def frames(path, skip):
    dumps = [[]]
    for line in open(path):
        if line.startswith("@"):
            dumps.append([])
            continue
        p = line.split()
        if len(p) == 3 and p[1].isdigit() and 0 < int(p[1]) < 9e18:
            dumps[-1].append(int(p[1]))
    pairs = set()
    for d in dumps[skip + 1:]:
        s = sorted(set(d))
        pairs.update(zip(s, s[1:]))
    return [(b - a) / 1e6 for a, b in pairs]


def poll(path):
    rows = list(csv.DictReader(open(path)))
    col = lambda c: [float(r[c]) for r in rows if r.get(c) not in (None, "")]
    avg = lambda c, k=1: round(sum(v := col(c)) / len(v) / k, 1) if col(c) else ""
    top = lambda c, k=1: round(max(col(c)) / k, 1) if col(c) else ""
    watts = [float(r["usb_ma"]) * float(r["usb_mv"]) / 1e6 for r in rows if r.get("usb_ma") and r.get("usb_mv")]
    return {"gpu_busy": avg("gpu_busy"), "gpu_mhz": avg("gpu_mhz"), "cpu_c_avg": avg("cpu_tmax", 1000),
            "cpu_c_max": top("cpu_tmax", 1000), "gpu_c_avg": avg("gpu_tmax", 1000), "gpu_c_max": top("gpu_tmax", 1000),
            "c0_mhz": avg("c0_mhz"), "c6_mhz": avg("c6_mhz"), "ddr_mhz": avg("ddr_mhz"),
            "usb_w": round(sum(watts) / len(watts), 2) if watts else ""}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--commit", required=True)
    ap.add_argument("--scene", required=True)
    ap.add_argument("--settings", default="")
    ap.add_argument("--note", default="")
    ap.add_argument("--driver", default="", help="when logcat.txt lost the Report: Driver: line")
    ap.add_argument("--skip", type=int, default=0)
    a = ap.parse_args()
    for run in a.runs:
        meta = json.load(open(os.path.join(run, "meta.json")))
        res = json.load(open(os.path.join(run, "result.json")))
        ms = frames(os.path.join(run, "sf.txt"), a.skip)
        log = open(os.path.join(run, "logcat.txt"), errors="replace").read()
        drv = re.findall(r"Report: Driver: (.+)", log)
        row = {"date": datetime.date.fromtimestamp(os.path.getmtime(os.path.join(run, "result.json"))).isoformat(),
               "title": meta["title"], "name": meta.get("name", ""), "run": os.path.basename(run.rstrip("/")),
               "commit": a.commit, "driver": drv[-1].strip() if drv else a.driver, "settings": a.settings,
               "scene": a.scene, "secs": meta.get("secs", ""), "alive": res.get("alive", ""),
               "fatal": res.get("fatal", ""), "fps": round(len(ms) / (sum(ms) / 1000), 2) if ms else 0,
               "slow_pct": round(100 * sum(m > 20 for m in ms) / len(ms), 2) if ms else 100,
               "over34": sum(m > 34 for m in ms), "over100": sum(m > 100 for m in ms),
               "longest_ms": round(max(ms)) if ms else 0, "black": res.get("black", ""),
               "flick": res.get("flick", ""), "note": a.note, **poll(os.path.join(run, "poll.csv"))}
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results", f"{meta['title']}.csv")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        new = not os.path.exists(out)
        with open(out, "a", newline="") as f:
            w = csv.DictWriter(f, COLS)
            if new:
                w.writeheader()
            w.writerow(row)
        print(out, row["run"], row["driver"], row["fps"], row["slow_pct"], row["longest_ms"])


if __name__ == "__main__":
    main()
