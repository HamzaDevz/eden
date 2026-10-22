#!/usr/bin/env python3
"""Ultimus: reinforcement on top of smash_bc.py, by advantage weighted regression (needs torch).

Every recorded frame, seen from each fighter, gets a reward: damage dealt minus damage taken
(per 100 %), plus or minus one per stock taken or lost. The value head learns the discounted
return and the policy imitates each action weighted by exp(advantage / beta): what led to
stocks is copied more, what lost them less. The CPU level 9 matches (~/smash-ai/rec) and
Ultimus' own matches (~/smash-ai/play, half of each batch) feed the same training, so it
learns from the CPU's good moves and from its own.

  smash_rl.py train [--init ~/smash-ai/models/bc.pt] [--out ~/smash-ai/models/ultimus.pt]
  smash_rl.py loop    retrains after every few new Ultimus matches; once it wins most of its
                      last matches against the CPU, writes "selfplay" to ~/smash-ai/phase
  smash_rl.py stats   Ultimus' results, most recent last
"""
import argparse
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


def returns(d, me):
    other = 1 - me
    r = (gain(d["damage"][:, other]) - gain(d["damage"][:, me])) / 100
    r += gain(-d["stock"][:, other]) - gain(-d["stock"][:, me])
    g, acc = np.zeros(len(r), np.float32), 0.0
    for t in range(len(r) - 1, -1, -1):
        acc = r[t] + GAMMA * acc
        g[t] = acc
    return g


def results(folder):
    """(path, won) of each match Ultimus played against the CPU, oldest first."""
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
        out.append((path, mine > theirs))
    return out


def train(a):
    rec = [p for p in smash_dataset.recordings(a.rec) if smash_bc.sequences([p])]
    play = [p for p in smash_dataset.recordings(a.play) if smash_bc.sequences([p])]
    rec_seqs, play_seqs = smash_bc.sequences(rec, returns), smash_bc.sequences(play, returns)
    device = "mps" if torch.backends.mps.is_available() else "cpu"
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
                "step": a.steps, "init": init}, tmp)
    os.replace(tmp, a.out)  # the recorder reloads it between matches
    log(f"saved {a.out}")


def loop(a):
    trained, phase = -1, os.path.join(HOME, "phase")
    while True:
        res = results(a.play)
        if len(res) - trained >= a.matches or trained < 0:
            train(a)
            shutil.copy(a.out, a.out.replace(".pt", time.strftime("-%Y%m%d-%H%M") + f"-{len(res)}.pt"))
            trained = len(res)
        last = [won for _, won in res[-a.window:]]
        current = open(phase).read().strip() if os.path.exists(phase) else "cpu"
        if current == "cpu" and len(last) == a.window and sum(last) >= a.wins:
            with open(phase, "w") as f:
                f.write("selfplay\n")
            log(f"{sum(last)} wins in the last {a.window} matches against the CPU: self-play")
        time.sleep(60)


def stats(a):
    res = results(a.play)
    for path, won in res:
        print(os.path.basename(path), "won" if won else "lost")
    print(sum(w for _, w in res), "wins out of", len(res))


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
    ap.add_argument("--matches", type=int, default=3, help="new Ultimus matches between trainings")
    ap.add_argument("--window", type=int, default=10)
    ap.add_argument("--wins", type=int, default=6, help="wins in the window that start self-play")
    a = ap.parse_args()
    {"train": train, "loop": loop, "stats": stats}[a.op](a)


if __name__ == "__main__":
    main()
