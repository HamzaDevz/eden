# SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
# SPDX-License-Identifier: GPL-3.0-or-later

# play.py <seconds> <outdir> [seed]: plays Zelda EoW from the Mac so measurements see real gameplay.
# Watches a 480x270 screenrecord stream decoded by ffmpeg (screencap stalls SurfaceFlinger and drops a
# 60 fps game to 37 fps; the stream keeps 60 fps for about 0.7 W) and finds Link by his blue hood. Exploration is
# curiosity driven: each heading is scored by how new the views it leads to are, compared with every
# view seen so far, and a heading that stops changing the picture is marked blocked for a while.
# Anything moving near Link that is not flickering in place is treated as an enemy: Link turns to it
# and swings. Also charges spin attacks, shoots arrows, throws bombs, and clears text boxes and the
# game over screen. Logs one line per step to play.log and keeps a frame every 10 s in frames/.
import os, random, subprocess, sys, threading, time
import numpy as np
from PIL import Image

ADB = [os.path.expanduser("~/Library/Android/sdk/platform-tools/adb")]
PKG = os.environ.get("PKG", "com.miHoYo.Yuanshen")
EV = "/dev/input/event8"
KEYS = {"a": 304, "b": 305, "x": 307, "y": 308, "l": 310, "r": 311, "zl": 312, "zr": 313}
DIRS = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)]  # y > 0 is down

dur, out = float(sys.argv[1]), sys.argv[2]
rng = random.Random(int(sys.argv[3]) if len(sys.argv) > 3 else 1)
os.makedirs(os.path.join(out, "frames"), exist_ok=True)
log = open(os.path.join(out, "play.log"), "w")
sh = subprocess.Popen(ADB + ["shell", "-T", "sh"], stdin=subprocess.PIPE, text=True)


def send(s):
    sh.stdin.write(s + "\n")
    sh.stdin.flush()


def stick(d):
    x, y = (int(c * 32767 / (1.414 if d[0] and d[1] else 1)) for c in d)
    send(f"sendevent {EV} 3 0 {x}; sendevent {EV} 3 1 {y}; sendevent {EV} 0 0 0")


def key(name, down):
    send(f"sendevent {EV} 1 {KEYS[name]} {int(down)}; sendevent {EV} 0 0 0")


def press(name, ms=80):
    k = KEYS[name]
    send(f"sendevent {EV} 1 {k} 1; sendevent {EV} 0 0 0; usleep {ms * 1000}; "
         f"sendevent {EV} 1 {k} 0; sendevent {EV} 0 0 0")


def focused():
    w = subprocess.run(ADB + ["shell", "dumpsys window | grep mCurrentFocus"], capture_output=True, text=True).stdout
    return f"{PKG}/org.yuzu.yuzu_emu.activities.EmulationActivity" in w


