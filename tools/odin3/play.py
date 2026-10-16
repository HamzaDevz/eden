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
#
# With SCENARIO=<file>, plays that scenario instead: the same route through the game on every run, from the
# save run_variant.sh restores (<seconds> is then only a safety limit). One step per line, "#" comments:
#   walk <dir> <s>                    left stick for <s> s; dir is n, ne, e... or a bearing in degrees
#   walk <dir> until <cond> [max <s>] cond: dark, light, text, notext, still (picture stopped), moving
#   door <dir> [max <s>]              walks into a door until the screen goes dark, sidestepping a little
#                                     further each way while it does not (doors are narrower than the drift)
#   stick <dir>|0                     leaves the stick there while the next steps run
#   rstick <dir> <s>                  right stick: camera, map zoom (s = out)
#   press <btn> [ms] [xN]             a b x y l r zl zr plus minus up down left right
#   hold <btn> <s>
#   mash <btn> <s>                    one press every 0.3 s: dialogue, cutscenes
#   wait <s> | wait <cond> [max <s>]
#   auto <s>                          the autonomous player above
#   mark <segment>                    starts a segment; summarize.py reports each one
#   check <name> [min]                compares the picture with the one an AUTHOR=1 run saved
#   repeat <n> ... end
# A wait that times out or a check below <min> (default 0.5) is logged as DIVERGED: the run left the route.
import os, random, re, subprocess, sys, threading, time
import numpy as np
from PIL import Image

ADB = [os.path.expanduser("~/Library/Android/sdk/platform-tools/adb")]
PKG = os.environ.get("PKG", "com.miHoYo.Yuanshen")
EV = "/dev/input/event8"
KEYS = {"a": 304, "b": 305, "x": 307, "y": 308, "l": 310, "r": 311, "zl": 312, "zr": 313, "minus": 314, "plus": 315}
HAT = {"up": (17, -1), "down": (17, 1), "left": (16, -1), "right": (16, 1)}
DIRS = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)]  # y > 0 is down
BEARING = {"n": 0, "ne": 45, "e": 90, "se": 135, "s": 180, "sw": 225, "w": 270, "nw": 315}

dur, out = float(sys.argv[1]), sys.argv[2]
rng = random.Random(int(sys.argv[3]) if len(sys.argv) > 3 else 1)
os.makedirs(os.path.join(out, "frames"), exist_ok=True)
log = open(os.path.join(out, "play.log"), "w")
sh = subprocess.Popen(ADB + ["shell", "-T", "sh"], stdin=subprocess.PIPE, text=True)


def send(s):
    sh.stdin.write(s + "\n")
    sh.stdin.flush()


def stick_event(d, axes=(0, 1)):
    n = max(1.0, float(np.hypot(*d)))
    x, y = (int(c / n * 32767) for c in d)
    return f"sendevent {EV} 3 {axes[0]} {x}; sendevent {EV} 3 {axes[1]} {y}; sendevent {EV} 0 0 0"


def stick(d, axes=(0, 1)):
    send(stick_event(d, axes))


def timed(on, seconds, off):
    """Holds an input for exactly <seconds>: the device times it, not the link (WiFi adb jitters by 100 ms or more)."""
    send(f"{on}; usleep {int(seconds * 1e6)}; {off}")
    time.sleep(seconds)


def event(name, down):
    if name in HAT:
        axis, v = HAT[name]
        return f"sendevent {EV} 3 {axis} {v if down else 0}; sendevent {EV} 0 0 0"
    return f"sendevent {EV} 1 {KEYS[name]} {int(down)}; sendevent {EV} 0 0 0"


def key(name, down):
    send(event(name, down))


def press(name, ms=80):
    send(f"{event(name, True)}; usleep {ms * 1000}; {event(name, False)}")


def focused():
    w = subprocess.run(ADB + ["shell", "dumpsys window | grep mCurrentFocus"], capture_output=True, text=True).stdout
    return f"{PKG}/org.yuzu.yuzu_emu.activities.EmulationActivity" in w


