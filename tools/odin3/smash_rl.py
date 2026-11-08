#!/usr/bin/env python3
"""Ultimus: reinforcement on top of smash_bc.py, by advantage weighted regression (needs torch).

Every recorded frame, seen from each fighter, gets a reward: damage dealt minus damage taken
(per 100 %), plus or minus one per stock taken or lost, and WIN more for the last stock taken,
which wins the match. The value head learns the discounted
return. The policy imitates every move of the CPU level 9 matches (~/smash-ai/rec), and in
Ultimus' own matches (~/smash-ai/play, half of each batch) only the moves that did better
than the value head expected, weighted by exp(advantage / beta).

  smash_rl.py train [--init ~/smash-ai/models/bc.pt] [--out ~/smash-ai/models/ultimus.pt]
  smash_rl.py loop    keeps the best model as models/champion.pt: each new one, trained from it,
                      plays --trial matches against the CPU and replaces it only if it scored
                      at least as much as the champion over its last 2 * --trial matches,
                      which smash_farm.py has it play at the same time. The CPU level, in
                      ~/smash-ai/level, goes from 1 up to 9 each time the champion wins --wins
                      of its last --trial matches; at 9 that writes "selfplay" to ~/smash-ai/phase.
                      Each of the --ais has its own brain (smash_farm.models()), trained on its
                      own matches; they take turns, one candidate each (Name:k for k turns in a
                      row), the one whose turn it is named in ~/smash-ai/ai for smash_farm.py
  smash_rl.py stats   the AIs' results, most recent last
"""
import argparse
import collections
import glob
import gzip
import json
import math
import os
import shutil
import subprocess
import sys
import time
import zlib

import numpy as np
import torch
from torch import nn

import amiibo
import smash_bc
import smash_dataset
import smash_farm

HOME = os.path.expanduser("~/smash-ai")
GAMMA = 0.997  # per frame, a half life of about 4 s
WARMUP = 500  # steps of plain imitation while the value head learns
# Ultimus loses most stocks alone, falling beside the stage: each frame there costs a little,
# before the stock does, and a share of each batch is taken around such frames.
OFFSTAGE_COST = 0.005
# Both stages end at |x| 80. At level 5 a fifth of its lives were still lost unhit, after
# 4 s in the air out to |x| 139, mostly above the stage's level.
EDGE = 80
# At level 3 it came back 73 % of the time (the CPU 96 %), with no up B in 62 % of the falls
# it lost, or later and lower (y -66, -33 when it came back): half of each batch, not a quarter.
FOCUS = 0.5
# What counts is winning (Hamza): the last stock taken is worth a whole match of lives.
WIN = 3
# A stock lost with no hit taken in the 4 s before: Ultimus fell on its own. The champion of
# 01:14 on 31/10 measured better than the one before by dealing more, yet fell so twice as
# often (0.37 a match against 0.17): in the measure of a model, each such fall costs a tenth of
# a win more.
UNHIT = 240
FALL = 0.1
# An attack or special the other fighter does not feel within WHIFF_WINDOW frames costs WHIFF:
# a landed smash was worth a lot and a missed one nothing, so Ultimus swung in the void.
WHIFF = 0.03
WHIFF_WINDOW = 40


def log(*args):
    print(time.strftime("%H:%M:%S"), *args, flush=True)


def gain(v):
    """Increase from each frame to the next, 0 when it drops."""
    return np.maximum(np.diff(v, append=v[-1:]), 0)


def reward(d, me):
    other = 1 - me
    r = (gain(d["damage"][:, other]) - gain(d["damage"][:, me])) / 100
    stock = d["stock"][:, other]
    taken = gain(-stock)
    won = taken * (np.append(stock[1:], stock[-1:]) == 0)
    return r + taken + WIN * won - gain(-d["stock"][:, me])


def self_destructs(path, me):
    d = smash_dataset.load(path)
    v = d["valid"].all(1)
    dmg, stock = d["damage"][:, me], d["stock"][:, me]
    lost = np.nonzero(v[1:] & v[:-1] & (stock[1:] < stock[:-1]))[0] + 1
    return int(sum(not (np.diff(dmg[max(0, t - UNHIT - 1):t]) > 0).any() for t in lost))


def offstage(d, me):
    """In the air beside the stage, below its level or past its edge."""
    x = np.abs(d["x"][:, me])
    return (d["situation"][:, me] == 2) & ((d["y"][:, me] < -5) & (x > 50) | (x > EDGE + 5))


