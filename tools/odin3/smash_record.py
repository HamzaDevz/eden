#!/usr/bin/env python3
"""Record Smash Ultimate matches through the game bridge, one gzip file per match.

Each file starts with a "# {json}" header (build, watch layout), then the raw bridge frame
lines of the match: pads plus every smash_state field and the WorkModule arrays of each
fighter. With --auto it also drives the menus so CPU matches chain on their own: A on the
results, + on the character select. Set it up once with the players as CPU and a rule set
whose stage selection is random (no stage screen; the random list holds the stages wanted),
and never press B there: it picks up a CPU token. It stops when someone touches a controller, or when the
console battery gets low (exit status 3, for the caller to wait for the charger).

With --agent, the model plays the player whose name tag is --name (Ultimus): give the tag
to a player on the character select and its controller is the model's for the match.
Offline only, never against people online: it stops when the other player is not a CPU,
unless --sparring (local humans) or --selfplay (the model plays every human slot). With
--players 4 (up to 8) it also plays matches of three or more, kept in ~/smash-ai/party.
With --pad P (mode IA), a CPU given Mario on the character select is the model's, through
controller P, its own (player 8 is connected without a pad in Smash's game config): L+R there
takes a CPU's card (Smash gives one to a joining controller), its hand picks Mario, checked in
Smash's card table, and at the start of each match it finds its fighter by swinging its stick
until one fighter's stick follows. A tagged player still goes first.
Matches with the model go to ~/smash-ai/play, the others to ~/smash-ai/rec. It also stops
once smash_rl.py writes "selfplay" in ~/smash-ai/phase, for the CPU slot to become a player.

On a PC, --sync 1 makes the game wait for each frame to be handled, so an instance running
without speed limit never outpaces the agent; --min-battery 0 skips the console checks.

  smash_record.py [--host H] [--port 6560] [--sync K] [--out DIR] [--auto] [--matches N]
                  [--min-battery 15]
                  [--agent ~/smash-ai/models/ultimus.pt [--name Ultimus | --pad 7]
                   [--sparring | --selfplay]]
"""
import argparse
import gzip
import json
import os
import signal
import subprocess
import sys
import time

import numpy as np

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


def pick_path(down):
    """The hand to the top left corner, down for that many frames and a little right, then A."""
    return ([(0, -32767, 0)] * 120 + [(0, 0, 32767)] * 80 + [(0, 0, -32767)] * down
            + [(0, 32767, 0)] * 11 + [(0, 0, 0)] * 10 + [(BUTTON_A, 0, 0)] * PRESS_FRAMES)


PICK = pick_path(13)
# With --pad: L+R takes the card, then a pick path. The grid's rows shrink as matches unlock
# fighters, so when the card shows another fighter B takes the token back for the next path.
# The probe's stick swings every PROBE_HALF frames; the stick a fighter reads comes back a few
# frames later, maybe mirrored.
JOIN = [(0xc0, 0, 0)] * PRESS_FRAMES + [(0, 0, 0)] * 30
RETRY = [(0x2, 0, 0)] * PRESS_FRAMES + [(0, 0, 0)] * 10
SETTLE = [(0, 0, 0)] * 20  # before the card table shows the pick
PICK_DOWNS = (7, 13, 4, 10)
PROBE_HALF, PROBE_WINDOW, PROBE_LAGS, PROBE_FRAMES = 8, 60, 12, 900
LOOK_EVERY = 180


def log(*args):
    print(time.strftime("%H:%M:%S"), *args, flush=True)


