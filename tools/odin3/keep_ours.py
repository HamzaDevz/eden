# SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
# SPDX-License-Identifier: GPL-3.0-or-later

# keep_ours.py <old_upstream> <ours> <new_upstream> <candidate>  (exit 1 and list each line of our delta
# old_upstream..ours that the candidate merge lost or brought back, upstream's own changes allowed)
import re
import subprocess
import sys
from collections import Counter

base, ours, upstream, cand = sys.argv[1:5]


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, errors="replace",
                          check=True).stdout


def counts(rev, path):
    r = subprocess.run(["git", "show", f"{rev}:{path}"], capture_output=True, text=True,
                       errors="replace")
    return Counter(l.strip() for l in r.stdout.splitlines()) if r.returncode == 0 else Counter()


paths = [p for p in git("diff", "--no-renames", "--name-only", "-z", base, ours).split("\0") if p]
problems = []
for path in paths:
    added, removed = Counter(), Counter()
    for l in git("diff", "--no-renames", "-U0", base, ours, "--", path).splitlines():
        if l[:1] in ("+", "-") and re.search(r"\w", l[1:]):
            (added if l[0] == "+" else removed)[l[1:].strip()] += 1
    b, o, u, c = (counts(rev, path) for rev in (base, ours, upstream, cand))
    problems += [f"{path}: perdu   {l}" for l in added if c[l] < o[l] - max(0, b[l] - u[l])]
    problems += [f"{path}: revenu  {l}" for l in removed if c[l] > o[l] + max(0, u[l] - b[l])]

print("\n".join(problems) if problems else f"ok : {len(paths)} fichiers, nos changements gardés")
sys.exit(1 if problems else 0)