def whiffs(d, me):
    """Attack or special presses on the stage that hit nothing within WHIFF_WINDOW frames."""
    b = d["buttons"][:, me].astype(np.int64) & 0x3
    pressed = (b & ~np.r_[0, b[:-1]]) > 0
    hits = np.cumsum(gain(d["damage"][:, 1 - me]) > 0)
    later = hits[np.minimum(np.arange(len(hits)) + WHIFF_WINDOW, len(hits) - 1)]
    return pressed & (later == hits) & ~offstage(d, me)


def returns(d, me):
    """Discounted return of each frame, next to whether the fighter is off the stage."""
    off = offstage(d, me)
    r = reward(d, me) - OFFSTAGE_COST * off - WHIFF * whiffs(d, me)
    g, acc = np.zeros(len(r), np.float32), 0.0
    for t in range(len(r) - 1, -1, -1):
        acc = r[t] + GAMMA * acc
        g[t] = acc
    return np.stack([g, off], 1)


def focused_batch(seqs, hot, size, length, rng, device):
    """smash_bc.batch, with FOCUS of the windows around the off-stage frames listed in hot."""
    focus = int(size * FOCUS) if hot else 0
    parts = smash_bc.batch(seqs, size - focus, length, rng, device)
    if not focus:
        return parts
    picked = []
    for _ in range(focus):
        s, frames = hot[rng.integers(len(hot))]
        t = int(np.clip(frames[rng.integers(len(frames))] - rng.integers(length), 0, len(s[0]) - length))
        picked.append([a[t:t + length] for a in s])
    more = [torch.from_numpy(np.stack(p)).to(device) for p in zip(*picked)]
    return [torch.cat(p) for p in zip(parts, more)]


Match = collections.namedtuple("Match", "path won score level version me measured name person stats",
                               defaults=(None,))


def player(header):
    """Whether the PC measured the match (in lockstep against a CPU of the level it set: the Odin's
    have none and may be against Hamza), and the AI that played it (Ultimus before the FIG cards)."""
    return (header.get("sync", 1) > 0 and header.get("level") is not None,
            (header.get("figs") or {}).get(str(header.get("me")), "Ultimus"))


def person(d, me, rows):
    """Whether the AI played someone (Hamza, on the Odin) rather than a CPU."""
    return not d["cpu"][rows[-1]][1 - me]


def match_stats(d, me, rows):
    """Damage the AI dealt and took over the match, and its length in seconds, for performance()."""
    dmg, frames = d["damage"][rows], d["frame"][rows]
    return [float(gain(dmg[:, 1 - me]).sum()), float(gain(dmg[:, me]).sum()), float(frames[-1] - frames[0]) / 60]


def result(path):
    """Match fields after the path, None when no AI played it alone."""
    with gzip.open(path, "rt") as f:
        header = json.loads(f.readline()[2:])
    me = header.get("me")
    if me is None or len(header.get("agents", [me])) != 1:
        return None
    d = smash_dataset.load(path)
    rows = d["valid"].all(1).nonzero()[0]
    if len(rows) < 200:
        return None
    stock, damage = d["stock"][rows[-1]], d["damage"][rows[-1]]
    mine, theirs = (stock[me], -damage[me]), (stock[1 - me], -damage[1 - me])
    score = reward({k: d[k][rows] for k in ("damage", "stock")}, me).sum()
    return (bool(mine > theirs), float(score), header.get("level") or 9, header.get("version"), int(me),
            *player(header), bool(person(d, me, rows)), match_stats(d, me, rows))


