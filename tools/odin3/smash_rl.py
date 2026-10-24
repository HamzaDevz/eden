#!/usr/bin/env python3
"""Ultimus: reinforcement on top of smash_bc.py, by advantage weighted regression (needs torch).

Every recorded frame, seen from each fighter, gets a reward: damage dealt minus damage taken
(per 100 %), plus or minus one per stock taken or lost. The value head learns the discounted
return. The policy imitates every move of the CPU level 9 matches (~/smash-ai/rec), and in
Ultimus' own matches (~/smash-ai/play, half of each batch) only the moves that did better
than the value head expected, weighted by exp(advantage / beta).

  smash_rl.py train [--init ~/smash-ai/models/bc.pt] [--out ~/smash-ai/models/ultimus.pt]
  smash_rl.py loop    keeps the best model as models/champion.pt: each new one, trained from it,
                      plays --trial matches against the CPU and replaces it only if it scored
                      at least as much as the champion over its last 2 * --trial matches,
                      which smash_farm.py has it play at the same time. The CPU level, in
                      ~/smash-ai/level, goes from 1 up to 9 each time the champion wins --wins
                      of its last --trial matches; at 9 that writes "selfplay" to ~/smash-ai/phase
  smash_rl.py stats   Ultimus' results, most recent last
"""
import argparse
import collections
import gzip
import json
import os
import shutil
import subprocess
import sys
import time

import numpy as np
import torch
from torch import nn

import smash_bc
import smash_dataset

HOME = os.path.expanduser("~/smash-ai")
GAMMA = 0.997  # per frame, a half life of about 4 s
WARMUP = 500  # steps of plain imitation while the value head learns
# Ultimus loses most stocks alone, falling beside the stage: each frame there costs a little,
# before the stock does, and a share of each batch is taken around such frames.
OFFSTAGE_COST = 0.005
FOCUS = 0.25


def log(*args):
    print(time.strftime("%H:%M:%S"), *args, flush=True)


def gain(v):
    """Increase from each frame to the next, 0 when it drops."""
    return np.maximum(np.diff(v, append=v[-1:]), 0)


def reward(d, me):
    other = 1 - me
    r = (gain(d["damage"][:, other]) - gain(d["damage"][:, me])) / 100
    return r + gain(-d["stock"][:, other]) - gain(-d["stock"][:, me])


def offstage(d, me):
    """In the air beside the stage, below its level."""
    return (d["situation"][:, me] == 2) & (d["y"][:, me] < -5) & (np.abs(d["x"][:, me]) > 50)


def returns(d, me):
    """Discounted return of each frame, next to whether the fighter is off the stage."""
    off = offstage(d, me)
    r = reward(d, me) - OFFSTAGE_COST * off
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


Match = collections.namedtuple("Match", "path won score level version me lockstep")


def results(folder):
    """Each match Ultimus played alone against the CPU, oldest first: result, total reward,
    CPU level (9 when the recorder was not told), model version, and whether the game waited
    for the agent (the PC) or not (the Odin, its pads a little late)."""
    out = []
    for path in smash_dataset.recordings(folder):
        with gzip.open(path, "rt") as f:
            header = json.loads(f.readline()[2:])
        me = header.get("me")
        if me is None or len(header.get("agents", [me])) != 1:
            continue
        d = smash_dataset.load(path)
        rows = d["valid"].all(1).nonzero()[0]
        if len(rows) < 200:
            continue
        stock, damage = d["stock"][rows[-1]], d["damage"][rows[-1]]
        mine, theirs = (stock[me], -damage[me]), (stock[1 - me], -damage[1 - me])
        score = reward({k: d[k][rows] for k in ("damage", "stock")}, me).sum()
        out.append(Match(path, mine > theirs, score, header.get("level") or 9, header.get("version"), me,
                         header.get("sync", 1) > 0))
    return out


def current_level():
    path = os.path.join(HOME, "level")
    return int(open(path).read()) if os.path.exists(path) else 1


