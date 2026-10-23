#!/usr/bin/env python3
"""Record Smash Ultimate matches through the game bridge, one gzip file per match.

Each file starts with a "# {json}" header (build, watch layout), then the raw bridge frame
lines of the match: pads plus every smash_state field and the WorkModule arrays of each
fighter. With --auto it also drives the menus so CPU matches chain on their own: A on the
results, + on the character select. Set it up once with the players as CPU and a rule set
whose stage selection is random (no stage screen; the random list holds the stages wanted),
and never press B there: it picks up a CPU token. It stops when someone touches a controller, or when the
console battery gets low.

With --agent, the model plays the player whose name tag is --name (Ultimus): give the tag
to a player on the character select and its controller is the model's for the match.
Offline only, never against people online: it stops when the other player is not a CPU,
unless --sparring (a local human) or --selfplay (the model plays every human slot).
Matches with the model go to ~/smash-ai/play, the others to ~/smash-ai/rec. It also stops
once smash_rl.py writes "selfplay" in ~/smash-ai/phase, for the CPU slot to become a player.

On a PC, --sync 1 makes the game wait for each frame to be handled, so an instance running
without speed limit never outpaces the agent; --min-battery 0 skips the console checks.

  smash_record.py [--host H] [--port 6560] [--sync K] [--out DIR] [--auto] [--matches N]
                  [--min-battery 15]
                  [--agent ~/smash-ai/models/ultimus.pt [--name Ultimus] [--sparring | --selfplay]]
"""
import argparse
import gzip
import json
import os
import signal
import subprocess
import sys
import time

import smash_farm
import smash_state
from bridge import Bridge, Frame

ADB = os.path.expanduser("~/Library/Android/sdk/platform-tools/adb")
LOCK = "/data/local/tmp/odin3.lock"
PRESS_FRAMES, PRESS_EVERY = 6, 90
BUTTON_A, BUTTON_PLUS = 0x1, 0x400
# When + does not start the match, the agent's player has lost its fighter (a late A on the
# character select): its hand goes to the top left corner, then onto the first fighter, Mario.
# For player 1, smash_farm finds its way back to the character select (the presses may have
# wandered off into other menus) and looks for his portrait, as the grid grows with the
# fighters that matches unlock. That takes a minute, and the + presses already reach
# PICK_AFTER on the results: it waits for a minute without match first.
PICK_AFTER = 4
NAVIGATE_AFTER = 40
PICK = ([(0, -32767, 0)] * 120 + [(0, 0, 32767)] * 80 + [(0, 0, -32767)] * 13
        + [(0, 32767, 0)] * 11 + [(0, 0, 0)] * 10 + [(BUTTON_A, 0, 0)] * PRESS_FRAMES)


def log(*args):
    print(time.strftime("%H:%M:%S"), *args, flush=True)