def results(folder, ais=()):
    """Each match an AI played alone against the CPU or someone, oldest first: result, total reward,
    CPU level (9 when the recorder was not told), model version, whether the game waited for the
    agent (the PC) or not (the Odin, its pads a little late), the AI, whether it played someone and
    its match_stats(): read again for those of the ais' level_games() kept without them."""
    # Decoding all 16 000 matches took the loop 50 min between two models: a saved match no
    # longer changes, its result is kept in results.json (once a minute old, the Odin's arrive
    # by scp under their final name).
    index = os.path.join(os.path.expanduser(folder), "results.json")
    try:
        with open(index) as f:
            known = json.load(f)
        if known.get("win") != WIN:  # scored by another reward
            known = {}
    except (OSError, ValueError):
        known = {}
    out, fresh = [], False
    for path in smash_dataset.recordings(folder):
        name = os.path.basename(path)
        try:
            r = known[name] if name in known else result(path)
        except (OSError, EOFError, ValueError, zlib.error):  # cut short, as by a full disk
            r = None
        if r and len(r) < 8:  # kept before the AI's name and opponent: read again
            with gzip.open(path, "rt") as f:
                measured, ai = player(json.loads(f.readline()[2:]))
            d = None if measured else smash_dataset.load(path)  # a CPU of the level the PC set
            r = known[name] = [*r[:5], measured, ai,
                               bool(d is not None and person(d, r[4], d["valid"].all(1).nonzero()[0]))]
            fresh = True
        if name not in known and time.time() - os.path.getmtime(path) > 60:
            known[name], fresh = r, True
        if r:
            out.append(Match(path, *r))
    need = {m.path for ai in ais for m in sum(level_games(out, ai), []) if m.stats is None}
    for i, m in enumerate(out):
        if m.path in need:
            d = smash_dataset.load(m.path)
            out[i] = m._replace(stats=match_stats(d, m.me, d["valid"].all(1).nonzero()[0]))
            if os.path.basename(m.path) in known:
                known[os.path.basename(m.path)], fresh = list(out[i])[1:], True
    if fresh:
        known["win"] = WIN
        with open(index + ".tmp", "w") as f:
            json.dump(known, f)
        os.replace(index + ".tmp", index)
    return out


def current_level():
    path = os.path.join(HOME, "level")
    return int(open(path).read()) if os.path.exists(path) else 1


# Levels on the scale of Smash's FIG cards, a CPU k at the level its table gives it (CPU 9 at 44),
# open above 50: SCALE levels more make the odds of a better performance() ten times better. When the
# AIs went up a CPU level their odds of a win fell by a factor 10 every 3.6 to 40 levels, 6 from CPU 8
# to 9 (their results on 1/11); a performance's log-odds are 0.35 to 0.6 times its win's (8 AIs against
# CPU 9 on 3/11): 6 / 0.4.
SCALE = 15
# The true level (Hamza, 3/11): the wins, but also dealing damage without taking any, which is spacing,
# and dealing it fast. PACE is the damage a minute a CPU 9 dealt the AIs (91 to 112 on 3/11).
PERFORMANCE = dict(won=0.4, spacing=0.4, speed=0.2)
PACE = 105
CPU_LEVEL = {k + 1: amiibo.xp_level(xp) for k, xp in enumerate(amiibo.CPU_XP)}


def fit_levels(games, steps=50):
    """Each player's level from games (a, b, won): a an AI, b an AI, Hamza or a CPU's level (int), won
    a's share of a win (performance()), one win and one loss each against a CPU 9 keeping those of few
    games near it."""
    c = math.log(10) / SCALE
    players = {a for a, _, _ in games} | {b for _, b, _ in games if isinstance(b, str)}
    lv = dict.fromkeys(players, float(CPU_LEVEL[9]))
    games = games + [(p, 9, won) for p in players for won in (True, False)]
    for _ in range(steps):  # Newton's, player by player
        grad, curv = dict.fromkeys(players, 0.0), dict.fromkeys(players, 0.0)
        for a, b, won in games:
            other = lv[b] if isinstance(b, str) else CPU_LEVEL[b]
            p = 1 / (1 + math.exp(-c * (lv[a] - other)))
            grad[a] += c * (won - p)
            curv[a] += c * c * p * (1 - p)
            if isinstance(b, str):
                grad[b] -= c * (won - p)
                curv[b] += c * c * p * (1 - p)
        for p in players:
            lv[p] += max(min(grad[p] / curv[p], 10), -10)
    return lv


def level_games(res, ai):
    """ai's last 100 matches measured against a CPU, and its last 50 against Hamza."""
    mine = [m for m in res if m.name == ai]
    return [m for m in mine if m.measured][-100:], [m for m in mine if m.person][-50:]


def performance(m):
    """m's outcome in [0, 1] for fit_levels: PERFORMANCE's mix of its win, its share of the damage
    dealt, and the pace of its damage against PACE's; its win alone when kept without match_stats()."""
    if m.stats is None:
        return float(m.won)
    dealt, taken, seconds = m.stats
    share = dealt / (dealt + taken) if dealt + taken else 0.5
    pace = dealt * 60 / max(seconds, 1)
    w = PERFORMANCE
    return w["won"] * m.won + w["spacing"] * share + w["speed"] * pace / (pace + PACE)


