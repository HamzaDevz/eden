# SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
# SPDX-License-Identifier: GPL-3.0-or-later

# setini.py [--global] <src.ini> <dst.ini> Section.key=value ...  (in a per-game ini, forces use_global=false for
# each key)
import sys

glob = sys.argv[1] == "--global"
src, dst, edits = sys.argv[1 + glob], sys.argv[2 + glob], sys.argv[3 + glob:]
lines = open(src).read().splitlines()
for e in edits:
    sk, val = e.split("=", 1)
    sec, key = sk.split(".", 1)
    start = lines.index(f"[{sec}]")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("[")), len(lines))
    body = [l for l in lines[start + 1:end]
            if not l.startswith((f"{key}=", f"{key}\\use_global=", f"{key}\\default="))]
    while body and body[-1] == "":
        body.pop()
    body += ([] if glob else [f"{key}\\use_global=false"]) + [f"{key}\\default=false", f"{key}={val}", ""]
    lines = lines[:start + 1] + body + lines[end:]
open(dst, "w").write("\n".join(lines) + "\n")