def train(a):
    rec = [p for p in smash_dataset.recordings(a.rec) if smash_bc.sequences([p])]
    # Ultimus' recent matches at the current level only: hundreds of old falls at level 9
    # would mostly teach it to fall again.
    play = [m for m in results(a.play) if m.level == current_level()][-a.recent:]
    # Ultimus' side only: the weak CPU's moves are no example.
    rec_seqs = smash_bc.sequences(rec, returns)
    play_seqs = smash_bc.sequences([m.path for m in play], returns, {m.path: m.me for m in play})
    hot = lambda seqs: [(s, f) for s in seqs for f in [np.nonzero(s[-1][:, 1])[0]] if len(f)]
    rec_hot, play_hot = hot(rec_seqs), hot(play_seqs)
    device = ("cuda" if torch.cuda.is_available()
              else "mps" if torch.backends.mps.is_available() else "cpu")
    init = a.init if os.path.exists(a.init) else os.path.join(HOME, "models/bc.pt")
    log(f"{len(rec)} CPU matches, {len(play)} Ultimus matches, from {init}, device {device}")
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
    tmp = a.out + ".tmp"
    torch.save({"state": model.state_dict(), "delay": smash_bc.DELAY, "matches": len(rec) + len(play),
                "step": a.steps, "init": init, "version": time.strftime("%Y%m%d-%H%M%S")}, tmp)
    os.replace(tmp, a.out)  # the recorder reloads it between matches
    log(f"saved {a.out}")


def train_apart(a):
    """train() in a process of its own: in the loop's, each one after the first took 20 min
    instead of one on the PC."""
    done = subprocess.run([sys.executable, "-u", os.path.abspath(__file__), "train",
                           *[f"--{k}={v}" for k, v in vars(a).items() if k != "op"]]).returncode == 0
    if not done:  # out of memory while other work shares the PC: the next turn tries again
        log("training failed")
    return done


def version(path):
    return torch.load(path, map_location="cpu").get("version") if os.path.exists(path) else None


def deploy(src, dst):
    shutil.copy(src, dst + ".tmp")
    os.replace(dst + ".tmp", dst)  # the recorders reload it between matches


def loop(a):
    champion, phase = os.path.join(os.path.dirname(a.out), "champion.pt"), os.path.join(HOME, "phase")
    level_file = os.path.join(HOME, "level")  # also read by smash_farm.py
    if not os.path.exists(champion):
        deploy(a.out, champion)
    a.init = champion
    while True:
        level = current_level()
        # Models are measured on the PC only: the Odin's matches train them but play by other rules.
        res = [m for m in results(a.play) if m.level == level and m.lockstep]
        best, current = version(champion), version(a.out)
        # The champion's last matches, played next to the candidate's: ten matches measured
        # alone in the morning were lucky by 1.7 stocks, and no candidate could beat them.
        theirs = [m for m in res if m.version == best][-2 * a.trial:]
        mine = [m for m in res if m.version == current][-a.trial:]
        score = lambda ms: sum(m.score for m in ms) / len(ms)
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
        elif len(mine) == a.trial and len(theirs) >= a.trial:
            if score(mine) >= score(theirs):
                deploy(a.out, champion)
                shutil.copy(champion, champion.replace(".pt", f"-{current}.pt"))
                log(f"{current} scores {score(mine):.2f} against {score(theirs):.2f} at level {level}: champion")
                continue
            log(f"{current} scores {score(mine):.2f} against {score(theirs):.2f} at level {level}: dropped")
            train_apart(a)
        time.sleep(60)


def stats(a):
    res = results(a.play)
    for m in res:
        print(os.path.basename(m.path), "won" if m.won else "lost", f"{m.score:.2f} level {m.level} {m.version}")
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
    ap.add_argument("--recent", type=int, default=300, help="Ultimus matches trained on")
    ap.add_argument("--trial", type=int, default=10, help="matches that measure a model")
    ap.add_argument("--wins", type=int, default=6, help="champion wins in --trial that raise the level")
    a = ap.parse_args()
    {"train": train, "loop": loop, "stats": stats}[a.op](a)


if __name__ == "__main__":
    main()