def show_levels(res, ais):
    """Each AI's level from its level_games(), as its FIG's: in its amiibo on every instance here, and in
    HOME/strength.json, with Hamza's. The Odin's FIGs show the level of their Smash points instead
    (smash_record.global_levels(), 3/11)."""
    games = []
    for ai in ais:
        cpu, hamza = level_games(res, ai)
        games += [(ai, m.level, performance(m)) for m in cpu]
        games += [(ai, "Hamza", performance(m)) for m in hamza]
    if not games:
        return
    s = {p: round(v, 2) for p, v in fit_levels(games).items()}
    with open(os.path.join(HOME, "strength.json.tmp"), "w") as f:
        json.dump(s, f)
    try:
        os.replace(os.path.join(HOME, "strength.json.tmp"), os.path.join(HOME, "strength.json"))
    except PermissionError:
        pass  # Windows: pcsync is reading it; the next round writes it again
    for ai in ais:
        shown = set()
        for path in glob.glob(os.path.join(smash_farm.ROOT, "*", "amiibo", f"{amiibo.shown(ai)}.bin")) if ai in s else []:
            try:
                levels = amiibo.set_level(path, s[ai])
            except OSError:  # Eden reading it: next time
                continue
            if levels and levels[0] != levels[1]:
                shown.add(levels[1])
        if shown:
            log(f"{ai}'s FIG: level {max(shown)} ({s[ai]})" + (f", Hamza {s['Hamza']}" if "Hamza" in s else ""))


def train(a):
    rec = [p for p in smash_dataset.recordings(a.rec) if smash_bc.sequences([p])]
    # The AI's recent matches at the current level only: hundreds of old falls at level 9
    # would mostly teach it to fall again.
    play = [m for m in results(a.play) if m.level == current_level() and m.name == a.ai][-a.recent:]
    # Ultimus' side only: the weak CPU's moves are no example.
    rec_seqs = smash_bc.sequences(rec, returns)
    play_seqs = smash_bc.sequences([m.path for m in play], returns, {m.path: m.me for m in play})
    hot = lambda seqs: [(s, f) for s in seqs for f in [np.nonzero(s[-1][:, 1])[0]] if len(f)]
    rec_hot, play_hot = hot(rec_seqs), hot(play_seqs)
    device = ("cuda" if torch.cuda.is_available()
              else "mps" if torch.backends.mps.is_available() else "cpu")
    init = a.init if os.path.exists(a.init) else os.path.join(HOME, "models/bc.pt")
    log(f"{len(rec)} CPU matches, {len(play)} {a.ai} matches, from {init}, device {device}")
    model = smash_bc.load(init).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=a.lr, weight_decay=1e-4)
    rng = np.random.default_rng()
    start = time.time()
    for step in range(1, a.steps + 1):
        model.train()
        half = a.batch // 2 if play_seqs else 0
        parts = focused_batch(rec_seqs, rec_hot, a.batch - half, a.length, rng, device)
        if half:
            more = focused_batch(play_seqs, play_hot, half, a.length, rng, device)
            parts = [torch.cat(p) for p in zip(parts, more)]
        cont, cat, prev_stick, prev_buttons, stick, buttons, g = parts
        g = g[..., 0]
        logits, blogits, value, _ = model(cont, cat, prev_stick, prev_buttons)
        ce = nn.functional.cross_entropy(logits.flatten(0, 2), stick.flatten(), reduction="none")
        bce = nn.functional.binary_cross_entropy_with_logits(blogits, buttons, reduction="none")
        imitation = ce.view(stick.shape).sum(-1) + bce.sum(-1)
        adv = (g - value).detach()
        w = torch.ones_like(adv)
        mine = adv[a.batch - half:]
        if half and step > WARMUP:
            # Most of Ultimus' moves led to falls: weighted down, they were still copied.
            w[a.batch - half:] = torch.exp(mine / (mine.std() + 1e-6) / a.beta).clamp(max=20) * (mine > 0)
        else:  # while the value head learns, only the CPU's moves are imitated
            w[a.batch - half:] = 0
        vloss = nn.functional.mse_loss(value, g)
        loss = (w * imitation).mean() + a.vcoef * vloss
        opt.zero_grad()
        loss.backward()
        nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        opt.step()
        if step % a.every == 0 or step == a.steps:
            log(f"step {step} imitation {imitation.mean().item():.3f} value {vloss.item():.4f} "
                f"weight max {w.max().item():.1f} {time.time() - start:.0f} s")
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    style = smash_bc.mutate(model.style, rng)
    tmp = a.out + ".tmp"
    torch.save({"state": model.state_dict(), "delay": smash_bc.DELAY, "matches": len(rec) + len(play),
                "step": a.steps, "init": init, "version": time.strftime("%Y%m%d-%H%M%S"), "style": style}, tmp)
    os.replace(tmp, a.out)  # the recorder reloads it between matches
    log(f"saved {a.out}, style {style}")


