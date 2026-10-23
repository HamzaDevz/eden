#!/usr/bin/env python3
"""Keeps Smash Ultimate instances recording on the Windows PC, restarting each one before the
game wears out: without speed limit it hits null pointers after about 2 h (~200 matches),
then Eden's error log swallows tens of GB. Each Eden also starts at about 7 GB and grows by
about 3.5 GB an hour: past 45 min two of them filled the PC's 32 GB and training swapped.

Instance k lives in ROOT\\k (eden.exe and its user folder, game bridge on port 6560+k); its
save keeps player 1 named Ultimus and player 2 a CPU. A restart kills its recorder and Eden,
starts the game again, finds its way to the character select by comparing screenshots with
the screens in smash_screens.npz, puts Mario in player 1's slot (found by his portrait in
smash_mario.npy, the grid grows as matches unlock fighters), as a CPU for the instances
listed by --cpu, sets player 2's level (9 against a CPU, else the one in HOME\\level, which
smash_rl.py raises), then starts smash_record.py, which starts the matches. Ultimus plays
models/champion.pt on the instances listed by --champion and the candidate models/ultimus.pt on
the others, so that smash_rl.py compares them on matches played at the same time. An instance
is restarted too when its recorder saved nothing for --idle seconds, or when the level changed.

  smash_farm.py --instances 0 1 [--cpu 0] [--champion 0] [--every 5400] [--idle 900]
"""
import argparse
import glob
import os
import shutil
import socket
import subprocess
import sys
import time

import numpy as np

from bridge import Bridge, write_png

ROOT = r"C:\Emulation\tools\eden-inst"
GAME = r"C:\Emulation\roms\switch\Super Smash Bros Ultimate.xci\Super Smash Bros Ultimate.xci"
# Eden writes a save size file under a shifted copy of the profile UUID; at the next start the
# folder is taken for an orphaned profile, and a dialog nobody sees blocks the boot.
ORPHAN = r"user\nand\user\save\0000000000000000\C80FA1D29410835E6100000000000000"
DETACHED = 0x8 | 0x200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
SCREENS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "smash_screens.npz")
LEVELS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "smash_levels.npz")
MARIO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "smash_mario.npy")
# Pad scripts in frames: a button or a stick direction held n frames, then w n frames idle.
# What to press on each known screen on the way to the character select: the main menu and
# the Smash menu remember their last cursor, the corner moves bring it back to the top left.
ROUTE = {"title": "a w180", "main": "sl sl su su w20 a w180", "smash": "su su sl sl w20 a w180",
         "rules": "a w700"}
