# SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
# SPDX-License-Identifier: GPL-3.0-or-later

# regress_input.py press <buttons hex> <wait s> | <mode> <seconds> : player 1 inputs for regress.sh, by the game bridge.
#   roam            walks in 8 directions (1.5 s each) and presses A every 0.7 s
#   circle          walks in a circle (3 s a turn), no button
#   mash            A every 1.3 s, stick at rest (menus, dialogs, battles)
#   brothership_cut from the 1.0.1 save by the stairs: into the "Nya nyaaah" dialog, clears it, climbs the stairs,
#                   then A every 0.8 s through the cutscene (the black-object hotspot)
#   silksong        Silksong new game in Moss Grotto: A pulls the needle 10 s, left jumping and attacking until 52 s, then back right
import math, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bridge

TICK = 0.05
DIRS = [(1, 0), (0, -1), (-1, 0), (0, 1), (1, -1), (-1, 1), (1, 1), (-1, -1)]


def roam(t, j):
    x, y = DIRS[int(t / 1.5) % len(DIRS)]
    return (0x1 if j % 14 == 0 else 0), int(x * 32767), int(y * 32767)


def circle(t, j):
    a = 2 * math.pi * t / 3
    return 0, int(25000 * math.cos(a)), int(25000 * math.sin(a))


def mash(t, j):
    return (0x1 if j % 26 == 0 else 0), 0, 0


CUT = [(2, 0, 32767), (10, 8, 0), (20, 0, 32767)]  # (until s, A period in ticks or 0, stick y; +y = up)


def brothership_cut(t, j):
    per, y = next(((p, yy) for e, p, yy in CUT if t < e), (16, 0))
    return (0x1 if per and j % per == 0 else 0), 0, y


def silksong(t, j):
    # new game, Moss Grotto: A pulls the needle, left until 52 s (next room, moss creeps, a pit), then back right;
    # B jumps, Y attacks
    if t < 10:
        return (0x1 if j % 10 < 3 else 0), 0, 0
    return (0x2 if j % 20 < 8 else 0) | (0x8 if j % 10 == 5 else 0), (-32767 if t < 52 else 32767), 0


def main():
    b = bridge.Bridge()
    if sys.argv[1] == "press":
        b.pad(0, int(sys.argv[2], 16))
        time.sleep(0.15)
        b.pad(0)
        time.sleep(float(sys.argv[3]))
        b.release(0)
        return
    f = {"roam": roam, "circle": circle, "mash": mash, "brothership_cut": brothership_cut,
         "silksong": silksong}[sys.argv[1]]
    start = time.time(); end = start + float(sys.argv[2]); j = 0
    while time.time() < end:
        j += 1
        btn, x, y = f(time.time() - start, j)
        try:
            b.pad(0, btn, x, y)
        except OSError:
            time.sleep(0.3); b = bridge.Bridge()
        time.sleep(TICK)
    b.pad(0)
    b.release(0)


if __name__ == "__main__":
    main()
