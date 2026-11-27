# SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
# SPDX-License-Identifier: GPL-3.0-or-later
# smg2.py route <out dir> | explore <secs> <out dir> [A period s] : Super Mario Galaxy 2 inputs for smg2.sh, by the game bridge.
# Returns early when the bridge stays gone (Eden crashed).
#   route    new save to the first playable scene (ROUTE), a shot every 2 s; exit 1 if Eden died
#   explore  presses A every period (default 1.5 s), Right just before it with SMG2_RIGHT=1 (the menu pointer only
#            shows after a direction), and saves a 360p shot every 2 s as <out>/tNNN.png
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bridge
import numpy as np
from PIL import Image

A, RIGHT = 0x1, 0x4000
# A shot asked before the renderer exists crashes Eden (null renderer in GameBridge::RequestShot)
FIRST_SHOT = 10


def connect(timeout=90):
    end = time.time() + timeout
    while True:
        try:
            b = bridge.Bridge()
            b.ask("info", "info")
            return b
        except (OSError, EOFError):
            if time.time() > end:
                raise
            time.sleep(1)


def save_shot(b, path):
    w, h, data = b.shot(360)
    Image.fromarray(np.frombuffer(data, np.uint8).reshape(h, w, 4)[:, :, 2::-1]).save(path)


class Pad:
    """Bridge that reconnects instead of raising; dead after 10 s without Eden."""
    def __init__(self):
        self.b = connect(); self.dead = False

    def do(self, f):
        try:
            return f(self.b)
        except (RuntimeError, OSError, EOFError) as e:
            print("bridge:", e)
            try:
                self.b = connect(10)
            except (OSError, EOFError):
                self.dead = True


# Route on a new save, from the URI launch to the 2D opening level then on: (until s, mode). ra = Right tap then A
# every 1.5 s (title, new file, icon, mode, the file's "Play", story text), run = stick right with A every 0.9 s.
ROUTE = [(62, "ra"), (float(os.environ.get("SMG2_RUN", 190)), "run")]


def route(out, plan=ROUTE):
    os.makedirs(out, exist_ok=True)
    p = Pad(); start = time.time(); next_shot = start + FIRST_SHOT; j = 0
    for until, mode in plan:
        while not p.dead and (t := time.time() - start) < until:
            if time.time() >= next_shot:
                p.do(lambda b: save_shot(b, os.path.join(out, f"t{int(t):03d}.png"))); next_shot += 2
            if mode == "ra":
                p.do(lambda b: b.pad(0, RIGHT)); time.sleep(0.1); p.do(lambda b: b.pad(0)); time.sleep(0.1)
                p.do(lambda b: b.pad(0, A)); time.sleep(0.12); p.do(lambda b: b.pad(0)); time.sleep(1.18)
            else:
                p.do(lambda b: b.pad(0, A if j % 9 == 0 else 0, 32767, 0)); time.sleep(0.1)
            j += 1
    p.do(lambda b: b.release(0))
    return not p.dead


def explore(secs, out, period):
    os.makedirs(out, exist_ok=True)
    b = connect()
    start = time.time(); next_a = start + period; next_shot = start + FIRST_SHOT
    while (t := time.time() - start) < secs:
        now = time.time()
        if now >= next_shot:
            try:
                save_shot(b, os.path.join(out, f"t{int(t):03d}.png"))
            except (RuntimeError, OSError, EOFError) as e:
                print("shot", int(t), e)
                try:
                    b = connect(10)
                except (OSError, EOFError):
                    print("bridge gone (crash?) at", int(t)); return
            next_shot += 2
        if now >= next_a and period > 0:
            if os.environ.get("SMG2_RIGHT"):
                b.pad(0, RIGHT); time.sleep(0.1); b.pad(0); time.sleep(0.1)
            b.pad(0, A); time.sleep(0.12); b.pad(0)
            next_a += period
        time.sleep(0.05)
    b.release(0)


if __name__ == "__main__":
    if sys.argv[1] == "route":
        sys.exit(0 if route(sys.argv[2]) else 1)
    if sys.argv[1] == "explore":
        explore(float(sys.argv[2]), sys.argv[3], float(sys.argv[4]) if len(sys.argv) > 4 else 1.5)
