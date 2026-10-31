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
With --pad P (mode IA), a FIG card on the character select is an AI's: as soon as a FIG card
waits for its figurine, Eden lists the AIs' virtual amiibo (AMIIBO/<name>.bin on the console,
made by amiibo.py and registered once in Smash's amiibo menu) for the player to pick one. The AIs
are --figs (--name by default), each on its own controller (P, then P-1...) with its own model
(<name>.pt or .npz next to --agent, else --agent). When someone presses + there, the FIG cards of
AIs become player cards on their controllers, so Smash's amiibo AI never plays (its level and
learning count for nothing): the model does, and presses A on the results. Back on the select,
the card waits for its figurine again and gets the same AI's amiibo from here; an AI's player
card that lost its fighter has its hand pick Mario again.
Matches with the model go to ~/smash-ai/play, the others to ~/smash-ai/rec. It also stops
once smash_rl.py writes "selfplay" in ~/smash-ai/phase, for the CPU slot to become a player.

On a PC, --sync 1 makes the game wait for each frame to be handled, so an instance running
without speed limit never outpaces the agent; --min-battery 0 skips the console checks.

  smash_record.py [--host H] [--port 6560] [--sync K] [--out DIR] [--auto] [--matches N]
                  [--min-battery 15]
                  [--agent ~/smash-ai/models/ultimus.pt [--name Ultimus | --pad 7 [--figs A,B]]
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
# With --pad, an AI's card without its fighter: a pick path. The grid's rows shrink as matches
# unlock fighters, so when the card shows another fighter B takes the token back for the next path.
RETRY = [(0x2, 0, 0)] * PRESS_FRAMES + [(0, 0, 0)] * 10
SETTLE = [(0, 0, 0)] * 20  # before the card table shows the pick
PICK_DOWNS = (7, 13, 4, 10)
LOOK_EVERY = 60
PLACED_FOR = 180  # frames an amiibo put back stays on the reader, for Smash to scan it
AMIIBO = "/storage/emulated/0/Android/data/com.miHoYo.Yuanshen.relWithDebInfo/files/amiibo"
KINDS_FOR = 600  # frames the FIG cards stay player cards after a + that started no match


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
    ap.add_argument("--pad", type=int, help="the first AI's own controller (0 for player 1) "
                    "instead of a name tag: it plays the FIG cards of the AIs")
    ap.add_argument("--figs", help="with --pad, the AIs, comma separated (--name by default)")
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
    # With --pad: each AI's controller, and the AI on each of them.
    fig_pads = {name: a.pad - k for k, name in enumerate((a.figs or a.name).split(","))} \
        if a.pad is not None and a.agent else {}
    pad_figs = {p: name for name, p in fig_pads.items()}
    # An amiibo's nickname holds 10 characters: Nostradamus is Nostradamu on its card.
    full_names = {name[:10]: name for name in fig_pads}

    def card_owners(f):
        return {i: (full_names.get(nick, nick), pad)
                for i, (nick, pad) in smash_state.card_owners(f).items()}

    def agent(i, name):
        own = os.path.join(os.path.dirname(os.path.expanduser(a.agent)),
                           name.lower() + os.path.splitext(a.agent)[1])
        path = own if os.path.exists(own) else os.path.expanduser(a.agent)
        if i not in agents or agents[i].path != path:
            agents[i] = Agent(path, i)
        return agents[i]

    b = Bridge(a.host, a.port)
    b.ask("stream 0", "ok")  # drops the frames sent since the connection
    info = b.ask("info", "info").split()
    b.ask("unwatch *", "ok")
    # entry count at +0, is_ready_go at +0x32, is_result_mode at +0x49
    b.ask(f"watch fm 0x50 {smash_state.FIGHTER_MANAGER} 0 0xa0", "ok")
    b.ask(smash_state.STAGE_WATCH, "ok")
    smash_state.watch_all(b, a.players, work=a.pad is None)
    if fig_pads:
        smash_state.watch_cards(b)
        for i in range(a.players):
            b.ask(f"watch {i}npad 4 {smash_state.FIGHTER_MANAGER} 0 {0x20 + i * 8:#x} "
                  f"{smash_state.ENTRY_PAD:#x}", "ok")
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
    join_steps, next_look, seen = [], 0, None  # with --pad: a pick in progress, the next look
    picking, tries, tried = None, 0, None  # (card, controller) it picks on, cards it gave up on
    fig_cards, last_ai = {}, {}  # AIs' FIG cards, the AI each card had
    placed, placed_until = None, 0  # an amiibo put back on the reader
    kinds_until, converted = 0, []  # the FIG cards made player cards on a + press
    next_ok, ok_until = 0, 0  # the AIs' A on the results
    next_plug = 0  # the next check of the AIs' controllers
    try:
        for line in b.frames(raw=True):
            f = Frame(line)
            fm = f.watches.get("fm")
            count, ready, result = (fm[0], fm[0x32], fm[0x49]) if fm else (0, 0, 0)
            if (count, ready, result) != flags:
                flags = (count, ready, result)
                log(f"fighters {count} ready_go {ready} result {result}")
            fighting = count >= 2 and not result and f.watches.get("0pos") is not None
            # Eden can unplug the AIs' controllers (a pad joining as player 1, a launch from the
            # game list): they are plugged back in, else their fighters stand still.
            if fig_pads and f.index >= next_plug:
                for p in sorted(set(pad_figs) - set(f.pads)):
                    log(f"{pad_figs[p]}'s controller {p + 1} plugged back in" if b.plug(p) else
                        f"{pad_figs[p]}'s controller {p + 1} could not be plugged in")
                next_plug = f.index + LOOK_EVERY

            fighters = smash_state.parse(f, a.players) if fighting else {}
            if out is None and fighters and len(fighters) == min(count, a.players):
                tags = smash_state.names(f, a.players)
                # With --pad the AIs play the fighters on their controllers, else the tagged one.
                figs = {}
                for i in fighters if fig_pads else ():
                    raw = f.watches.get(f"{i}npad")
                    pad = int.from_bytes(raw, "little", signed=True) if raw else -1
                    if pad in pad_figs and not fighters[i].cpu:
                        figs[i] = pad_figs[pad]
                if fig_pads:
                    me = min(figs, default=None)
                else:
                    me = next((i for i, t in tags.items() if t == a.name and i in fighters), None) \
                        if a.agent else None
                if a.agent and a.pad is None and me is None:  # a match to learn nothing from
                    log(f"no player named {a.name}, stopping")
                    return
                if kinds_until or fig_cards:
                    b.unpoke("*")
                    fig_cards, kinds_until = {}, 0
                active = {i: agent(i, name) for i, name in figs.items()}
                if me is not None and not figs:
                    for i, x in fighters.items():
                        if i != me and not x.cpu and not a.sparring and not a.selfplay:
                            log(f"player {i + 1} is not a CPU, stopping")
                            return
                        if i == me or (not x.cpu and a.selfplay):
                            if i not in agents:
                                agents[i] = Agent(a.agent, i)
                            active[i] = agents[i]
                pads = {i: fig_pads[figs[i]] if figs else i for i in active}
                # Matches of more than two or with people stay apart: training reads one on one
                # against the CPU only.
                folder = a.out or ("~/smash-ai/party" if len(fighters) > 2 or fig_pads else
                                   "~/smash-ai/play" if active else "~/smash-ai/rec")
                os.makedirs(os.path.expanduser(folder), exist_ok=True)
                path = os.path.join(os.path.expanduser(folder), time.strftime("%Y%m%d-%H%M%S")
                                    + (f"-{a.port}" if a.port != 6560 else "") + ".txt.gz")
                out = gzip.open(path + ".part", "wt", compresslevel=6)
                header = {"title": info[1], "build": info[2], "players": a.players,
                          "manager": smash_state.FIGHTER_MANAGER, "layout": layout,
                          "names": tags, "agent": a.agent if active else None,
                          "me": me, "pad": a.pad, "figs": figs,
                          "agents": sorted(active), "level": a.level, "sync": a.sync,
                          "version": next(iter(active.values())).model.version if active else None,
                          "rules": next(iter(active.values())).RULES if active else None}
                out.write("# " + json.dumps(header) + "\n")
                frames, gaps, last = 0, 0, None
                who = figs or ({me: a.name} if me is not None else {})
                log(f"match -> {path}" + "".join(f", {n} is player {i + 1}" for i, n in who.items()))
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
                    quiet, quiet_until, active = set(pads.values()), f.index + PRESS_EVERY, {}
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

            if active and len(fighters) > 1:
                for i, ag in active.items():
                    if i in fighters:
                        b.pad(pads[i], *ag.act(fighters))

            # The results wait for an A from every player, the AIs' too (their player cards still
            # hold their controllers there).
            if fig_pads and result and count >= 2 and f.index >= next_ok:
                for _, p in smash_state.card_owners(f).values():
                    if p in pad_figs:
                        b.pad(p, BUTTON_A)
                ok_until, next_ok = f.index + PRESS_FRAMES, f.index + PRESS_EVERY
            elif ok_until and f.index >= ok_until:
                for p in pad_figs:
                    b.release(p)
                ok_until = 0

            if fig_pads and not fighting:
                if join_steps:
                    card, pad = picking
                    b.pad(pad, *join_steps.pop(0))
                    if not join_steps:
                        b.release(pad)
                        now = smash_state.cards(f)
                        if now.get(card, (None, None))[1] == smash_state.MARIO:
                            log(f"{pad_figs[pad]} plays card {card + 1} with Mario")
                            picking = None
                        elif tries + 1 < len(PICK_DOWNS):
                            tries += 1
                            join_steps = RETRY + pick_path(PICK_DOWNS[tries]) + SETTLE
                        else:
                            log(f"{pad_figs[pad]} did not get Mario on card {card + 1}")
                            tried, picking = now, None
                elif kinds_until and f.index >= kinds_until:
                    for i in converted:
                        b.unpoke(f"kind{i}")
                        b.unpoke(f"kindcopy{i}")
                    kinds_until = 0
                elif count == 0 and not kinds_until and any(
                        p[0] & (BUTTON_PLUS | BUTTON_A) for k, p in f.pads.items() if k not in pad_figs):
                    # The match takes the kind and controller from both tables when it starts:
                    # player cards on the AIs' controllers, built as players, Smash's FIG AI out.
                    # Read from this frame, as + (or A on the "ready" banner) may come right after
                    # the amiibo's nickname; an A that starts nothing only makes them player cards
                    # sooner.
                    owners, converted = card_owners(f), {}
                    for i, (kind, _) in smash_state.cards(f).items():
                        nick = owners.get(i, ("",))[0]
                        if kind == smash_state.FIG and nick in fig_pads and nick not in converted.values():
                            b.poke(f"pad{i}", fig_pads[nick].to_bytes(4, "little", signed=True),
                                   f"main+{smash_state.CARD_PADS + i * smash_state.CARD_PAD_SIZE:#x}")
                            b.poke(f"kind{i}", bytes(4), f"main+{smash_state.CARDS + i * smash_state.CARD_SIZE + smash_state.CARD_KIND:#x}")
                            b.poke(f"kindcopy{i}", bytes(4), f"main+{smash_state.CARD_PADS + i * smash_state.CARD_PAD_SIZE + smash_state.CARD_COPY + smash_state.CARD_KIND:#x}")
                            converted[i] = fig_cards[i] = last_ai[i] = nick
                    if converted:
                        kinds_until = f.index + KINDS_FOR
                        log("+: " + ", ".join(f"card {i + 1} is {n}'s" for i, n in converted.items()))
                elif count == 0 and f.index >= next_look:
                    next_look = f.index + LOOK_EVERY
                    now, owners = smash_state.cards(f), card_owners(f)
                    seated, ai_figs, waiting = {}, {}, set()
                    for i, (kind, chara) in now.items():
                        nick, pad = owners.get(i, ("", -1))
                        if kind == smash_state.FIG and nick in fig_pads and nick not in seated:
                            ai_figs[i] = seated[nick] = nick
                        elif kind == smash_state.FIG and not nick:
                            waiting.add(i)
                        elif kind == smash_state.HUMAN and pad in pad_figs:
                            seated[pad_figs[pad]] = i
                    for i, nick in ai_figs.items():
                        if fig_cards.get(i) != nick:
                            log(f"card {i + 1} is {nick}'s, on controller {fig_pads[nick] + 1}")
                            b.poke(f"pad{i}", fig_pads[nick].to_bytes(4, "little", signed=True),
                                   f"main+{smash_state.CARD_PADS + i * smash_state.CARD_PAD_SIZE:#x}")
                    for i in set(fig_cards) - set(ai_figs):
                        b.unpoke(f"pad{i}")
                        if now.get(i, (None,))[0] != smash_state.HUMAN:  # no longer an AI's card
                            b.poke(f"pad{i}", (-1).to_bytes(4, "little", signed=True),
                                   f"main+{smash_state.CARD_PADS + i * smash_state.CARD_PAD_SIZE:#x}")
                            b.unpoke(f"pad{i}")
                    fig_cards = ai_figs
                    # A card that had an AI waits for its figurine again after the match: the same
                    # one goes back on the reader, for Smash to write the match to it.
                    for i in set(last_ai) - waiting - set(seated.values()) - set(ai_figs):
                        del last_ai[i]  # the card is someone else's now
                    back = [(i, last_ai[i]) for i in sorted(waiting & set(last_ai))
                            if last_ai[i] not in seated]
                    if placed and f.index >= placed_until:
                        b.amiibo()
                        placed = None
                    elif back and not placed and b.amiibo(f"{AMIIBO}/{back[0][1]}.bin"):
                        placed, placed_until = back[0][1], f.index + PLACED_FOR
                        log(f"{placed}'s amiibo back on the reader for card {back[0][0] + 1}")
                    # An AI's player card without its fighter: its hand picks Mario.
                    lost = [(i, p) for i, (kind, chara) in now.items()
                            for p in [owners.get(i, ("", -1))[1]]
                            if kind == smash_state.HUMAN and p in pad_figs and chara != smash_state.MARIO]
                    if lost and now != tried:
                        # Its presses anywhere but on the character select could open a menu.
                        shot = smash_farm.grab(b)
                        name, dist = smash_farm.screen(shot[0], refs) if shot else ("none", 0)
                        if name in ("css", "ready"):
                            log(f"{pad_figs[lost[0][1]]} picks Mario on card {lost[0][0] + 1}")
                            picking, tries = lost[0], 0
                            join_steps = pick_path(PICK_DOWNS[0]) + SETTLE
                        elif name != seen:
                            log(f"screen {name} ({dist:.0f}): the AIs wait for the character select")
                        seen = name

            if not a.auto:
                continue
            # Menu presses go to every controller (on the results only players' ones count) and can
            # show up a little late, so their buttons are ignored for a while.
            ours = pressed if f.index < next_press + PRESS_EVERY else 0
            touched = {i: hex(p[0]) for i, p in f.pads.items()
                       if p[0] & ~ours and i not in active and i != picker
                       and i not in fig_pads.values()
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
        for i in {0, *agents, *fig_pads.values()}:
            b.release(i)
        if a.release_lock:
            subprocess.run([ADB, "shell", "rm", "-f", LOCK])
            log("console lock released")


if __name__ == "__main__":
    sys.exit(main())
