# SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
# SPDX-License-Identifier: GPL-3.0-or-later

# addons.py <src config.ini> <dst config.ini> <title id hex> [add-on name;...]: Eden keeps the add-ons (mods, update)
# a player switched off for each game in [DisabledAddOns] of the global config; the listed ones are switched on.
import re, sys

src, dst, title = sys.argv[1], sys.argv[2], int(sys.argv[3], 16)
enable = {a for a in (sys.argv[4] if len(sys.argv) > 4 else "").split(";") if a}
lines = open(src).read().splitlines()
start = lines.index("[DisabledAddOns]")
end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("[")), len(lines))
body = lines[start + 1:end]
n = next((m.group(1) for l in body if (m := re.fullmatch(r"(\d+)\\title_id=(\d+)", l)) and int(m.group(2)) == title), None)
if n is None and enable:
    sys.exit(f"addons: title {title:016X} not in [DisabledAddOns]")
if n is not None and enable:
    names = [l.split("=", 1)[1] for l in body if re.fullmatch(rf"{n}\\disabled\\\d+\\d=.*", l)]
    missing = enable - set(names)
    if missing:
        sys.exit(f"addons: not disabled, or no such add-on: {';'.join(sorted(missing))}")
    keep = [a for a in names if a not in enable]
    at = next(i for i, l in enumerate(body) if l.startswith(f"{n}\\disabled\\"))
    body = [l for l in body[:at] + [None] + body[at:] if l is None or not l.startswith(f"{n}\\disabled\\")]
    at = body.index(None)
    body[at:at + 1] = [f"{n}\\disabled\\size={len(keep)}"] + [x for k, a in enumerate(keep, 1)
                                                               for x in (f"{n}\\disabled\\{k}\\d\\default=false",
                                                                         f"{n}\\disabled\\{k}\\d={a}")]
    lines = lines[:start + 1] + body + lines[end:]
open(dst, "w").write("\n".join(lines) + "\n")