class Screen(threading.Thread):
    """Keeps the latest frame of the stream; screenrecord stops after 180 s, so restart it."""

    def __init__(self):
        super().__init__(daemon=True)
        self.frame, self.procs, self.done = None, [], False
        self.start()

    def run(self):
        size = 480 * 270 * 3
        while not self.done:
            rec = subprocess.Popen(ADB + ["exec-out", "screenrecord --size 480x270 --bit-rate 1000000 "
                                          "--output-format=h264 -"], stdout=subprocess.PIPE)
            # No "-fflags nobuffer": it drops the probed keyframe and nothing decodes until the next one, 20 s later.
            dec = subprocess.Popen(["ffmpeg", "-v", "error", "-probesize", "32", "-analyzeduration", "0",
                                    "-flags", "low_delay", "-f", "h264", "-i", "-", "-fps_mode", "passthrough",
                                    "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                                   stdin=rec.stdout, stdout=subprocess.PIPE)
            self.procs = [rec, dec]
            while len(b := dec.stdout.read(size)) == size:
                self.frame = np.frombuffer(b, np.uint8).reshape(270, 480, 3)
            self.stop()

    def stop(self):
        for p in self.procs:
            p.kill()


def look(f):
    """Reduces a 480x270 frame to what the policy needs."""
    q = f.astype(np.int16)
    r, g, b = q[..., 0], q[..., 1], q[..., 2]
    gray = q.mean(axis=2)
    hood = (b > 90) & (b > r + 50) & (b > g + 25)
    hood[:30, :] = False  # hearts and item buttons
    ys, xs = np.nonzero(hood)
    link = (int(np.median(xs)), int(np.median(ys))) if len(xs) > 30 else None
    hearts = int(((r > 180) & (g < 80) & (b < 80))[5:26, 4:110].sum())
    # Text boxes: neutral white glyphs spread over a dark panel in the lower centre of the screen
    # (sword trails are bright too, but blue and compact).
    white = (np.minimum(np.minimum(r, g), b) > 205)[175:255, 100:380]
    text = float(white.any(axis=0).mean()) if gray[175:255, 100:380].mean() < 55 else 0.0
    # Coarse, contrast-normalised thumbnail of the playfield to recognise places already seen.
    t = gray[30:, :].reshape(30, 8, 60, 8).mean(axis=(1, 3))
    t = (t - t.mean()) / (t.std() + 1)
    return {"gray": gray[::2, ::2], "dark": float(gray[20:].mean()) < 14, "link": link,
            "hearts": hearts, "text": text, "thumb": t}


def toward(dx, dy):
    return int(round(np.arctan2(dy, dx) / (np.pi / 4))) % 8


if not focused():
    print("play.py: Eden is not focused, not sending input", file=sys.stderr)
    sys.exit(1)

screen = Screen()
while screen.frame is None:
    time.sleep(0.05)
start = time.monotonic()
heading, hold_until = rng.randrange(8), 0.0
value = [0.5] * 8  # optimistic: untried headings look promising
blocked = {}  # heading -> when it was last found blocked
memory = []  # thumbnails of places seen
flicker = np.zeros((135, 240))  # where the picture changes on its own (torches, water)
prev = None
swung, fights, calm_until, saved = 0.0, 0, 0.0, {}
last_frame = last_focus = last_step = start
next_swing, next_bow, next_bomb = start + 1.0, start + 6.0, start + 15.0
try:
    while time.monotonic() - start < dur:
        now = time.monotonic()
        if now - last_focus > 15:
            if not focused():
                print("play.py: Eden lost focus, stopping", file=sys.stderr)
                break
            last_focus = now
        time.sleep(max(0.0, 0.4 - (now - last_step)))  # decide at most every 0.4 s
        last_step = now = time.monotonic()
        f = screen.frame
        s = look(f)
        if now - last_frame >= 10:
            Image.fromarray(f).save(os.path.join(out, "frames", f"{int(now - start):04d}.png"))
            last_frame = now
        moved = np.abs(prev["gray"] - s["gray"]) > 25 if prev else np.ones((135, 240), bool)
        delta = float(moved.mean())
        prev = s
        novelty = min((float(np.abs(m - s["thumb"]).mean()) for m in memory), default=1.0)
        if novelty > 0.35:
            memory.append(s["thumb"])
        state, enemy = "explore", None

        if s["dark"]:
            state = "dark"  # room transition or loading: keep walking through the door
        elif s["text"] > 0.25:
            state = "text"
            stick((0, 0))
            press("b")
        elif s["hearts"] == 0 and s["link"] is None:
            state = "gameover"  # "Continue" is the default choice
            stick((0, 0))
            press("a")
        else:
            value[heading] = 0.7 * value[heading] + 0.3 * min(novelty, 1.0)
            if delta < 0.05 and s["link"] and now - swung > 0.8:
                # Camera still: things that move away from Link and are not known flicker are enemies.
                flicker = 0.85 * flicker + 0.15 * moved
                lx, ly = s["link"][0] // 2, s["link"][1] // 2
                cand = moved & (flicker < 0.3)
                yy, xx = np.nonzero(cand)
                far = (np.abs(xx - lx) > 30) | (np.abs(yy - ly) > 25)  # Link and his sword trail
                xx, yy = xx[far], yy[far]
                near = (np.abs(xx - lx) < 80) & (np.abs(yy - ly) < 60)
                if near.sum() >= 4 and now > calm_until:
                    enemy = (int(np.median(xx[near])) - lx, int(np.median(yy[near])) - ly)
            fights = fights + 1 if enemy else 0
            if fights >= 4:
                calm_until = now + 3.0  # probably chasing a flicker: explore for a while
            if enemy:
                state = "fight"
                heading = toward(*enemy)
                hold_until = now + 1.0
                stick(DIRS[heading])
                for _ in range(rng.randint(2, 3)):
                    press("y", 60)
                    time.sleep(0.1)
                swung = time.monotonic()
            else:
                if delta < 0.008 and now > hold_until - 2.5:
                    # The picture barely moved while walking: a wall, a block or a locked door.
                    state = "blocked"
                    blocked[heading] = now
                    value[heading] *= 0.5
                    press("y")  # cut whatever is in the way
                    if rng.random() < 0.25:
                        press("a")  # opens a chest or a door, or lifts a bomb...
                        time.sleep(0.3)
                        press("a")  # ...and throws it at a cracked wall
                    hold_until = 0.0
                if now > hold_until:
                    free = [h for h in range(8) if now - blocked.get(h, -99) > 8] or list(range(8))
                    if rng.random() < 0.2:
                        heading = rng.choice(free)
                    else:
                        heading = max(free, key=lambda h: value[h] + rng.uniform(0, 0.1))
                    hold_until = now + rng.uniform(1.5, 4.0)
                stick(DIRS[heading])
                if now > next_swing:
                    state += "+sword"
                    if rng.random() < 0.2:
                        key("y", True)
                        time.sleep(1.3)  # charge a spin attack while walking
                        key("y", False)
                    else:
                        press("y", 60)
                    swung = time.monotonic()
                    next_swing = now + rng.uniform(1.0, 2.5)
            if now > next_bow:
                state += "+bow"
                press("x", 100)
                next_bow = now + rng.uniform(5, 10)
            if now > next_bomb:
                state += "+bomb"
                press("a")  # Link lifts a bomb...
                time.sleep(0.3)
                press("a")  # ...and throws it
                heading = (heading + 4) % 8  # walk away from it
                hold_until = now + 2.0
                stick(DIRS[heading])
                next_bomb = now + rng.uniform(15, 30)
        if state in ("text", "gameover") and saved.get(state, 0) < 3:
            saved[state] = saved.get(state, 0) + 1
            Image.fromarray(f).save(os.path.join(out, "frames", f"{state}{saved[state]}.png"))
        log.write(f"{now - start:6.1f} {state:18s} h={heading} delta={delta:.3f} new={novelty:.2f} "
                  f"places={len(memory)} link={s['link']} enemy={enemy} hearts={s['hearts']} "
                  f"text={s['text']:.4f}\n")
finally:
    screen.done = True
    screen.stop()
    send("pkill screenrecord")
    stick((0, 0))
    for k in KEYS:
        key(k, False)
    sh.stdin.close()
    sh.wait(timeout=5)
    log.close()