def battery():
    out = subprocess.run([ADB, "shell", "dumpsys", "battery"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if line.strip().startswith("level:"):
            return int(line.split(":")[1])
    return 100


def phase():
    try:
        with open(os.path.expanduser("~/smash-ai/phase")) as f:
            return f.read().strip()
    except OSError:
        return "cpu"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=6560)
    ap.add_argument("--sync", type=int, default=0, help="lockstep, at most this many frames ahead")
    ap.add_argument("--out")
    ap.add_argument("--players", type=int, default=2)
    ap.add_argument("--auto", action="store_true", help="chain matches by driving the menus")
    ap.add_argument("--matches", type=int, default=0, help="stop after this many matches")
    ap.add_argument("--min-battery", type=int, default=15)
    ap.add_argument("--release-lock", action="store_true",
                    help=f"remove {LOCK} on exit (the caller took it)")
    ap.add_argument("--agent", help="model that plays the player named --name")
    ap.add_argument("--name", default="Ultimus")
    ap.add_argument("--sparring", action="store_true", help="let the agent face a local human")
    ap.add_argument("--selfplay", action="store_true", help="the agent plays every human slot")
    ap.add_argument("--level", type=int, help="level of the CPU, kept in the match header")
    a = ap.parse_args()
    # Started in the background, the process ignores SIGINT: both signals end it cleanly.
    def stop(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    agents, active = {}, {}  # model per slot, and the slots it plays this match
    if a.agent:
        from smash_bc import Agent

    b = Bridge(a.host, a.port)
    b.ask("stream 0", "ok")  # drops the frames sent since the connection
    info = b.ask("info", "info").split()
    b.ask("unwatch *", "ok")
    # entry count at +0, is_ready_go at +0x32, is_result_mode at +0x49
    b.ask(f"watch fm 0x50 {smash_state.FIGHTER_MANAGER} 0 0xa0", "ok")
    b.ask(smash_state.STAGE_WATCH, "ok")
    smash_state.watch_all(b, a.players, work=True)
    layout = {name: [length, [f"{o:#x}" for o in offs]]
              for name, (length, offs) in {**smash_state.FIELDS, **smash_state.WORK}.items()}
    b.ask("stream 1", "ok")
    if a.sync:
        b.sync(a.sync)

    out, path, frames, gaps, last, me = None, None, 0, 0, None, None
    matches, press_until, next_press, pressed, flags = 0, 0, -PRESS_EVERY, 0, None
    starts, steps, picker = 0, [], None  # + presses on this character select, PICK in progress
    quiet, quiet_until = set(), 0  # slots the agent played, their last input still shows a while
    try:
        for line in b.frames(raw=True):
            f = Frame(line)
            fm = f.watches.get("fm")
            count, ready, result = (fm[0], fm[0x32], fm[0x49]) if fm else (0, 0, 0)
            if (count, ready, result) != flags:
                flags = (count, ready, result)
                log(f"fighters {count} ready_go {ready} result {result}")
            fighting = count >= a.players and not result and f.watches.get("0pos") is not None

            fighters = smash_state.parse(f, a.players) if fighting else {}
            if out is None and len(fighters) == a.players:
                tags = smash_state.names(f, a.players)
                me = next((i for i, t in tags.items() if t == a.name), None) if a.agent else None
                if a.agent and me is None:  # its pad would stay idle, a match to learn nothing from
                    log(f"no player named {a.name}, stopping")
                    return
                active = {}
                if me is not None:
                    for i, x in fighters.items():
                        if i != me and not x.cpu and not a.sparring and not a.selfplay:
                            log(f"player {i + 1} is not a CPU, stopping")
                            return
                        if i == me or (not x.cpu and a.selfplay):
                            if i not in agents:
                                agents[i] = Agent(a.agent, i)
                            active[i] = agents[i]
                folder = a.out or ("~/smash-ai/play" if active else "~/smash-ai/rec")
                os.makedirs(os.path.expanduser(folder), exist_ok=True)
                path = os.path.join(os.path.expanduser(folder), time.strftime("%Y%m%d-%H%M%S")
                                    + (f"-{a.port}" if a.port != 6560 else "") + ".txt.gz")
                out = gzip.open(path + ".part", "wt", compresslevel=6)
                header = {"title": info[1], "build": info[2], "players": a.players,
                          "manager": smash_state.FIGHTER_MANAGER, "layout": layout,
                          "names": tags, "agent": a.agent if active else None, "me": me,
                          "agents": sorted(active), "level": a.level,
                          "version": next(iter(active.values())).model.version if active else None}
                out.write("# " + json.dumps(header) + "\n")
                frames, gaps, last = 0, 0, None
                log(f"match -> {path}" + (f", {a.name} is player {me + 1}" if me is not None else ""))
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
                    for i, ag in active.items():
                        ag.reset()
                        b.release(i)
                    quiet, quiet_until, active = set(active), f.index + PRESS_EVERY, {}
                    fighters = smash_state.parse(f, a.players)
                    stage = f.watches.get("stage")
                    log(f"saved {frames} frames, {gaps} gaps, "
                        + f"stage {int.from_bytes(stage, 'little') if stage else '-'}, "
                        + ", ".join(f"p{i} kind {x.kind:#x} stock {x.stock}" for i, x in fighters.items()))
                    if any([ag.reload() for ag in agents.values()]):
                        log("model reloaded")
                    if agents and not a.selfplay and phase() == "selfplay":
                        log(f"{a.name} beats the CPU: make the CPU a player and restart with --selfplay")
                        return
                    level = battery() if a.min_battery else 100
                    if level < a.min_battery:
                        log(f"battery {level} %, stopping")
                        return
                    if a.matches and matches >= a.matches:
                        return

            if active and len(fighters) == a.players:
                for i, ag in active.items():
                    b.pad(i, *ag.act(fighters))

            if not a.auto:
                continue
            # Menu presses go to every controller (on the results only players' ones count) and can
            # show up a little late, so their buttons are ignored for a while.
            ours = pressed if f.index < next_press + PRESS_EVERY else 0
            touched = {i: hex(p[0]) for i, p in f.pads.items()
                       if p[0] & ~ours and i not in active and i != picker
                       and not (i in quiet and f.index < quiet_until)}
            if touched:
                log(f"controller input from someone else {touched}, stopping")
                return
            if steps:
                b.pad(picker, *steps.pop(0))
                if not steps:
                    b.release(picker)
                    pressed, next_press, starts = BUTTON_A, f.index, 0
                continue
            if fighting:
                starts, picker = 0, None
            if press_until and f.index >= press_until:
                for i in range(a.players):
                    b.release(i)
                press_until = 0
            elif not fighting and f.index >= next_press + PRESS_EVERY:
                if count == 0:
                    tags = smash_state.names(f, a.players)
                    slot = next((i for i, t in tags.items() if t == a.name), None) if a.agent else None
                    # No tag read: off the character select, most likely.
                    if starts >= NAVIGATE_AFTER and a.agent and slot in (0, None):
                        log("the match does not start, player 1 goes back to the character select")
                        nav = smash_farm.Instance(a.port - 6560, argparse.Namespace(
                            cpu=[], home=os.path.expanduser("~/smash-ai")))
                        nav.level = a.level or 9
                        if not nav.navigate(b):
                            log("character select not reached")
                        b.ask("stream 1", "ok")  # navigate stops the stream when done
                        if a.sync:
                            b.sync(a.sync)
                        starts, picker = 0, 0  # its stick may still show until the match
                        continue
                    if starts >= PICK_AFTER and slot not in (0, None):
                        log(f"the match does not start, player {slot + 1} picks Mario")
                        steps, picker = list(PICK), slot
                        continue
                    starts += 1
                    pressed = BUTTON_PLUS  # character select: start the match
                elif result:
                    pressed = BUTTON_A  # results
                else:
                    continue  # match loading or ending
                for i in range(a.players):
                    b.pad(i, pressed)
                press_until = f.index + PRESS_FRAMES
                next_press = f.index
    except KeyboardInterrupt:
        pass
    finally:
        if out is not None:
            out.close()
            os.rename(path + ".part", path)
            log(f"partial match kept: {path}")
        for i in {0, *agents}:
            b.release(i)
        if a.release_lock:
            subprocess.run([ADB, "shell", "rm", "-f", LOCK])
            log("console lock released")


if __name__ == "__main__":
    sys.exit(main())
