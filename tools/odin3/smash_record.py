#!/usr/bin/env python3
"""Record Smash Ultimate matches through the game bridge, one gzip file per match.

Each file starts with a "# {json}" header (build, watch layout), then the raw bridge frame
lines of the match: pads plus every smash_state field and the WorkModule arrays of each
fighter. With --auto it also drives the menus so CPU matches chain on their own: A on the
results, + on the character select. Set it up once with the players as CPU and a rule set
whose stage selection is random (no stage screen; the random list holds the stages wanted),
and never press B there: it picks up a CPU token. It stops when someone touches a controller (exit status 4), or when
the console battery gets low (exit status 3, for the caller to wait for the charger).

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
learning count for nothing): the model does, and presses A on the results. An AI plays from its
FIG card only, never from a player card on its controller. Back on the select,
the card waits for its figurine again and gets the same AI's amiibo from here; a FIG card made by
the AI's own hand comes back as that controller's player card, and the hand makes it FIG again.
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

import amiibo
import smash_farm
import smash_state
from bridge import Bridge, Frame

ADB = os.path.expanduser("~/Library/Android/sdk/platform-tools/adb")
LOCK = "/data/local/tmp/odin3.lock"
PRESS_FRAMES, PRESS_EVERY = 6, 90
BUTTON_A, BUTTON_B, BUTTON_PLUS = 0x1, 0x2, 0x400
# When + does not start the match, the agent's player has lost its fighter (a late A on the
# character select): its hand goes to the top left corner, then onto the first fighter, Mario.
# For player 1, smash_farm finds its way back to the character select (the presses may have
# wandered off into other menus) and looks for his portrait, as the grid grows with the
# fighters that matches unlock. That takes a minute, and the + presses already reach
# PICK_AFTER on the results: it waits for a minute without match first.
PICK_AFTER = 4
NAVIGATE_AFTER = 40
DOOR = (slice(26, 32), slice(52, 58))  # Challenger's Approach's door on Games & More, 36x64


def pick_path(down):
    """The hand to the top left corner, down for that many frames and a little right, then A."""
    return ([(0, -32767, 0)] * 120 + [(0, 0, 32767)] * 80 + [(0, 0, -32767)] * down
            + [(0, 32767, 0)] * 11 + [(0, 0, 0)] * 10 + [(BUTTON_A, 0, 0)] * PRESS_FRAMES)


PICK = pick_path(13)
# With --pad, a FIG card made by an AI's own hand comes back from the match as that controller's
# player card, under someone's name tag: the hand turns it back into its FIG card, player then CPU
# then FIG on its label. The path holds for the first of two cards: the hand to the bottom left
# corner, then right onto the label: twelve frames on the Odin, thirty on the PC's instances, so
# the recorder tries each and keeps the one that worked.
def refig(right):
    return ([(0, -32767, -32767)] * 120 + [(0, 32767, 0)] * right + [(0, 0, 0)] * 10
            + ([(BUTTON_A, 0, 0)] * PRESS_FRAMES + [(0, 0, 0)] * 30) * 2 + [(0, 0, 0)] * 20)


REFIG_RIGHT = (12, 30)
# With --auto, someone who wants to play cannot join a match the AIs started, nor pause it: the
# first AI quits it from its pause menu (+, down to "Quitter", A, right to "Oui", A).
QUIT = ([(BUTTON_PLUS, 0, 0)] * PRESS_FRAMES + [(0, 0, 0)] * 40
        + ([(0, 0, -32767)] * 6 + [(0, 0, 0)] * 8) * 4 + [(BUTTON_A, 0, 0)] * PRESS_FRAMES
        + [(0, 0, 0)] * 40 + [(0, 32767, 0)] * 6 + [(0, 0, 0)] * 8 + [(BUTTON_A, 0, 0)] * PRESS_FRAMES)
QUIT_CHECK = 180  # frames after QUIT: a match still on is unpaused and played out
QUIT_WITHIN = 900  # frames for the match to show its GO (it may be loading)
TRAIN = os.path.expanduser("~/smash-ai/train")  # termux_ultimus.sh passes --auto while it exists
LOOK_EVERY = 60
PLACED_FOR = 180  # frames an amiibo put back stays on the reader, for Smash to scan it
AMIIBO = "/storage/emulated/0/Android/data/com.miHoYo.Yuanshen.relWithDebInfo/files/amiibo"
KINDS_FOR = 600  # frames the FIG cards stay player cards after a + that started no match
BACK_FOR = 3600  # frames the AIs' cards stay FIG cards after a match without the character select seen


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


# The open cards share the bottom of the character select, left of its buttons, from this height
# (per mille of the frame): a level badge goes on the top right corner of a card's portrait.
CARDS_RIGHT, CARDS_TOP = 956, 633


def badge_place(rank, n):
    """Center and size (per mille of the frame width, y of its height) of the badge of the rank-th
    of n open cards."""
    w = CARDS_RIGHT / n
    size = round(min(max(w * 0.3, 45), 80))
    return round(w * rank + min(w * 0.7, w - size * 0.95)), round(CARDS_TOP + size * 16 / 9 * 0.55), size


def base(nick):
    """A FIG nickname or name tag without the level or points shown after it before."""
    head, _, tail = nick.rpartition(" ")
    return head if head and tail.lstrip("-").removesuffix("k").isdigit() else nick


# Smash points (Points), as Smash's own GSP: everyone starts at 1,000,000 and never goes under 0.
# A KO between two fighters of the same points is worth half of KO_POINTS, more against a stronger
# one and less against a weaker one, as in Elo (which GSP is said to follow, ssbwiki.com/GSP):
# SPREAD points apart, the stronger is expected to score 10 KOs for 1.
START, KO_POINTS, SPREAD = 1_000_000, 20_000, 400_000
DAMAGE_POINTS = 100  # in a free-for-all, for each % dealt to someone of the same points
MODES = {"1v1": "1v1", "teams": "EQUIPES", "ffa": "FFA"}  # Points' modes and their badge labels


def mode_of(fighters, teams=False):
    """Which of MODES a match of that many fighters is, teams with team battles on."""
    return "1v1" if fighters == 2 else "teams" if teams else "ffa"


class Points:
    """--points: everyone's Smash points by name and mode (MODES), an AI's or a name tag's, worked
    out at the end of each match with a person's name tag and someone else named. 1 on 1 and in
    teams, each KO on a named fighter of another team is won from them and each fall costs (half
    of KO_POINTS for a fall to no one named: a CPU, a teammate, the fighter itself); in a
    free-for-all, the KOs and the damage dealt to named fighters only add."""

    def __init__(self, path, ais):
        self.path = os.path.expanduser(path)
        try:
            with open(self.path) as fh:
                self.points = json.load(fh)
        except (OSError, ValueError):
            self.points = {}
        if any(m not in MODES for m in self.points):  # the KO counts kept before, set aside
            os.replace(self.path, self.path + ".kos")
            self.points = {}
        for mode in MODES:
            self.points.setdefault(mode, {})
        self.ais = list(ais)
        self.names, self.people, self.last, self.dealt, self.damage = {}, {}, {}, {}, {}

    def of(self, mode, name):
        return self.points[mode].get(name, START)

    def key(self, name):
        """The name the points of a FIG nickname or name tag are kept under: without the number
        after it, or the longer name it was cut from (amiibo.shown_name())."""
        head = base(name)
        known = {n for table in self.points.values() for n in table} | set(self.ais)
        return head if head in known or len(head) < 5 else next(
            (n for n in known if n.startswith(head)), head)

    def start(self, ais, tags):
        """A match of ais and people's tags ({fighter: name}): counted with one of each at least,
        or with two people."""
        names = {i: self.key(n) for i, n in {**tags, **ais}.items() if n}
        self.people = {names[i]: base(n) for i, n in tags.items() if n and i not in ais}
        self.names = names if self.people and len(names) > 1 else {}
        self.last, self.dealt, self.damage = {}, {}, {}

    def update(self, f, players):
        """Follows a counted match frame by frame: its KOs, and who dealt each fighter's damage."""
        if not self.names:
            return
        now = smash_state.kos(f, players)
        if now:
            self.last = now
        for j, (hurt, by) in smash_state.damage(f, players).items():
            was, self.damage[j] = self.damage.get(j), hurt
            if was is not None and hurt > was and by in now and by != j:
                self.dealt[by, j] = self.dealt.get((by, j), 0) + hurt - was

    def finish(self):
        """The points of the match start() began, from its last KOs and damage: {name: (points,
        change)}, saved."""
        names, now, self.names = self.names, self.last, {}
        if not names or len(now) < 2:
            return {}
        teams = [team for _, _, team in now.values()]
        mode = mode_of(len(now), len(set(teams)) < len(teams))
        had = {n: self.of(mode, n) for n in names.values()}

        def worth(i, j):  # i's KO on j
            return KO_POINTS / (1 + 10 ** ((had[names[i]] - had[names[j]]) / SPREAD))

        change = dict.fromkeys(had, 0.0)
        for i, (falls, beat, team) in now.items():
            won = {j: n for j, n in beat.items() if n and i in names and j in names and j in now and now[j][2] != team}
            for j, n in won.items():
                change[names[i]] += n * worth(i, j)
                if mode != "ffa":
                    change[names[j]] -= n * worth(i, j)
            if mode != "ffa" and i in names:
                lost = sum(now[k][1].get(i, 0) for k in now if k in names and now[k][2] != team)
                change[names[i]] -= max(falls - lost, 0) * KO_POINTS / 2
        if mode == "ffa":
            for (i, j), hurt in self.dealt.items():
                if i in names and j in names:
                    change[names[i]] += hurt * DAMAGE_POINTS * worth(i, j) / (KO_POINTS / 2)
        out = {}
        for name, d in change.items():
            new = max(round(had[name] + d), 0)
            self.points[mode][name] = new
            out[name] = new, new - had[name]
        with open(self.path + ".tmp", "w") as fh:
            json.dump(self.points, fh)
        os.replace(self.path + ".tmp", self.path)
        log(f"points {mode}: " + ", ".join(f"{n} {p:,} ({d:+,})" for n, (p, d) in out.items()))
        return out