# Anywhere else (intro video, Spirits menus, a message): back twice, then A.
LOST = ["b w90", "b w90", "a w180"]
# The character grid grows as matches unlock fighters: the select is also told by the rest.
ROSTER = slice(3, 22)
# From the bottom right corner onto player 2's level, which the hand does not always reach:
# SPIRAL then circles around it until the level box lights up.
TO_LEVEL = "sr120 sd80 w10 sl27 su12 w10"
SPIRAL = []
for ring in range(1, 9):  # right 1, down 1, left 2, up 2, right 3...
    SPIRAL += [("sr4", "sd4", "sl4", "su4")[(ring - 1) % 4]] * ((ring + 1) // 2)
PADS = {"a": (0x1, 0, 0), "b": (0x2, 0, 0), "dd": (0x8000, 0, 0), "sl": (0, -32767, 0), "sr": (0, 32767, 0), "su": (0, 0, 32767),
        "sd": (0, 0, -32767)}


def play(b, script):
    """Sends a pad script in lockstep with the game."""
    steps = []
    for tok in script.split():
        k, n = tok.rstrip("0123456789"), int(tok.lstrip("abdlrsuw") or 6)
        steps += [(0, 0, 0)] * n if k == "w" else [PADS[k]] * n + [(0, 0, 0)] * 8
    b.ask("stream 1", "ok")
    b.sync(1)
    for _ in b.frames(raw=True):
        if not steps:
            break
        b.pad(0, *steps.pop(0))
    b.release(0)
    b.sync(0)
    b.ask("stream 0", "ok")


def grab(b):
    """The screen as 36x64 grey levels, with the screenshot itself; None if none came."""
    try:
        w, h, bgra = b.shot(360)
    except (RuntimeError, TypeError, ValueError):
        return None
    g = np.frombuffer(bgra, np.uint8).reshape(h, w, 4)[:, :, :3].mean(2)
    g = g[:h - h % 36, :w - w % 64].reshape(36, h // 36, 64, w // 64).mean((1, 3))
    return g, (w, h, bgra)


def load_refs():
    refs = np.load(SCREENS)
    return {n: refs[n].astype(np.float32) for n in refs.files}


def screen(g, refs):
    """Name of the reference screen nearest to g, "dark" or "unknown"."""
    if g.mean() < 12:
        return "dark", 0
    name = min(refs, key=lambda n: np.abs(g - refs[n]).mean())
    d = np.abs(g - refs[name]).mean()
    if d >= 25:
        rest = np.ones(len(g), bool)
        rest[ROSTER] = False
        d = np.abs(g - refs["css"])[rest].mean()
        return ("css" if d < 12 else "unknown"), d
    return name, d


def level_box(shot):
    """Brightness of player 2's level box, white under the hand."""
    w, h, bgra = shot
    return np.frombuffer(bgra, np.uint8).reshape(h, w, 4)[312:328, 576:588, :3].mean()


def on_mario(shot, mario):
    """Whether player 1's panel shows Mario, under its hand or chosen."""
    w, h, bgra = shot
    crop = np.frombuffer(bgra, np.uint8).reshape(h, w, 4)[235:305, :300, 2::-1].astype(np.float32)
    return np.abs(crop - mario).mean() < 15


def pick_mario(b, mario):
    """Sweeps player 1's hand along rows from the top left corner until its panel shows Mario,
    then puts the token there; True once done."""
    play(b, "sl120 su80 w10")
    for row in range(12):
        play(b, "sd6")
        for col in range(16):
            play(b, "sr6")
            if on_mario(b.shot(360), mario):
                play(b, "a w30")
                return True
        play(b, "sl120")
    return False


def read_level(shot, digits):
    """Player 2's level as the hovered box shows it, None if no digit matches."""
    w, h, bgra = shot
    crop = np.frombuffer(bgra, np.uint8).reshape(h, w, 4)[310:332, 552:574, 2::-1].astype(np.float32)
    level = min(digits, key=lambda n: np.abs(crop - digits[n]).mean())
    return level if np.abs(crop - digits[level]).mean() < 12 else None


def log(*args):
    print(time.strftime("%H:%M:%S"), *args, flush=True)


def powershell(command):
    subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True)


class Instance:
    def __init__(self, k, a):
        self.k, self.a, self.port = k, a, 6560 + k
        self.dir = os.path.join(ROOT, str(k))
        self.log = os.path.join(a.home, f"rec{k}.log")
        self.cpu, self.level, self.started = k in a.cpu, 9, 0
        self.refs = load_refs()
        digits = np.load(LEVELS)
        self.digits = {int(n[5:]): digits[n].astype(np.float32) for n in digits.files}
        self.mario = np.load(MARIO).astype(np.float32)

    def kill(self):
        exe = os.path.join(self.dir, "eden.exe")
        powershell(f"Get-Process eden -ErrorAction SilentlyContinue | Where-Object {{ $_.Path -eq '{exe}' }}"
                   " | Stop-Process -Force; Get-CimInstance Win32_Process -Filter \"Name='python.exe'\""
                   f" | Where-Object {{ $_.CommandLine -like '*smash_record.py*--port {self.port} *' }}"
                   " | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }")
        time.sleep(5)

    def last_save(self):
        """Time of the last match this instance's recorder saved."""
        out = os.path.join(self.a.home, "rec" if self.cpu else "play")
        name = f"*-{self.port}.txt.gz" if self.port != 6560 else "????????-??????.txt.gz"
        times = [os.path.getmtime(p) for p in glob.glob(os.path.join(out, name))]
        return max(times, default=0)

    def navigate(self, b):
        """Presses its way to the character select by what is on screen; True once ready."""
        lost = 0
        for _ in range(60):
            shot = grab(b)
            if shot is None:
                time.sleep(2)
                continue
            name, d = screen(shot[0], self.refs)
            log(f"instance {self.k}: {name} {d:.1f}")
            if name == "dark":
                time.sleep(2)
            elif name == "unknown":
                play(b, LOST[lost % len(LOST)])
                lost += 1
            elif name in ROUTE:
                play(b, ROUTE[name])
            else:
                break
        else:
            return False
        if name == "css":
            if self.cpu:  # the select forgets that player 1 was a CPU: the player button first
                play(b, "sd22 w10 a w30")
            if not pick_mario(b, self.mario):
                log(f"instance {self.k}: Mario not found")
            shot = grab(b)
            name = screen(shot[0], self.refs)[0] if shot else "none"
        ready = name == "ready" and self.set_level(b)
        shot = grab(b)
        if shot:  # to check the character select from afar
            write_png(os.path.join(self.a.home, f"shot{self.k}.png"), *shot[1])
        return ready

    def set_level(self, b):
        """Sets player 2's level through its list; True once the box shows it."""
        play(b, TO_LEVEL)
        for move in [None] + SPIRAL:
            if move:
                play(b, move)
            shot = b.shot(360)
            if level_box(shot) > 200:
                break
        else:
            log(f"instance {self.k}: level box not found")
            return False
        level = read_level(shot, self.digits)
        if level is not None and level != self.level:
            play(b, "a w30 " + "dd " * ((level - self.level) % 9) + "a w30")
            level = read_level(b.shot(360), self.digits)
        log(f"instance {self.k}: level {level}")
        return level == self.level

    def launch(self):
        self.kill()
        shutil.rmtree(os.path.join(self.dir, ORPHAN), ignore_errors=True)
        subprocess.Popen([os.path.join(self.dir, "eden.exe"), "-g", GAME], cwd=self.dir,
                         creationflags=DETACHED)
        self.started = time.time()
        b = None
        while b is None and time.time() < self.started + 180:
            try:
                b = Bridge("127.0.0.1", self.port)
            except OSError:
                time.sleep(5)
        if b is None:
            log(f"instance {self.k}: no bridge")
            return False
        time.sleep(30)  # boot logos: no frames to step through yet
        # A screenshot waits for the next frame: instance 0 once booted for over a minute.
        while True:
            try:
                b.shot(360)
                break
            except (socket.timeout, RuntimeError) as e:
                if time.time() > self.started + 300:
                    b.sock.close()
                    log(f"instance {self.k}: no frame")
                    return False
                if isinstance(e, socket.timeout):  # its answer would come out of turn
                    b.sock.close()
                    b = Bridge("127.0.0.1", self.port)
                else:
                    time.sleep(2)
        try:
            return self.navigate(b)
        except (OSError, RuntimeError) as e:
            log(f"instance {self.k}: {e}")
            return False
        finally:
            b.sock.close()

    def wanted_level(self):
        path = os.path.join(self.a.home, "level")
        return 9 if self.cpu else int(open(path).read()) if os.path.exists(path) else 1

    def start(self):
        self.level = self.wanted_level()
        for attempt in range(3):
            log(f"instance {self.k}: restart")
            if self.launch():
                break
        else:
            log(f"instance {self.k}: character select not reached")
            return
        mode = (["--out", os.path.join(self.a.home, "rec")] if self.cpu else
                ["--agent", os.path.join(self.a.home, "models",
                                         "champion.pt" if self.k in self.a.champion else "ultimus.pt"),
                 "--out", os.path.join(self.a.home, "play")])
        with open(self.log, "a") as out:
            subprocess.Popen([sys.executable, "-u", "smash_record.py", "--port", str(self.port),
                              "--sync", "1", "--auto", "--min-battery", "0",
                              "--level", str(self.level), *mode],
                             cwd=os.path.dirname(os.path.abspath(__file__)), stdout=out,
                             stderr=subprocess.STDOUT, creationflags=DETACHED)
        log(f"instance {self.k}: recording")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--instances", type=int, nargs="+", default=[0, 1])
    ap.add_argument("--home", default=r"C:\Emulation\tools\smash-ai")
    ap.add_argument("--cpu", type=int, nargs="*", default=[],
                    help="instances where CPU plays against CPU, into HOME\\rec")
    ap.add_argument("--champion", type=int, nargs="*", default=[0],
                    help="instances where Ultimus plays the champion instead of the candidate")
    ap.add_argument("--every", type=int, default=1800, help="restart after this many seconds")
    ap.add_argument("--idle", type=int, default=900, help="restart when nothing was saved for this long")
    a = ap.parse_args()
    instances = [Instance(k, a) for k in a.instances]
    for i in instances:
        i.start()
    while True:
        time.sleep(30)
        now = time.time()
        for i in instances:
            if (now - i.started > a.every or i.wanted_level() != i.level
                    or now - i.started > a.idle and now - i.last_save() > a.idle):
                i.start()


if __name__ == "__main__":
    sys.exit(main())