class Screen(threading.Thread):
    """Keeps the latest frame of the stream, restarting screenrecord if it dies."""

    def __init__(self):
        super().__init__(daemon=True)
        self.frame, self.procs, self.done = None, [], False
        self.times = []  # arrival of every frame, so glitch.py can place what it finds in stream.h264
        self.start()

    def run(self):
        size = 480 * 270 * 3
        while not self.done:
            # A screenrecord that stops on its own asks the media scanner to scan "-", which then keeps a prime core
            # at 4.3 GHz for about a minute (12-18 W): no time limit, and only ever killed with SIGKILL.
            rec = subprocess.Popen(ADB + ["exec-out", "screenrecord --time-limit 0 --size 480x270 --bit-rate 1000000 "
                                          "--output-format=h264 -"], stdout=subprocess.PIPE)
            tee = subprocess.Popen(["tee", "-a", os.path.join(out, "stream.h264")], stdin=rec.stdout,
                                   stdout=subprocess.PIPE)
            # No "-fflags nobuffer": it drops the probed keyframe and nothing decodes until the next one, 20 s later.
            dec = subprocess.Popen(["ffmpeg", "-v", "error", "-probesize", "32", "-analyzeduration", "0",
                                    "-flags", "low_delay", "-f", "h264", "-i", "-", "-fps_mode", "passthrough",
                                    "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                                   stdin=tee.stdout, stdout=subprocess.PIPE)
            self.procs = [rec, tee, dec]
            while len(b := dec.stdout.read(size)) == size:
                self.frame = np.frombuffer(b, np.uint8).reshape(270, 480, 3)
                self.times.append(time.monotonic())
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


def autoplay(seconds, start):
    """The autonomous player, for <seconds> s; logs times from <start>."""
    t0 = time.monotonic()
    heading, hold_until = rng.randrange(8), 0.0
    value = [0.5] * 8  # optimistic: untried headings look promising
    blocked = {}  # heading -> when it was last found blocked
    memory = []  # thumbnails of places seen
    flicker = np.zeros((135, 240))  # where the picture changes on its own (torches, water)
    prev = None
    swung, fights, calm_until, saved = 0.0, 0, 0.0, {}
    last_frame = last_focus = last_step = t0
    next_swing, next_bow, next_bomb = t0 + 1.0, t0 + 6.0, t0 + 15.0
    while time.monotonic() - t0 < seconds:
        now = time.monotonic()
        if now - last_focus > 15:
            if not focused():
                print("play.py: Eden lost focus, stopping", file=sys.stderr)
                return False
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
    stick((0, 0))
    return True


def parse(path):
    stack = [[]]
    for raw in open(path):
        t = raw.split("#")[0].split()
        if not t or t[0] == "save":
            continue
        if t[0] == "repeat":
            stack[-1].append(("repeat", int(t[1]), []))
            stack.append(stack[-1][-1][2])
        elif t[0] == "end":
            stack.pop()
        else:
            stack[-1].append(tuple(t))
    return stack[0]


def vec(d):
    if d == "0":
        return (0, 0)
    a = np.radians(BEARING[d] if d in BEARING else float(d))
    return (float(np.sin(a)), float(-np.cos(a)))


def until(cond, limit):
    """Waits for cond; still and moving need the picture to stay (un)changed for 0.5 s."""
    end, prev, since = time.monotonic() + limit, None, time.monotonic()
    while time.monotonic() < end:
        s = look(screen.frame)
        if cond in ("still", "moving"):
            ok = prev is not None and (float((np.abs(prev - s["gray"]) > 25).mean()) < 0.01) == (cond == "still")
            prev = s["gray"]
            if not ok:
                since = time.monotonic()
            elif time.monotonic() - since > 0.5:
                return True
        elif {"dark": s["dark"], "light": not s["dark"], "text": s["text"] > 0.25,
              "notext": s["text"] <= 0.25}[cond]:
            return True
        time.sleep(0.05)
    return False


def door(d, limit):
    end, k = time.monotonic() + limit, 0
    while time.monotonic() < end:
        stick(d)
        if until("dark", 1.0):
            stick((0, 0))
            return True
        k += 1
        sign = 1 if k % 2 else -1
        stick((-d[1] * sign, d[0] * sign))
        time.sleep(0.1 * k)
    stick((0, 0))
    return False


class Route:
    def __init__(self, path, start):
        self.name = os.path.splitext(os.path.basename(path))[0]
        self.refs = os.path.join(os.path.dirname(os.path.abspath(__file__)), "saves", "refs", self.name)
        self.author = os.environ.get("AUTHOR") == "1"
        os.makedirs(self.refs if self.author else os.path.join(out, "checks"), exist_ok=True)
        self.start, self.diverged, self.checks = start, 0, []
        self.segments, self.seg = open(os.path.join(out, "segments.csv"), "w"), None
        # Device clocks: SurfaceFlinger stamps presents with CLOCK_MONOTONIC, poll.sh with /proc/uptime.
        m = subprocess.run(ADB + ["shell", "dumpsys meminfo -c | head -2"], capture_output=True, text=True).stdout
        up, real = (int(x) for x in re.search(r"time,(\d+),(\d+)", m).groups())
        t0 = time.monotonic()
        boot = float(subprocess.run(ADB + ["shell", "cat /proc/uptime"], capture_output=True, text=True).stdout.split()[0])
        self.to_boot = boot - (t0 + time.monotonic()) / 2
        self.mono_minus_boot = (up - real) / 1000
        self.segments.write("segment,mono_start,mono_end,boot_start,boot_end\n")

    def note(self, msg):
        log.write(f"{time.monotonic() - self.start:6.1f} {msg}\n")
        log.flush()

    def close_segment(self):
        if self.seg:
            name, b0 = self.seg
            b1 = time.monotonic() + self.to_boot
            self.segments.write(f"{name},{b0 + self.mono_minus_boot:.3f},{b1 + self.mono_minus_boot:.3f},{b0:.3f},{b1:.3f}\n")
            self.segments.flush()
        self.seg = None

    def miss(self, what):
        self.diverged += 1
        self.note(f"DIVERGED {what}")
        Image.fromarray(screen.frame).save(os.path.join(out, "frames", f"diverged{self.diverged}.png"))

    def run(self, steps):
        for st in steps:
            if time.monotonic() - self.start > dur:
                return False
            if st[0] == "repeat":
                for _ in range(st[1]):
                    if not self.run(st[2]):
                        return False
                continue
            self.note(" ".join(st))
            c, a = st[0], st[1:]
            if c == "walk":
                if a[1] == "until":
                    stick(vec(a[0]))
                    ok = until(a[2], float(a[4]) if len(a) > 4 else 10)
                    stick((0, 0))
                    if not ok:
                        self.miss(" ".join(st))
                else:
                    timed(stick_event(vec(a[0])), float(a[1]), stick_event((0, 0)))
            elif c == "door":
                if not door(vec(a[0]), float(a[2]) if len(a) > 2 else 12):
                    self.miss(" ".join(st))
            elif c == "stick":
                stick(vec(a[0]))
            elif c == "rstick":
                timed(stick_event(vec(a[0]), (2, 5)), float(a[1]), stick_event((0, 0), (2, 5)))
            elif c == "press":
                ms = int(a[1]) if len(a) > 1 and a[1].isdigit() else 80
                n = int(a[-1][1:]) if a[-1].startswith("x") else 1
                for _ in range(n):
                    press(a[0], ms)
                    time.sleep(ms / 1000 + 0.15)
            elif c == "hold":
                timed(event(a[0], True), float(a[1]), event(a[0], False))
            elif c == "mash":
                end = time.monotonic() + float(a[1])
                while time.monotonic() < end:
                    press(a[0])
                    time.sleep(0.3)
            elif c == "wait":
                if re.fullmatch(r"[\d.]+", a[0]):
                    time.sleep(float(a[0]))
                elif not until(a[0], float(a[2]) if len(a) > 2 else 10):
                    self.miss(" ".join(st))
            elif c == "auto":
                if not autoplay(float(a[0]), self.start):
                    return False
            elif c == "mark":
                self.close_segment()
                self.seg = (a[0], time.monotonic() + self.to_boot)
            elif c == "check":
                f, ref = screen.frame, os.path.join(self.refs, a[0] + ".png")
                if self.author:
                    Image.fromarray(f).save(ref)
                elif os.path.exists(ref):
                    Image.fromarray(f).save(os.path.join(out, "checks", a[0] + ".png"))
                    r = look(np.asarray(Image.open(ref).convert("RGB")))["thumb"].ravel()
                    sim = float(np.corrcoef(r, look(f)["thumb"].ravel())[0, 1])
                    self.checks.append((a[0], sim))
                    self.note(f"check {a[0]} {sim:.2f}")
                    if sim < (float(a[1]) if len(a) > 1 else 0.5):
                        self.miss(f"check {a[0]} {sim:.2f}")
            else:
                raise SystemExit(f"play.py: unknown step {st}")
        return True


if not focused():
    print("play.py: Eden is not focused, not sending input", file=sys.stderr)
    sys.exit(1)

screen = Screen()
while screen.frame is None:
    time.sleep(0.05)
start = time.monotonic()
try:
    if os.environ.get("SCENARIO"):
        route = Route(os.environ["SCENARIO"], start)
        done = route.run(parse(os.environ["SCENARIO"]))
        route.close_segment()
        ok = sum(1 for _, s in route.checks if s >= 0.5)
        with open(os.path.join(out, "route.txt"), "w") as r:
            r.write(f"route={route.name} finished={done} seconds={time.monotonic() - start:.0f} "
                    f"diverged={route.diverged} checks={ok}/{len(route.checks)}\n")
    else:
        autoplay(dur, start)
finally:
    screen.done = True
    screen.stop()
    send("pkill -9 screenrecord")
    with open(os.path.join(out, "stream.txt"), "w") as f:
        f.writelines(f"{t - start:.3f}\n" for t in screen.times)
    stick((0, 0))
    stick((0, 0), (2, 5))
    for k in list(KEYS) + list(HAT):
        key(k, False)
    sh.stdin.close()
    sh.wait(timeout=5)
    log.close()