class Badges:
    """--badges: on the character select, Eden draws the Smash points (Points) of the mode its
    rules make over each card that has a name, an AI's FIG card by its nickname and a player card
    by its name tag, popping with what they won or lost since they were last shown there (the
    file keeps those)."""

    def __init__(self, path, points):
        self.path, self.points, self.sent = os.path.expanduser(path), points, set()
        try:
            with open(self.path) as fh:
                self.shown = json.load(fh)
        except (OSError, ValueError):
            self.shown = {}

    def wanted(self, b, main, f):
        """{card: (mode, name, points)} of the cards that get a badge, main being the main
        module's base."""
        owners, tags, cards, out = smash_state.card_owners(f), smash_state.card_tags(f), smash_state.cards(f), {}
        mode = mode_of(len(cards), (b.read(main + smash_state.TEAM_BATTLE, 1) or b"\0")[0] == 1)
        for i, (kind, _) in cards.items():
            who = owners.get(i, ("",))[0] if kind == smash_state.FIG else \
                smash_state.tag_name(b, main, tags.get(i, -1)) if kind == smash_state.HUMAN else ""
            if who:
                name = self.points.key(who)
                out[i] = mode, name, self.points.of(mode, name)
        return out

    def show(self, b, f, wanted=None):
        """Sends the badges of wanted (wanted()), or takes them all off without it."""
        cards = sorted(smash_state.cards(f))
        for i, (mode, name, n) in (wanted or {}).items():
            key = f"{mode} {name}"
            gain = n - self.shown.get(key, n)
            b.badge(i, *badge_place(cards.index(i), len(cards)), n, gain, MODES[mode])
            if self.shown.get(key) != n:
                if gain:
                    log(f"{name}'s card shows {n:,} points in {mode} ({gain:+,})")
                self.shown[key] = n
                with open(self.path + ".tmp", "w") as fh:
                    json.dump(self.shown, fh)
                os.replace(self.path + ".tmp", self.path)
        for i in self.sent - set(wanted or {}):
            b.badge(i)
        self.sent = set(wanted or {})


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
    ap.add_argument("--amiibo", default=AMIIBO, help="with --pad, the folder of their <name>.bin")
    ap.add_argument("--sparring", action="store_true", help="let the agent face a local human")
    ap.add_argument("--challengers", action="store_true", help="with --auto, from the door of "
                    "Challenger's Approach on Games & More, the agent fights them all as Mario")
    ap.add_argument("--selfplay", action="store_true", help="the agent plays every human slot")
    ap.add_argument("--level", type=int, help="level of the CPU, kept in the match header")
    ap.add_argument("--level-file", help="CPU level to follow, read after each match: player 1 "
                    "then goes back through the character select to set it")
    ap.add_argument("--tag", help="a person's name tags, comma separated, renamed as the AIs' FIG "
                    "cards with their level from --strength after it (Hamz 43)")
    ap.add_argument("--strength", help="smash_rl.py's strength.json, the person's level as Hamza")
    ap.add_argument("--points", help="with --pad, everyone's Smash points by name, kept in this "
                    "file (Points); each person's name tag shows theirs after a match (Hamz 12)")
    ap.add_argument("--badges", help="with --points, Eden draws each card's points over it on the "
                    "character select (Badges); this file keeps what it needs")
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
    # An amiibo's nickname holds 10 characters: Nostradamus is Nostradamu on its card, and Nostrad 53
    # past level 50 (amiibo.shown_name()).
    def card_owners(f):
        return {i: (amiibo.named(nick, fig_pads), pad)
                for i, (nick, pad) in smash_state.card_owners(f).items()}

    def agent(i, name):
        # The first AI plays --agent (on the PC the champion or the candidate), the others their
        # own model next to it.
        own = os.path.join(os.path.dirname(os.path.expanduser(a.agent)),
                           name.lower() + os.path.splitext(a.agent)[1])
        first = (a.figs or a.name).split(",")[0]
        path = own if name != first and os.path.exists(own) else os.path.expanduser(a.agent)
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

    def tag_level(seen):
        """Each --tag followed by the person's level when --strength changed since seen, its mtime
        then: its mtime now. The game writes the new name to its save after the next match."""
        main = info[3].split("=")[1]
        try:
            mtime = os.path.getmtime(os.path.expanduser(a.strength))
            if mtime == seen or main == "-":
                return seen
            with open(os.path.expanduser(a.strength)) as fh:
                lv = max(int(json.load(fh)["Hamza"]), 1)
        except (OSError, ValueError, KeyError):
            return seen
        for tag in a.tag.split(","):
            new = amiibo.shown_name(tag, lv)
            done = smash_state.rename_tag(b, int(main, 16), tag, new)
            log(f"name tag {tag} is now {new}" if done else f"name tag {tag} not in the save data")
        return mtime
    tag_seen = tag_level(0) if a.tag and a.strength else None
    points = Points(a.points, fig_pads) if a.points and fig_pads else None
    badges = Badges(a.badges, points) if a.badges and points else None

    out, path, frames, gaps, last, me = None, None, 0, 0, None, None
    matches, press_until, next_press, pressed, flags = 0, 0, -PRESS_EVERY, 0, None
    starts, steps, picker = 0, [], None  # + presses on this character select, PICK in progress
    relevel = False  # the CPU level to set on the next character select
    unreached = 0  # character selects missed in a row
    refs, mario = smash_farm.load_refs(), np.load(smash_farm.MARIO).astype(np.float32)
    css_seen, off_css = 0, 0
    quiet, quiet_until = set(), 0  # slots the agent played, their last input still shows a while
    pads = {}  # the controller playing each slot the agent plays
    join_steps, next_look, seen = [], 0, None  # with --pad: a REFIG in progress, the next look
    picking, tried = None, None  # (card, controller) of the REFIG, cards it gave up on
    refig_at, refig_fails = 0, 0  # REFIG_RIGHT's path in use, its failures in a row
    fig_cards, last_ai = {}, {}  # AIs' FIG cards, the AI each card had
    placed, placed_until = None, 0  # an amiibo put back on the reader
    kinds_until, converted = 0, []  # the FIG cards made player cards on a + press
    figs_back, back_until = [], 0  # the AIs' cards made FIG cards again after a match
    next_ok, ok_until = 0, 0  # the AIs' A on the results
    next_plug = 0  # the next check of the AIs' controllers
    quit_until, quit_steps, quit_pad, quit_check = 0, [], None, 0  # QUIT wanted, in progress
    idle, no_door = 0, 0  # with --challengers, presses since the last fight, looks with no door
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
            if a.challengers and any(x.stock == 0 for x in fighters.values()):
                # Lost, "Continue?" keeps the flags of a match: A there takes the rematch.
                fighting, fighters = False, {}
            if out is None and fighters and len(fighters) == min(count, a.players):
                tags = smash_state.names(f, a.players)
                # With --pad the AIs play the fighters on their controllers, else the tagged one.
                figs = {}
                # An AI plays from its FIG card only, never a player card left on its controller
                # (that one shows someone's name tag and counts in their stats): the converted
                # card keeps the amiibo's nickname.
                from_figs = {(nick, pad) for nick, pad in card_owners(f).values()}
                for i in fighters if fig_pads else ():
                    raw = f.watches.get(f"{i}npad")
                    pad = int.from_bytes(raw, "little", signed=True) if raw else -1
                    if pad in pad_figs and not fighters[i].cpu and (pad_figs[pad], pad) in from_figs:
                        figs[i] = pad_figs[pad]
                if badges:
                    badges.show(b, f)
                if fig_pads:
                    me = min(figs, default=None)
                else:
                    me = next((i for i, t in tags.items() if t == a.name and i in fighters), None) \
                        if a.agent else None
                if a.agent and a.pad is None and me is None:  # a match to learn nothing from
                    log(f"no player named {a.name}, stopping")
                    return
                if kinds_until or fig_cards or figs_back:
                    b.unpoke("*")
                    fig_cards, kinds_until, figs_back = {}, 0, []
                active = {i: agent(i, name) for i, name in figs.items()}
                if points:
                    points.start(figs, {i: t for i, t in tags.items()
                                        if i in fighters and not fighters[i].cpu and i not in figs})
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
                # Training reads one AI on one against anyone, people too (their wins show its
                # flaws): matches of more fighters, of more AIs or of none stay apart.
                folder = a.out or ("~/smash-ai/party" if len(fighters) > 2 or len(active) > 1
                                   or (fig_pads and not active) else
                                   "~/smash-ai/play" if active else "~/smash-ai/rec")
                os.makedirs(os.path.expanduser(folder), exist_ok=True)
                path = os.path.join(os.path.expanduser(folder), time.strftime("%Y%m%d-%H%M%S")
                                    + (f"-{a.port}" if a.port != 6560 or a.out else "") + ".txt.gz")
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
                    if points:
                        points.update(f, a.players)
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
                    # The AIs' cards would come back as player cards on their controllers: FIG
                    # cards again, which the character select builds them as.
                    owners = card_owners(f)
                    figs_back = [i for i, (kind, _) in smash_state.cards(f).items()
                                 for nick, p in [owners.get(i, ("", -1))]
                                 if kind == smash_state.HUMAN and p in pad_figs and nick == pad_figs[p]]
                    for i in figs_back:
                        b.poke(f"kind{i}", smash_state.FIG.to_bytes(4, "little"), f"main+{smash_state.CARDS + i * smash_state.CARD_SIZE + smash_state.CARD_KIND:#x}")
                        b.poke(f"kindcopy{i}", smash_state.FIG.to_bytes(4, "little"), f"main+{smash_state.CARD_PADS + i * smash_state.CARD_PAD_SIZE + smash_state.CARD_COPY + smash_state.CARD_KIND:#x}")
                    if figs_back:
                        back_until = f.index + BACK_FOR
                        log("back to FIG cards: " + ", ".join(f"card {i + 1}" for i in figs_back))
                    if any([ag.reload() for ag in agents.values()]):
                        log("model reloaded")
                    if tag_seen is not None:
                        tag_seen = tag_level(tag_seen)
                    if points:  # the points go on the badges only: the tags keep the bare names
                        points.finish()
                        for name, tag in points.people.items():
                            if smash_state.rename_tag(b, int(info[3].split("=")[1], 16), tag, name):
                                log(f"name tag {tag} is now {name}")
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

            if quit_until and fighting and ready and active:
                log("the AI quits its match for them")
                quit_pad, quit_steps, quit_until = pads[min(active)], list(QUIT), 0
            elif quit_until and f.index >= quit_until:
                quit_until = 0
            if quit_steps:
                b.pad(quit_pad, *quit_steps.pop(0))
                if not quit_steps:
                    b.release(quit_pad)
                    quit_check = f.index + QUIT_CHECK
            elif quit_check and f.index >= quit_check:
                if fighting:
                    log("the match is still on: unpaused, the AI plays it out")
                    b.pad(quit_pad, BUTTON_PLUS)
                    quit_steps = [(0, 0, 0)] * PRESS_FRAMES
                quit_check = 0
            elif active and len(fighters) > 1:
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
                        if now.get(card, (None,))[0] == smash_state.FIG:
                            log(f"card {card + 1} is {pad_figs[pad]}'s FIG card again")
                            last_ai[card] = pad_figs[pad]  # its amiibo goes back below
                            refig_fails = 0
                        else:
                            log(f"card {card + 1} stays a player card on {pad_figs[pad]}'s controller "
                                f"({REFIG_RIGHT[refig_at]} frames right)")
                            refig_at, refig_fails = (refig_at + 1) % len(REFIG_RIGHT), refig_fails + 1
                            if refig_fails >= len(REFIG_RIGHT):  # every path failed: until the cards change
                                tried, refig_fails = now, 0
                        picking = None
                elif kinds_until and f.index >= kinds_until:
                    for i in converted:
                        b.unpoke(f"kind{i}")
                        b.unpoke(f"kindcopy{i}")
                    kinds_until = 0
                elif count == 0 and not kinds_until and not figs_back and any(
                        p[0] & (BUTTON_PLUS | BUTTON_A) for k, p in f.pads.items()
                        if k not in pad_figs or a.auto):  # --auto presses on the first AI's
                    # The match takes the kind and controller from both tables when it starts:
                    # player cards on the AIs' controllers, built as players, Smash's FIG AI out.
                    # Read from this frame, as + (or A on the "ready" banner) may come right after
                    # the amiibo's nickname; an A that starts nothing only makes them player cards
                    # sooner.
                    if badges:
                        badges.show(b, f)
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
                elif count == 0 and figs_back and f.index >= next_look:
                    # Let go once the character select shows them: it writes the kinds itself.
                    next_look = f.index + LOOK_EVERY
                    shot = smash_farm.grab(b)
                    if f.index >= back_until or shot and smash_farm.screen(shot[0], refs)[0] in ("css", "ready"):
                        for i in figs_back:
                            b.unpoke(f"kind{i}")
                            b.unpoke(f"kindcopy{i}")
                        log("FIG cards let go on the character select")
                        figs_back = []
                elif count == 0 and f.index >= next_look:
                    next_look = f.index + LOOK_EVERY
                    now, owners = smash_state.cards(f), card_owners(f)
                    if badges:
                        # Only on the character select itself, a screenshot tells.
                        wanted = badges.wanted(b, int(info[3].split("=")[1], 16), f)
                        shot = smash_farm.grab(b) if wanted else None
                        css = shot and smash_farm.screen(shot[0], refs)[0] in ("css", "ready")
                        badges.show(b, f, wanted if css else None)
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
                    for i in waiting - set(last_ai):  # one an AI's hand made is that AI's
                        p = owners.get(i, ("", -1))[1]
                        if p in pad_figs and pad_figs[p] not in seated:
                            last_ai[i] = pad_figs[p]
                    back = [(i, last_ai[i]) for i in sorted(waiting & set(last_ai))
                            if last_ai[i] not in seated]
                    if placed and f.index >= placed_until:
                        b.amiibo()
                        placed = None
                    elif back and not placed and b.amiibo(os.path.join(a.amiibo, f"{back[0][1]}.bin")):
                        placed, placed_until = back[0][1], f.index + PLACED_FOR
                        log(f"{placed}'s amiibo back on the reader for card {back[0][0] + 1}")
                    # A player card on an AI's controller: never the AI's, back to its FIG card
                    # (not one a + that started no match made a player card for a while).
                    lost = [(i, p) for i, (kind, chara) in now.items()
                            for p in [owners.get(i, ("", -1))[1]]
                            if kind == smash_state.HUMAN and p in pad_figs
                            and not (kinds_until and i in converted)]
                    if lost and now != tried:
                        # Its presses anywhere but on the character select could open a menu.
                        shot = smash_farm.grab(b)
                        name, dist = smash_farm.screen(shot[0], refs) if shot else ("none", 0)
                        if name in ("css", "ready") and (lost[0][0], len(now)) != (0, 2):
                            log(f"card {lost[0][0] + 1} is a player card on {pad_figs[lost[0][1]]}'s "
                                "controller, no path to its label: not an AI")
                            tried = now
                        elif name in ("css", "ready"):
                            log(f"card 1 is a player card on {pad_figs[lost[0][1]]}'s controller: "
                                "back to its FIG card")
                            b.plug(lost[0][1])  # Eden may have unplugged it as someone joined
                            picking, join_steps = lost[0], refig(REFIG_RIGHT[refig_at])
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
                # The Odin is theirs again: no more menus nor matches started, the AIs still play
                # their FIG cards.
                log(f"controller input from someone else {touched}: Odin given back")
                a.auto = False
                if os.path.exists(TRAIN):
                    os.remove(TRAIN)
                if press_until:
                    for i in range(a.players):
                        b.release(i)
                if steps:
                    b.release(picker)
                quit_until = f.index + QUIT_WITHIN  # the match it may have started
                continue
            if join_steps:  # the AI's hand is on its way to its card
                continue
            if steps:
                b.pad(picker, *steps.pop(0))
                if not steps:
                    b.release(picker)
                    pressed, next_press, starts = BUTTON_A, f.index, 0
                continue
            if fighting:
                starts, picker, idle = 0, None, 0
            if press_until and f.index >= press_until:
                for i in range(a.players):
                    b.release(i)
                press_until = 0
            elif not fighting and a.challengers and f.index >= next_press + PRESS_EVERY:
                # Challenger's Approach: A on its door, on the silhouette, on Mario's select and
                # through the results or the newcomer, back to the door until it is gone.
                shot = smash_farm.grab(b)
                if shot is None:
                    continue
                g = shot[0]
                # Games & More fades in: its door has to stay away a few looks in a row.
                no_door = no_door + 1 if np.abs(g - refs["extras"]).mean() < 25 \
                    and g[DOOR].mean() < 140 else 0
                if no_door >= 3:
                    log("no door on Games & More: every challenger met")
                    return
                if no_door:
                    next_press = f.index
                    continue
                if np.abs(g[:smash_farm.ROSTER.start] - refs["challengers"][:smash_farm.ROSTER.start]
                          ).mean() < 8 and not smash_farm.on_mario(shot[1], mario):
                    log("player 1 picks Mario for the challenger")
                    if not smash_farm.pick_mario(b, mario, lockstep=bool(a.sync)):
                        # Another select (Classic's, A on Games & More once the door left).
                        log("Mario not found: not Challenger's Approach, stopping")
                        return
                    b.ask("stream 1", "ok")  # play stops the stream when done
                    if a.sync:
                        b.sync(a.sync)
                    next_press = f.index
                    continue
                idle += 1
                if idle > 40:
                    log("no challenger fight after 40 presses, stopping")
                    return
                pressed = BUTTON_A
                b.pad(0, pressed)
                press_until, next_press = f.index + PRESS_FRAMES, f.index
            elif not fighting and f.index >= next_press + PRESS_EVERY:
                if count == 0:
                    tags = smash_state.names(f, a.players)
                    slot = next((i for i, t in tags.items() if t == a.name), None) if a.agent else None
                    # No tag read: off the character select, most likely.
                    # With --pad the character select is left as set: player 1 is someone else's.
                    if a.pad is None and (relevel and slot == 0 or starts >= NAVIGATE_AFTER
                                          and a.agent and slot in (0, None)):
                        log("player 1 goes back to the character select")
                        nav = smash_farm.Instance(a.port - 6560, argparse.Namespace(
                            cpu=[], figs=None, home=os.path.expanduser("~/smash-ai")))
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
                    if a.pad is None and starts >= PICK_AFTER and slot not in (0, None):
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
                    if css_seen > 2 and a.agent and a.pad is None and slot == 0:
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
                    # Off the select: the results, or a challenge met, which only player 1's
                    # controller closes, by A on its picture then B on the board.
                    off_css = 0 if name in ("css", "ready") else off_css + 1
                    pressed = (BUTTON_PLUS if not off_css else
                               BUTTON_B if off_css % 2 == 0 else BUTTON_A)
                elif result:
                    pressed = BUTTON_A  # results
                else:
                    continue  # match loading or ending
                # Player 1's controller only: on the character select, a press from player 2's
                # makes it a human player, and the CPU's slot is lost until someone clears it.
                # With --pad, the first AI's: player 1's has no card there.
                for i in ([a.pad, 0] if off_css else [a.pad]) if a.pad is not None else \
                        range(a.players if a.selfplay else 1):
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
