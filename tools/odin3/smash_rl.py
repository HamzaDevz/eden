#!/usr/bin/env python3
"""Ultimus: reinforcement on top of smash_bc.py, by advantage weighted regression (needs torch).

Every recorded frame, seen from each fighter, gets a reward: damage dealt minus damage taken
(per 100 %), plus or minus one per stock taken or lost. The value head learns the discounted
return and the policy imitates each action weighted by exp(advantage / beta): what led to
stocks is copied more, what lost them less. The CPU level 9 matches (~/smash-ai/rec) and
Ultimus' own matches (~/smash-ai/play, half of each batch) feed the same training, so it
learns from the CPU's good moves and from its own.

  smash_rl.py train [--init ~/smash-ai/models/bc.pt] [--out ~/smash-ai/models/ultimus.pt]
  smash_rl.py loop    keeps the best model as models/champion.pt: each new one, trained from it,
                      plays --trial matches against the CPU and replaces it only if it scored
                      at least as much. The CPU level, in ~/smash-ai/level, goes from 1 up to 9
                      each time the champion wins --wins of its last --trial matches; at 9
                      that writes "selfplay" to ~/smash-ai/phase
  smash_rl.py stats   Ultimus' results, most recent last
"""
import argparse
import collections
import gzip
import json
import os
import shutil
import time

import numpy as np
import torch
from torch import nn

import smash_bc
import smash_dataset

HOME = os.path.expanduser("~/smash-ai")
GAMMA = 0.997  # per frame, a half life of about 4 s
WARMUP = 500  # steps of plain imitation while the value head learns


def log(*args):
    print(time.strftime("%H:%M:%S"), *args, flush=True)


def gain(v):
    """Increase from each frame to the next, 0 when it drops."""
    return np.maximum(np.diff(v, append=v[-1:]), 0)


def reward(d, me):
    other = 1 - me
    r = (gain(d["damage"][:, other]) - gain(d["damage"][:, me])) / 100
    return r + gain(-d["stock"][:, other]) - gain(-d["stock"][:, me])


def returns(d, me):
    r = reward(d, me)
    g, acc = np.zeros(len(r), np.float32), 0.0
    for t in range(len(r) - 1, -1, -1):
        acc = r[t] + GAMMA * acc
        g[t] = acc
    return g


Match = collections.namedtuple("Match", "path won score level version")


def results(folder):
    """Each match Ultimus played alone against the CPU, oldest first: result, total reward,
    CPU level (9 when the recorder was not told) and model version."""
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
        out.append(Match(path, mine > theirs, score, header.get("level") or 9, header.get("version")))
    return out


def train(a):
    rec = [p for p in smash_dataset.recordings(a.rec) if smash_bc.sequences([p])]
    play = [p for p in smash_dataset.recordings(a.play) if smash_bc.sequences([p])]
    rec_seqs, play_seqs = smash_bc.sequences(rec, returns), smash_bc.sequences(play, returns)
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
        parts = smash_bc.batch(rec_seqs, a.batch - half, a.length, rng, device)
        if half:
            more = smash_bc.batch(play_seqs, half, a.length, rng, device)
            parts = [torch.cat(p) for p in zip(parts, more)]
        cont, cat, prev_stick, prev_buttons, stick, buttons, g = parts
        logits, blogits, value, _ = model(cont, cat, prev_stick, prev_buttons)
        ce = nn.functional.cross_entropy(logits.flatten(0, 2), stick.flatten(), reduction="none")
        bce = nn.functional.binary_cross_entropy_with_logits(blogits, buttons, reduction="none")
        imitation = ce.view(stick.shape).sum(-1) + bce.sum(-1)
        adv = (g - value).detach()
        if step > WARMUP:
            w = torch.exp(adv / (adv.std() + 1e-6) / a.beta).clamp(max=20)
        else:
            w = torch.ones_like(adv)
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


def version(path):
    return torch.load(path, map_location="cpu").get("version") if os.path.exists(path) else None


def deploy(src, dst):
    shutil.copy(src, dst + ".tmp")
    os.replace(dst + ".tmp", dst)  # the recorders reload it between matches


def loop(a):
    champion, phase = os.path.join(os.path.dirname(a.out), "champion.pt"), os.path.join(HOME, "phase")
    level_file = os.path.join(HOME, "level")
    if not os.path.exists(champion):
        deploy(a.out, champion)
    a.init = champion
    while True:
        level = int(open(level_file).read()) if os.path.exists(level_file) else 1
        res = [m for m in results(a.play) if m.level == level]
        best, current = version(champion), version(a.out)
        theirs = [m for m in res if m.version == best][-a.trial:]
        mine = [m for m in res if m.version == current][-a.trial:]
        score = lambda ms: sum(m.score for m in ms) / len(ms)
        if len(theirs) == a.trial and sum(m.won for m in theirs) >= a.wins:
            if level < 9:
                with open(level_file, "w") as f:
                    f.write(f"{level + 1}\n")
                log(f"{sum(m.won for m in theirs)} wins in {a.trial} at level {level}: level {level + 1}")
                continue
            if not os.path.exists(phase) or open(phase).read().strip() != "selfplay":
                with open(phase, "w") as f:
                    f.write("selfplay\n")
                log(f"{sum(m.won for m in theirs)} wins in {a.trial} at level 9: self-play")
        if len(theirs) < a.trial:
            if current != best:
                deploy(champion, a.out)
                log(f"champion {best} plays level {level}")
        elif current == best:
            train(a)
        elif len(mine) == a.trial:
            if score(mine) >= score(theirs):
                deploy(a.out, champion)
                shutil.copy(champion, champion.replace(".pt", f"-{current}.pt"))
                log(f"{current} scores {score(mine):.2f} against {score(theirs):.2f} at level {level}: champion")
                continue
            log(f"{current} scores {score(mine):.2f} against {score(theirs):.2f} at level {level}: dropped")
            train(a)
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
    ap.add_argument("--trial", type=int, default=10, help="matches that measure a model")
    ap.add_argument("--wins", type=int, default=6, help="champion wins in --trial that raise the level")
    a = ap.parse_args()
    {"train": train, "loop": loop, "stats": stats}[a.op](a)


if __name__ == "__main__":
    main()
