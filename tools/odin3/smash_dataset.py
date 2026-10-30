#!/usr/bin/env python3
"""Decode smash_record.py matches into numpy arrays, cached as .npz next to each recording.

Per frame and player: the smash_state fields plus the WorkModule arrays. The pad is the one
the fighter really used (ControlModule), so CPUs and humans give the same kind of data.

  smash_dataset.py [--rec ~/smash-ai/rec]     decode the new recordings, print a summary
"""
import argparse
import glob
import gzip
import json
import os
import struct

import numpy as np

import smash_state
from bridge import Frame

SCALARS = ("kind", "cpu", "stock", "x", "y", "lr", "damage", "status", "situation",
           "motion_frame", "stick_x", "stick_y", "buttons", "slots", "slot", *smash_state.EYES)
WORK = {"wf0": "<120f", "wf1": "<40f", "wi0": "<270q", "wi1": "<32q", "wb0": "<9I", "wb1": "<3I"}


def decode(path, players=2):
    with gzip.open(path, "rt") as f:
        header = json.loads(f.readline()[2:])
        lines = f.readlines()
    n = len(lines)
    out = {name: np.zeros((n, players), np.float32) for name in SCALARS}
    out["motion"] = np.zeros((n, players), np.uint64)
    out["valid"] = np.zeros((n, players), bool)
    for name, fmt in WORK.items():
        out[name] = np.zeros((n, players, struct.calcsize(fmt) // struct.calcsize(fmt[-1])),
                             np.float32 if fmt[-1] == "f" else np.int64)
    out["frame"] = np.zeros(n, np.int64)
    out["stage"] = np.full(n, -1, np.int64)
    for t, line in enumerate(lines):
        frame = Frame(line)
        out["frame"][t] = frame.index
        stage = frame.watches.get("stage")
        if stage is not None:
            out["stage"][t] = int.from_bytes(stage, "little")
        for i, fighter in smash_state.parse(frame, players).items():
            out["valid"][t, i] = True
            for name in SCALARS:
                out[name][t, i] = getattr(fighter, name)
            out["motion"][t, i] = fighter.motion
            for name, fmt in WORK.items():
                data = frame.watches.get(f"{i}{name}")
                if data is not None:
                    out[name][t, i] = struct.unpack(fmt, data)
    out["build"] = np.array(header["build"])
    return out


def load(path):
    cache = path.replace(".txt.gz", ".npz")
    if not os.path.exists(cache) or os.path.getmtime(cache) < os.path.getmtime(path):
        d = decode(path)
        tmp = f"{cache}.{os.getpid()}.tmp.npz"  # the loop and report.py may cache the same match
        np.savez_compressed(tmp, **d)
        try:
            os.replace(tmp, cache)  # a killed process leaves no broken cache
        except PermissionError:  # on Windows, while another process reads the one it wrote
            os.remove(tmp)
        return d
    with np.load(cache) as z:
        d = dict(z)
    if "shield" not in d:  # cached before the eyes: from the whole arrays when they were recorded
        for name, (table, j) in smash_state.EYES.items():
            d[name] = (d[table][..., j].astype(np.float32) if d[table].any()
                       else np.full(d["x"].shape, np.nan, np.float32))
    return d


def recordings(rec):
    return sorted(glob.glob(os.path.join(os.path.expanduser(rec), "*.txt.gz")))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rec", default="~/smash-ai/rec")
    a = ap.parse_args()
    total = 0
    for path in recordings(a.rec):
        d = load(path)
        n = len(d["frame"])
        total += n
        last = d["valid"].nonzero()[0].max(initial=0)
        print(os.path.basename(path), n, "frames", "stage", d["stage"].max(),
              " vs ".join(f"{int(d['kind'][last, i]):#x} stock {int(d['stock'][last, i])}"
                          for i in range(d["kind"].shape[1])))
    print(total, "frames", f"{total / 3600:.1f} min")


if __name__ == "__main__":
    main()
