#!/usr/bin/env python3
"""Record Smash Ultimate matches through the game bridge, one gzip file per match.

Each file starts with a "# {json}" header (build, watch layout), then the raw bridge frame
lines of the match: pads plus every smash_state field and the WorkModule arrays of each
fighter. With --auto it also drives the menus so CPU matches chain on their own: A on the
results, + on the character select. Set it up once with the players as CPU and a rule set
whose stage selection is random (no stage screen; the random list holds the stages wanted),
and never press B there: it picks up a CPU token. It stops when someone touches a controller, or when the
console battery gets low.

With --agent, a model trained by smash_bc.py plays fighter --me against the CPU (offline
only, never against people online) and its matches go to ~/smash-ai/play.

  smash_record.py [--out ~/smash-ai/rec] [--auto] [--matches N] [--min-battery 15]
                  [--agent ~/smash-ai/models/bc.pt [--me 0]]
"""
import argparse
import gzip
import json
import os
import signal
import subprocess
import sys
import time

import smash_state
from bridge import Bridge, Frame

ADB = os.path.expanduser("~/Library/Android/sdk/platform-tools/adb")
LOCK = "/data/local/tmp/odin3.lock"
PRESS_FRAMES, PRESS_EVERY = 6, 90
BUTTON_A, BUTTON_PLUS = 0x1, 0x400


def log(*args):
    print(time.strftime("%H:%M:%S"), *args, flush=True)


def battery():
    out = subprocess.run([ADB, "shell", "dumpsys", "battery"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if line.strip().startswith("level:"):
            return int(line.split(":")[1])
    return 100


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--out")
    ap.add_argument("--players", type=int, default=2)
    ap.add_argument("--auto", action="store_true", help="chain matches by driving the menus")
    ap.add_argument("--matches", type=int, default=0, help="stop after this many matches")
    ap.add_argument("--min-battery", type=int, default=15)
    ap.add_argument("--release-lock", action="store_true",
                    help=f"remove {LOCK} on exit (the caller took it)")
    ap.add_argument("--agent", help="model that plays fighter --me")
    ap.add_argument("--me", type=int, default=0)
    ap.add_argument("--sparring", action="store_true", help="let the agent face a local human")
    a = ap.parse_args()
    a.out = os.path.expanduser(a.out or ("~/smash-ai/play" if a.agent else "~/smash-ai/rec"))
    os.makedirs(a.out, exist_ok=True)
    # Started in the background, the process ignores SIGINT: both signals end it cleanly.
    def stop(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    agent = None
    if a.agent:
        from smash_bc import Agent
        agent = Agent(a.agent, a.me)

    b = Bridge(a.host)
    b.ask("stream 0", "ok")  # drops the frames sent since the connection
    info = b.ask("info", "info").split()
    b.ask("unwatch *", "ok")
    # entry count at +0, is_ready_go at +0x32, is_result_mode at +0x49
    b.ask(f"watch fm 0x50 {smash_state.FIGHTER_MANAGER} 0 0xa0", "ok")
    smash_state.watch_all(b, a.players, work=True)
    layout = {name: [length, [f"{o:#x}" for o in offs]]
              for name, (length, offs) in {**smash_state.FIELDS, **smash_state.WORK}.items()}
    b.ask("stream 1", "ok")

    out, path, frames, gaps, last = None, None, 0, 0, None
    matches, press_until, next_press, pressed, flags = 0, 0, -PRESS_EVERY, 0, None
    try:
        for line in b.frames(raw=True):
            f = Frame(line)
            fm = f.watches.get("fm")
            count, ready, result = (fm[0], fm[0x32], fm[0x49]) if fm else (0, 0, 0)
            if (count, ready, result) != flags:
                flags = (count, ready, result)
                log(f"fighters {count} ready_go {ready} result {result}")
            fighting = count >= a.players and not result and f.watches.get("0pos") is not None

            if fighting and out is None:
                path = os.path.join(a.out, time.strftime("%Y%m%d-%H%M%S") + ".txt.gz")
                out = gzip.open(path + ".part", "wt", compresslevel=6)
                header = {"title": info[1], "build": info[2], "players": a.players,
                          "manager": smash_state.FIGHTER_MANAGER, "layout": layout,
                          "agent": a.agent, "me": a.me if a.agent else None}
                out.write("# " + json.dumps(header) + "\n")
                frames, gaps, last = 0, 0, None
                log(f"match -> {path}")
            if out is not None:
                if fighting:
                    out.write(line + "\n")
                    frames += 1
                    if last is not None and f.index != last + 1:
                        gaps += 1
                    last = f.index
                else:
                    out.close()
                    os.rename(path + ".part", path)
                    out = None
                    matches += 1
                    if agent:
                        agent.reset()
                        b.release(a.me)
                    fighters = smash_state.parse(f, a.players)
                    log(f"saved {frames} frames, {gaps} gaps, "
                        + ", ".join(f"p{i} kind {x.kind:#x} stock {x.stock}" for i, x in fighters.items()))
                    level = battery()
                    if level < a.min_battery:
                        log(f"battery {level} %, stopping")
                        return
                    if a.matches and matches >= a.matches:
                        return

            if agent and fighting:
                fighters = smash_state.parse(f, a.players)
                if len(fighters) == 2:
                    if not a.sparring and not fighters[1 - a.me].cpu:
                        log("the opponent is not a CPU, stopping")
                        return
                    b.pad(a.me, *agent.act(fighters))

            if not a.auto:
                continue
            # Our own presses can show up a little late, so their buttons are ignored for a while.
            ours = pressed if f.index < next_press + PRESS_EVERY else 0
            touched = {i: hex(p[0]) for i, p in f.pads.items()
                       if p[0] & ~(ours if i == 0 else 0) and not (agent and i == a.me)}
            if touched:
                log(f"controller input from someone else {touched}, stopping")
                return
            if press_until and f.index >= press_until:
                b.release(0)
                press_until = 0
            elif not fighting and f.index >= next_press + PRESS_EVERY:
                if count == 0:
                    pressed = BUTTON_PLUS  # character select: start the match
                elif result:
                    pressed = BUTTON_A  # results
                else:
                    continue  # match loading or ending
                b.pad(0, pressed)
                press_until = f.index + PRESS_FRAMES
                next_press = f.index
    except KeyboardInterrupt:
        pass
    finally:
        if out is not None:
            out.close()
            os.rename(path + ".part", path)
            log(f"partial match kept: {path}")
        b.release(0)
        b.release(a.me)
        if a.release_lock:
            subprocess.run([ADB, "shell", "rm", "-f", LOCK])
            log("console lock released")


if __name__ == "__main__":
    sys.exit(main())
