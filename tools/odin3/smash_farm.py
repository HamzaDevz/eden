#!/usr/bin/env python3
"""Keeps Smash Ultimate instances recording on the Windows PC, restarting each one before the
game wears out: without speed limit it hits null pointers after about 2 h (~200 matches),
then Eden's error log swallows tens of GB.

Instance k lives in ROOT\\k (eden.exe and its user folder, game bridge on port 6560+k) and
was set up once on the character select, which the save keeps: CPU players (--cpu), or the
player named Ultimus for the agent. A restart kills its recorder and Eden, starts the game
again, finds its way to the character select by comparing screenshots with the screens in
smash_screens.npz, then starts smash_record.py, which starts the matches. An instance whose recorder saved nothing for
--idle seconds is restarted too.

  smash_farm.py --instances 0 1 [--cpu] [--every 5400] [--idle 900]
"""
import argparse
import glob
import os
import shutil
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
# Pad scripts in frames: a button or a stick direction held n frames, then w n frames idle.
# What to press on each known screen on the way to the character select: the main menu and
# the Smash menu remember their last cursor, the corner moves bring it back to the top left.
ROUTE = {"title": "a w180", "main": "sl sl su su w20 a w180", "smash": "su su sl sl w20 a w180",
         "rules": "a w700"}
# Anywhere else (intro video, Spirits menus, a message): back twice, then A.
LOST = ["b w90", "b w90", "a w180"]
# The character select forgets that player 1 was a CPU: its hand goes down onto the player
# button, makes it a CPU, then carries its token from the top left corner onto Mario.
CPU_MARIO = "sd22 w10 a w30 sl120 su80 sd13 sr11 sd14 sr14 w10 a w30"
PADS = {"a": (0x1, 0, 0), "b": (0x2, 0, 0), "sl": (0, -32767, 0), "sr": (0, 32767, 0), "su": (0, 0, 32767),
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


def screen(g, refs):
    """Name of the reference screen nearest to g, "dark" or "unknown"."""
    if g.mean() < 12:
        return "dark", 0
    name = min(refs, key=lambda n: np.abs(g - refs[n]).mean())
    d = np.abs(g - refs[name]).mean()
    return (name if d < 25 else "unknown"), d


def log(*args):
    print(time.strftime("%H:%M:%S"), *args, flush=True)


def powershell(command):
    subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True)


class Instance:
    def __init__(self, k, a):
        self.k, self.a, self.port = k, a, 6560 + k
        self.dir = os.path.join(ROOT, str(k))
        self.log = os.path.join(a.home, f"rec{k}.log")
        self.started = 0
        refs = np.load(SCREENS)
        self.refs = {n: refs[n].astype(np.float32) for n in refs.files}

    def kill(self):
        exe = os.path.join(self.dir, "eden.exe")
        powershell(f"Get-Process eden -ErrorAction SilentlyContinue | Where-Object {{ $_.Path -eq '{exe}' }}"
                   " | Stop-Process -Force; Get-CimInstance Win32_Process -Filter \"Name='python.exe'\""
                   f" | Where-Object {{ $_.CommandLine -like '*smash_record.py*--port {self.port} *' }}"
                   " | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }")
        time.sleep(5)

    def last_save(self):
        """Time of the last match this instance's recorder saved."""
        out = os.path.join(self.a.home, "rec" if self.a.cpu else "play")
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
        if self.a.cpu and name == "css":
            play(b, CPU_MARIO)
            shot = grab(b)
            name = screen(shot[0], self.refs)[0] if shot else "none"
        if shot:  # to check the character select from afar
            write_png(os.path.join(self.a.home, f"shot{self.k}.png"), *shot[1])
        return name == "ready" or not self.a.cpu and name == "css"

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
        try:
            return self.navigate(b)
        except (OSError, RuntimeError) as e:
            log(f"instance {self.k}: {e}")
            return False
        finally:
            b.sock.close()

    def start(self):
        for attempt in range(3):
            log(f"instance {self.k}: restart")
            if self.launch():
                break
        else:
            log(f"instance {self.k}: character select not reached")
            return
        mode = (["--out", os.path.join(self.a.home, "rec")] if self.a.cpu else
                ["--agent", os.path.join(self.a.home, "models", "ultimus.pt"),
                 "--out", os.path.join(self.a.home, "play")])
        with open(self.log, "a") as out:
            subprocess.Popen([sys.executable, "-u", "smash_record.py", "--port", str(self.port),
                              "--sync", "1", "--auto", "--min-battery", "0", *mode],
                             cwd=os.path.dirname(os.path.abspath(__file__)), stdout=out,
                             stderr=subprocess.STDOUT, creationflags=DETACHED)
        log(f"instance {self.k}: recording")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--instances", type=int, nargs="+", default=[0, 1])
    ap.add_argument("--home", default=r"C:\Emulation\tools\smash-ai")
    ap.add_argument("--cpu", action="store_true", help="CPU against CPU, into HOME\\rec")
    ap.add_argument("--every", type=int, default=5400, help="restart after this many seconds")
    ap.add_argument("--idle", type=int, default=900, help="restart when nothing was saved for this long")
    a = ap.parse_args()
    instances = [Instance(k, a) for k in a.instances]
    for i in instances:
        i.start()
    while True:
        time.sleep(30)
        now = time.time()
        for i in instances:
            if now - i.started > a.every or (now - i.started > a.idle and now - i.last_save() > a.idle):
                i.start()


if __name__ == "__main__":
    sys.exit(main())