def train_apart(a):
    """train() in a process of its own: in the loop's, each one after the first took 20 min
    instead of one on the PC."""
    done = subprocess.run([sys.executable, "-u", os.path.abspath(__file__), "train",
                           *[f"--{k}={v}" for k, v in vars(a).items() if k != "op"]]).returncode == 0
    if not done:  # out of memory while other work shares the PC: the next turn tries again
        log("training failed")
    return done


def version(path):
    return torch.load(path, map_location="cpu", weights_only=False).get("version") if os.path.exists(path) else None


def deploy(src, dst):
    shutil.copy(src, dst + ".tmp")
    os.replace(dst + ".tmp", dst)  # the recorders reload it between matches


def loop(a):
    phase = os.path.join(HOME, "phase")
    level_file, ai_file = os.path.join(HOME, "level"), os.path.join(HOME, "ai")  # also read by smash_farm.py
    # Turns in a row cost no restart of the instances, which a change of AI does (4 min of 15).
    turns = [n for spec in a.ais.split(",") for n in [spec.partition(":")[0]] * int(spec.partition(":")[2] or 1)]
    ais = list(dict.fromkeys(turns))
    named = open(ai_file).read().strip() if os.path.exists(ai_file) else None
    turn = turns.index(named) if named in turns else 0  # a restarted loop goes on with the same AI
    falls = {}  # self_destructs() of the matches measured, by path
    looked = {}  # matches of each candidate already judged

    def name_next(ai):
        """Tells smash_farm.py which AI to play, once its models exist: its instances restart on it."""
        candidate, champion = smash_farm.models(HOME, ai)
        if not os.path.exists(champion):  # a new AI starts from Ultimus' brain
            deploy(smash_farm.models(HOME, ais[0])[1], champion)
        if not os.path.exists(candidate):
            deploy(champion, candidate)
        if not os.path.exists(ai_file) or open(ai_file).read().strip() != ai:
            with open(ai_file, "w") as f:
                f.write(ai + "\n")
            log(f"{ai}'s turn")

    waiting = None
    while True:
        try:
            # Each AI has its brain, trained on its own matches: one turn each, a candidate measured.
            a.ai = turns[turn % len(turns)]
            name_next(a.ai)
            a.out, champion = smash_farm.models(HOME, a.ai)
            a.init = champion
            level = current_level()
            # Models are measured on the PC only: the Odin's matches train them but play by other rules.
            res = results(a.play, ais)
            show_levels(res, ais)
            res = [m for m in res if m.level == level and m.measured and m.name == a.ai]
            best, current = version(champion), version(a.out)
            # The champion's last matches, played next to the candidate's: ten matches measured
            # alone in the morning were lucky by 1.7 stocks, and no candidate could beat them.
            theirs = [m for m in res if m.version == best][-a.reference:]
            # Judged every --trial matches: one match scores within 0.2 of another while candidates
            # differ by a few hundredths at most, and keeping a candidate at least as good on ten
            # matches kept one in two by luck, Ultimus drifting down (8/11).
            played = [m for m in res if m.version == current][:a.most]
            mine = played[:len(played) // a.trial * a.trial]
            for m in mine + theirs:
                if m.path not in falls:
                    falls[m.path] = self_destructs(m.path, m.me)
            # The true level decides (3/11): wins, spacing and speed.
            scores = lambda ms: np.array([performance(m) - FALL * falls[m.path] for m in ms])
            score = lambda ms: scores(ms).mean()
            fell = lambda ms: sum(falls[m.path] for m in ms) / len(ms)
            # How far luck alone takes the candidate's mean from the champion's.
            noise = lambda: math.sqrt(scores(mine).var(ddof=1) / len(mine) + scores(theirs).var(ddof=1) / len(theirs))
            if len(theirs) >= a.trial and sum(m.won for m in theirs[-a.trial:]) >= a.wins:
                if level < 9:
                    with open(level_file, "w") as f:
                        f.write(f"{level + 1}\n")
                    log(f"{sum(m.won for m in theirs[-a.trial:])} wins in {a.trial} at level {level}: level {level + 1}")
                    continue
                if not os.path.exists(phase) or open(phase).read().strip() != "selfplay":
                    with open(phase, "w") as f:
                        f.write("selfplay\n")
                    log(f"{sum(m.won for m in theirs[-a.trial:])} wins in {a.trial} at level 9: self-play")
            if current == best:
                train_apart(a)
            elif len(mine) > looked.get(current, 0) and len(theirs) >= a.trial:
                looked[current] = len(mine)
                gap, margin = score(mine) - score(theirs), a.z * noise()
                measure = (f"{current} scores {score(mine):.2f} in {len(mine)} against {score(theirs):.2f} in "
                           f"{len(theirs)}, {gap:+.3f} for {margin:.3f} needed, falls on its own "
                           f"{fell(mine):.2f} a match against {fell(theirs):.2f}, at level {level}")
                if gap > margin:
                    deploy(a.out, champion)
                    shutil.copy(champion, champion.replace(".pt", f"-{current}.pt"))
                    log(f"{a.ai} {measure}: champion")
                elif gap > 0 and len(mine) < a.most:  # promising: measured on
                    log(f"{a.ai} {measure}: more matches")
                    continue
                else:
                    log(f"{a.ai} {measure}: dropped")
                # The next AI plays while this one's next candidate trains.
                turn += 1
                name_next(turns[turn % len(turns)])
                train_apart(a)
                continue
            if (current, len(played), len(theirs)) != waiting:  # rl.log moves while the matches come in
                waiting = current, len(played), len(theirs)
                log(f"{a.ai} {current}: {len(played)} of {looked.get(current, 0) + a.trial} matches measured, "
                    f"champion {len(theirs)}")
            time.sleep(60)
        except MemoryError:  # other work on the PC took it all: wait for it to finish
            log("out of memory, next turn in 5 min")
            time.sleep(300)


def stats(a):
    res = results(a.play)
    for m in res:
        print(os.path.basename(m.path), m.name, "won" if m.won else "lost", f"{m.score:.2f} level {m.level} {m.version}")
    print(sum(m.won for m in res), "wins out of", len(res))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("op", choices=["train", "loop", "stats"])
    ap.add_argument("--rec", default=os.path.join(HOME, "rec"))
    ap.add_argument("--play", default=os.path.join(HOME, "play"))
    ap.add_argument("--init", default=os.path.join(HOME, "models/ultimus.pt"))
    ap.add_argument("--out", default=os.path.join(HOME, "models/ultimus.pt"))
    ap.add_argument("--steps", type=int, default=3000)
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--length", type=int, default=64)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--beta", type=float, default=1.0, help="advantage temperature, in std units")
    ap.add_argument("--vcoef", type=float, default=1.0)
    ap.add_argument("--every", type=int, default=500)
    ap.add_argument("--recent", type=int, default=300, help="the AI's matches trained on")
    ap.add_argument("--ai", default="Ultimus", help="the AI trained, by its matches")
    # Ultimus first (Hamza, 1/11): 16 turns in a row of 7 min against one of 13 for each other AI, four
    # candidates of its own for each one when they each had one in turn.
    ap.add_argument("--ais", default="Ultimus,Ganon,Link,Zelda,Mac,Bowser", help="the AIs the loop trains in turn")
    ap.add_argument("--trial", type=int, default=10, help="matches between two looks at a candidate")
    ap.add_argument("--most", type=int, default=40, help="matches a candidate is measured on at most")
    ap.add_argument("--reference", type=int, default=60, help="the champion's last matches it is compared to")
    # 2.4 standard errors keep 3 candidates in 100 no better than their champion, over four looks (8/11).
    ap.add_argument("--z", type=float, default=2.4, help="standard errors a candidate has to win by")
    ap.add_argument("--wins", type=int, default=6, help="champion wins in --trial that raise the level")
    a = ap.parse_args()
    {"train": train, "loop": loop, "stats": stats}[a.op](a)


if __name__ == "__main__":
    main()