def battery():
    out = subprocess.run([ADB, "shell", "dumpsys", "battery"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if line.strip().startswith("level:"):
            return int(line.split(":")[1])
    return 100


def identify(probe):
    """Player whose stick followed the probe's over its last PROBE_WINDOW frames, else None."""
    if len(probe) < PROBE_WINDOW:
        return None
    window = probe[-PROBE_WINDOW:]
    sent = np.sign([x for x, _ in window])
    n = PROBE_WINDOW - PROBE_LAGS
    scores = {}
    for i in window[-1][1]:
        stick = np.sign([sticks.get(i, 0) for _, sticks in window])
        scores[i] = max(abs(np.mean(sent[:n] * stick[lag:lag + n])) for lag in range(PROBE_LAGS))
    second, best = sorted(scores.values())[-2:]
    return max(scores, key=scores.get) if best > 0.8 and second < 0.5 else None


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
    ap.add_argument("--pad", type=int, help="the agent's own controller (0 for player 1) "
                    "instead of a name tag: it joins on the character select")
    ap.add_argument("--sparring", action="store_true", help="let the agent face a local human")
    ap.add_argument("--selfplay", action="store_true", help="the agent plays every human slot")
    ap.add_argument("--level", type=int, help="level of the CPU, kept in the match header")
    ap.add_argument("--level-file", help="CPU level to follow, read after each match: player 1 "
                    "then goes back through the character select to set it")
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
    smash_state.watch_all(b, a.players, work=a.pad is None)
    if a.pad is not None:
        smash_state.watch_cards(b)
    layout = {name: [length, [f"{o:#x}" for o in offs]]
              for name, (length, offs) in {**smash_state.FIELDS, **smash_state.WORK}.items()}
    b.ask("stream 1", "ok")
    if a.sync:
        b.sync(a.sync)

    out, path, frames, gaps, last, me = None, None, 0, 0, None, None
    matches, press_until, next_press, pressed, flags = 0, 0, -PRESS_EVERY, 0, None
    starts, steps, picker = 0, [], None  # + presses on this character select, PICK in progress
    relevel = False  # the CPU level to set on the next character select
    unreached = 0  # character selects missed in a row
    refs, mario, css_seen = smash_farm.load_refs(), np.load(smash_farm.MARIO).astype(np.float32), 0
    quiet, quiet_until = set(), 0  # slots the agent played, their last input still shows a while
    pads = {}  # the controller playing each slot the agent plays
    probe, join_steps, next_look, seen = None, [], 0, None  # with --pad
    before, card, tries, tried = {}, None, 0, None  # cards before its join, the one it took
    try:
        for line in b.frames(raw=True):
            f = Frame(line)
            fm = f.watches.get("fm")
            count, ready, result = (fm[0], fm[0x32], fm[0x49]) if fm else (0, 0, 0)
            if (count, ready, result) != flags:
                flags = (count, ready, result)
                log(f"fighters {count} ready_go {ready} result {result}")
            fighting = count >= 2 and not result and f.watches.get("0pos") is not None

            fighters = smash_state.parse(f, a.players) if fighting else {}
            if out is None and fighters and len(fighters) == min(count, a.players):
                tags = smash_state.names(f, a.players)
                me = next((i for i, t in tags.items() if t == a.name and i in fighters), None) \
                    if a.agent else None
                if a.agent and a.pad is None and me is None:  # a match to learn nothing from
                    log(f"no player named {a.name}, stopping")
                    return
                # A tagged player goes through its own controller, else --pad looks for its fighter.
                active, probe = {}, [] if a.agent and a.pad is not None and me is None else None
                if me is not None:
                    for i, x in fighters.items():
                        if i != me and not x.cpu and not a.sparring and not a.selfplay:
                            log(f"player {i + 1} is not a CPU, stopping")
                            return
                        if i == me or (not x.cpu and a.selfplay):
                            if i not in agents:
                                agents[i] = Agent(a.agent, i)
                            active[i] = agents[i]
                pads = {i: i for i in active}
                # Matches of more than two or with people stay apart: training reads one on one
                # against the CPU only.
                folder = a.out or ("~/smash-ai/party" if len(fighters) > 2 or probe is not None else
                                   "~/smash-ai/play" if active else "~/smash-ai/rec")
                os.makedirs(os.path.expanduser(folder), exist_ok=True)
                path = os.path.join(os.path.expanduser(folder), time.strftime("%Y%m%d-%H%M%S")
                                    + (f"-{a.port}" if a.port != 6560 else "") + ".txt.gz")
                out = gzip.open(path + ".part", "wt", compresslevel=6)
                header = {"title": info[1], "build": info[2], "players": a.players,
                          "manager": smash_state.FIGHTER_MANAGER, "layout": layout,
                          "names": tags, "agent": a.agent if active or probe is not None else None,
                          "me": me, "pad": a.pad,
                          "agents": sorted(active), "level": a.level, "sync": a.sync,
                          "version": next(iter(active.values())).model.version if active else None,
                          "rules": next(iter(active.values())).RULES if active else None}
                out.write("# " + json.dumps(header) + "\n")
                frames, gaps, last = 0, 0, None
                log(f"match -> {path}" + (f", {a.name} is player {me + 1}" if me is not None else
                                          f", {a.name} looks for its fighter" if probe is not None else ""))
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
                        b.release(pads[i])
                    if probe is not None:  # it was not in this match
                        b.release(a.pad)
                        probe = None
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
                        return 3
                    if a.matches and matches >= a.matches:
                        return
                    if a.level_file and os.path.exists(a.level_file):
                        with open(a.level_file) as lf:
                            wanted = int(lf.read())
                        if wanted != a.level:
                            log(f"CPU level {wanted} wanted instead of {a.level}")
                            a.level, relevel = wanted, True

            if probe is not None and fighting:
                x = 32767 if len(probe) // PROBE_HALF % 2 == 0 else -32767
                probe.append((x, {i: v.stick_x for i, v in fighters.items()}))
                me = identify(probe)
                if me is not None:
                    log(f"{a.name} is player {me + 1}")
                    if me not in agents:
                        agents[me] = Agent(a.agent, me)
                    active, pads, probe = {me: agents[me]}, {me: a.pad}, None
                elif len(probe) >= PROBE_FRAMES:
                    log(f"no fighter follows controller {a.pad + 1}: {a.name} is not in this match")
                    b.release(a.pad)
                    probe = None
                else:
                    b.pad(a.pad, 0, x, 0)

            if active and len(fighters) > 1:
                for i, ag in active.items():
                    if i in fighters:
                        b.pad(pads[i], *ag.act(fighters))

            if a.pad is not None and a.agent:
                if join_steps:
                    b.pad(a.pad, *join_steps.pop(0))
                    if not join_steps:
                        b.release(a.pad)
                        now = smash_state.cards(f)
                        if card is None:  # the CPU's card, or a free one Smash gave first
                            card = next((i for i, (kind, _) in now.items()
                                         if kind == smash_state.HUMAN
                                         and before.get(i, (None,))[0] != smash_state.HUMAN), None)
                            if card is None:
                                # Its hand is already there: a pick would grab a CPU's token.
                                log(f"controller {a.pad + 1} took no card")
                                tried = now
                            else:
                                join_steps = pick_path(PICK_DOWNS[0]) + SETTLE
                        elif now.get(card, (None, None))[1] == smash_state.MARIO:
                            log(f"{a.name} plays card {card + 1} with Mario")
                        elif tries + 1 < len(PICK_DOWNS):
                            tries += 1
                            join_steps = RETRY + pick_path(PICK_DOWNS[tries]) + SETTLE
                        else:
                            log(f"{a.name} did not get Mario on card {card + 1}")
                            tried = now
                elif count == 0 and f.index >= next_look:
                    next_look = f.index + LOOK_EVERY
                    now = smash_state.cards(f)
                    if card is not None and now.get(card, (None,))[0] != smash_state.HUMAN:
                        card = None  # its card was closed or given back to a CPU
                    cpu_mario = [i for i, c in now.items()
                                 if c == (smash_state.CPU, smash_state.MARIO)]
                    # A tagged player plays through its own controller; a failed try waits for the
                    # cards to change.
                    tagged = a.name in smash_state.names(f, a.players).values()
                    if cpu_mario and card is None and now != tried and not tagged:
                        # Its presses anywhere but on the character select could open a menu.
                        shot = smash_farm.grab(b)
                        name, dist = smash_farm.screen(shot[0], refs) if shot else ("none", 0)
                        if name in ("css", "ready"):
                            log(f"{a.name} takes the CPU Mario of card {cpu_mario[0] + 1}")
                            before, card, tries = now, None, 0
                            join_steps = JOIN + SETTLE
                        elif name != seen:
                            log(f"screen {name} ({dist:.0f}): {a.name} waits for the character select")
                        seen = name

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
                    if relevel and slot == 0 or starts >= NAVIGATE_AFTER and a.agent and slot in (0, None):
                        log("player 1 goes back to the character select")
                        nav = smash_farm.Instance(a.port - 6560, argparse.Namespace(
                            cpu=[], home=os.path.expanduser("~/smash-ai")))
                        nav.level = a.level or 9
                        if nav.navigate(b):
                            unreached = 0
                        else:
                            unreached += 1
                            log("character select not reached")
                            # Two hours of presses on a screen it did not know: stop instead.
                            if unreached >= 3:
                                log("character select not reached 3 times, stopping")
                                return
                        b.ask("stream 1", "ok")  # navigate stops the stream when done
                        if a.sync:
                            b.sync(a.sync)
                        starts, picker, relevel = 0, 0, False  # its stick may still show until the match
                        continue
                    if starts >= PICK_AFTER and slot not in (0, None):
                        log(f"the match does not start, player {slot + 1} picks Mario")
                        steps, picker = list(PICK), slot
                        continue
                    starts += 1
                    # The results also keep the count at 0, and the result flag stays up on the
                    # character select: only the screen tells them apart. + does not leave the
                    # results, and an A that comes late on the select drops player 1's fighter.
                    shot = smash_farm.grab(b) if result else None
                    name = smash_farm.screen(shot[0], refs)[0] if shot else "ready"
                    css_seen = css_seen + 1 if name == "css" else 0
                    if css_seen > 2 and a.agent and slot == 0:
                        log("player 1 has no fighter, it picks Mario")
                        if not smash_farm.pick_mario(b, mario, lockstep=bool(a.sync)):
                            log("Mario not found")
                        b.ask("stream 1", "ok")  # play stops the stream when done
                        if a.sync:
                            b.sync(a.sync)
                        css_seen, picker = 0, 0
                        continue
                    if name == "dark":  # between two screens: wait for the next one
                        next_press = f.index
                        continue
                    pressed = BUTTON_PLUS if name in ("css", "ready") else BUTTON_A
                elif result:
                    pressed = BUTTON_A  # results
                else:
                    continue  # match loading or ending
                # Player 1's controller only: on the character select, a press from player 2's
                # makes it a human player, and the CPU's slot is lost until someone clears it.
                for i in range(a.players if a.selfplay else 1):
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
        for i in {0, *agents, *([] if a.pad is None else [a.pad])}:
            b.release(i)
        if a.release_lock:
            subprocess.run([ADB, "shell", "rm", "-f", LOCK])
            log("console lock released")


if __name__ == "__main__":
    sys.exit(main())
