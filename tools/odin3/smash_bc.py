#!/usr/bin/env python3
"""Imitation (behavior cloning) of the fighters recorded by smash_record.py (needs torch).

A GRU reads, every frame, both fighters' state from DELAY frames ago plus the pad it sent
on the previous frame, and predicts the pad for this frame: stick x and y (9 bins each)
and the attack, special, jump and guard buttons. The delay covers the bridge round trip,
so the model plays later with the information it really has. Matches are mirrored at
random (x, facing and stick x flipped) to double the data.

  smash_bc.py train [--rec ~/smash-ai/rec] [--steps 20000] [--out ~/smash-ai/models/bc.pt]
"""
import argparse
import os
import time

import numpy as np
import torch
from torch import nn

import smash_dataset

DELAY = 3
KINDS, STATUSES, MOTIONS = 128, 1024, 8192
BINS = 9  # stick values -1 .. 1 by 0.25
BUTTON_BITS = (0, 1, 2, 3)  # CONTROL_PAD_BUTTON attack, special, jump, guard
CONT = 2 * 10 + 2  # per fighter continuous features, then the relative position
STAGES = (0, 3)  # Battlefield, Final Destination


def fighter_features(d, i, sign):
    """Continuous and categorical features of fighter i, x mirrored when sign is -1."""
    x, y = d["x"][:, i] * sign, d["y"][:, i]
    vx, vy = np.diff(x, prepend=x[:1]), np.diff(y, prepend=y[:1])
    cont = np.stack([x / 100, y / 100, d["lr"][:, i] * sign, d["damage"][:, i] / 100,
                     d["stock"][:, i] / 3, np.minimum(d["motion_frame"][:, i], 300) / 60,
                     np.clip(vx, -10, 10), np.clip(vy, -10, 10),
                     d["situation"][:, i] == 0, d["situation"][:, i] == 2], 1)
    motion = d["motion"][:, i].astype(np.uint64)
    cat = np.stack([np.clip(d["kind"][:, i], 0, KINDS - 1),
                    np.clip(d["status"][:, i], 0, STATUSES - 1),
                    ((motion & 0xffffffff) ^ (motion >> 32)) % MOTIONS], 1)
    return cont.astype(np.float32), cat.astype(np.int64)


def actions(d, i, sign):
    """Pad of fighter i: stick bins (T, 2) and buttons (T, 4)."""
    bins = lambda v: np.clip(np.round((v + 1) * 4), 0, BINS - 1).astype(np.int64)
    stick = np.stack([bins(d["stick_x"][:, i] * sign), bins(d["stick_y"][:, i])], 1)
    b = d["buttons"][:, i].astype(np.int64)
    buttons = np.stack([(b >> bit) & 1 for bit in BUTTON_BITS], 1).astype(np.float32)
    return stick, buttons


def perspective(d, me, sign):
    """Model inputs and targets for fighter `me` against the other one."""
    other = 1 - me
    c0, k0 = fighter_features(d, me, sign)
    c1, k1 = fighter_features(d, other, sign)
    rel = np.stack([c1[:, 0] - c0[:, 0], c1[:, 1] - c0[:, 1]], 1)
    cont = np.concatenate([c0, c1, rel], 1)
    cat = np.concatenate([k0, k1], 1)
    stick, buttons = actions(d, me, sign)
    return cont, cat, stick, buttons


class Policy(nn.Module):
    def __init__(self, hidden=256):
        super().__init__()
        self.kind = nn.Embedding(KINDS, 16)
        self.status = nn.Embedding(STATUSES, 32)
        self.motion = nn.Embedding(MOTIONS, 32)
        self.prev_stick = nn.Embedding(BINS, 8)
        width = CONT + 2 * (16 + 32 + 32) + 2 * 8 + len(BUTTON_BITS)
        self.inp = nn.Sequential(nn.Linear(width, hidden), nn.ReLU(), nn.Dropout(0.2),
                                 nn.Linear(hidden, hidden), nn.ReLU(), nn.Dropout(0.2))
        self.gru = nn.GRU(hidden, hidden, batch_first=True)
        self.stick = nn.Linear(hidden, 2 * BINS)
        self.buttons = nn.Linear(hidden, len(BUTTON_BITS))
        self.value = nn.Linear(hidden, 1)  # discounted return, trained by smash_rl.py

    def forward(self, cont, cat, prev_stick, prev_buttons, h=None):
        e = [self.kind(cat[..., 0]), self.status(cat[..., 1]), self.motion(cat[..., 2]),
             self.kind(cat[..., 3]), self.status(cat[..., 4]), self.motion(cat[..., 5]),
             self.prev_stick(prev_stick[..., 0]), self.prev_stick(prev_stick[..., 1])]
        z = self.inp(torch.cat([cont, *e, prev_buttons], -1))
        z, h = self.gru(z, h)
        # The value reads the features without shaping them: its loss pulled them away from
        # the moves and every model trained on top of imitation played worse.
        return self.stick(z).unflatten(-1, (2, BINS)), self.buttons(z), self.value(z.detach())[..., 0], h


class Agent:
    """Plays fighter `me` with a trained Policy, one step per bridge frame."""
    STATE = ("kind", "status", "situation", "x", "y", "lr", "damage", "stock", "motion_frame",
             "stick_x", "stick_y", "buttons")

    def __init__(self, path, me):
        self.path, self.mtime = os.path.expanduser(path), None
        self.me, self.rng = me, np.random.default_rng()
        self.reload()
        self.reset()

    def reload(self):
        """Loads the model again if its file changed, True when it did."""
        mtime = os.path.getmtime(self.path)
        if mtime == self.mtime:
            return False
        self.model = load(self.path)
        self.model.eval()
        self.mtime = mtime
        return True

    def reset(self):
        self.h, self.prev = None, None
        self.stick, self.buttons = [BINS // 2] * 2, [0.0] * len(BUTTON_BITS)

    @torch.no_grad()
    def act(self, fighters):
        """Pad for the next frame: (bridge button mask, lx, ly) as npad values.

        Trained one on one: with more fighters, it faces the nearest one still in the match."""
        me = fighters[self.me]
        others = [i for i in fighters if i != self.me]
        other = min([i for i in others if fighters[i].stock > 0] or others,
                    key=lambda i: (fighters[i].x - me.x) ** 2 + (fighters[i].y - me.y) ** 2)
        prev, self.prev = self.prev or fighters, fighters
        d = {k: np.array([[getattr(r.get(i, fighters[i]), k) for i in (self.me, other)]
                          for r in (prev, fighters)], np.uint64 if k == "motion" else np.float32)
             for k in self.STATE + ("motion",)}
        cont, cat, _, _ = perspective(d, 0, 1)
        t = lambda a, dt: torch.tensor(np.asarray(a)[None, None], dtype=dt)
        logits, blogits, _, self.h = self.model(
            t(cont[-1], torch.float32), t(cat[-1], torch.long), t(self.stick, torch.long),
            t(self.buttons, torch.float32), self.h)
        probs = torch.softmax(logits[0, 0], -1).numpy()
        self.stick = [int(self.rng.choice(BINS, p=p / p.sum())) for p in probs]
        bprobs = torch.sigmoid(blogits[0, 0]).numpy()
        self.buttons = [float(self.rng.random() < p) for p in bprobs]
        # attack A, special B, jump X, guard ZR
        mask = sum(m for m, on in zip((0x1, 0x2, 0x4, 0x200), self.buttons) if on)
        lx, ly = ((s - BINS // 2) / (BINS // 2) for s in self.stick)
        return mask, int(lx * 32767), int(ly * 32767)


def load(path):
    """Policy from a checkpoint; imitation ones have no value head yet."""
    checkpoint = torch.load(path, map_location="cpu")
    model = Policy()
    model.load_state_dict(checkpoint["state"], strict=False)
    model.version = checkpoint.get("version")  # set by smash_rl.py, which compares versions
    return model


def sequences(paths, extra=None, sides=None):
    """Per match and fighter: (cont, cat, prev_stick, prev_buttons, stick, buttons), delay applied,
    then extra(d, me) per frame if given. sides maps each path to the only fighter kept."""
    out = []
    for path in paths:
        d = smash_dataset.load(path)
        ok = d["valid"].all(1)
        d = {k: v[ok] for k, v in d.items() if v.ndim and len(v) == len(ok)}
        stage = d.get("stage", np.zeros(0))  # absent from recordings made before it was read
        stages = set(stage[stage >= 0].tolist())
        if len(d["frame"]) < 200 or d["slots"].max() > 1 or stages - set(STAGES):
            continue
        for me in [sides[path]] if sides else (0, 1):
            more = () if extra is None else (extra(d, me)[DELAY:],)
            for sign in (1, -1):
                cont, cat, stick, buttons = perspective(d, me, sign)
                out.append((cont[:-DELAY], cat[:-DELAY], stick[DELAY - 1:-1], buttons[DELAY - 1:-1],
                            stick[DELAY:], buttons[DELAY:], *more))
    return out


def batch(seqs, size, length, rng, device):
    parts = [[] for _ in seqs[0]]
    for _ in range(size):
        s = seqs[rng.integers(len(seqs))]
        t = rng.integers(len(s[0]) - length)
        for p, a in zip(parts, s):
            p.append(a[t:t + length])
    return [torch.from_numpy(np.stack(p)).to(device) for p in parts]


def loss_and_acc(model, b):
    cont, cat, prev_stick, prev_buttons, stick, buttons = b
    logits, blogits, _, _ = model(cont, cat, prev_stick, prev_buttons)
    loss = nn.functional.cross_entropy(logits.flatten(0, 2), stick.flatten()) + \
        nn.functional.binary_cross_entropy_with_logits(blogits, buttons) * len(BUTTON_BITS)
    acc_stick = (logits.argmax(-1) == stick).float().mean().item()
    pressed = buttons > 0.5
    hit = ((blogits > 0) == pressed) & pressed
    recall = hit.sum().item() / max(1, pressed.sum().item())
    return loss, acc_stick, recall


def train(a):
    # Matches with Pyra/Mythra or Pokemon Trainer are left out by sequences().
    paths = [p for p in smash_dataset.recordings(a.rec) if sequences([p])]
    held = paths[::10] if len(paths) >= 10 else paths[-1:]
    trn = [p for p in paths if p not in held] or held
    train_seqs, val_seqs = sequences(trn), sequences(held)
    device = ("cuda" if torch.cuda.is_available()
              else "mps" if torch.backends.mps.is_available() else "cpu")
    print(f"{len(trn)} train / {len(held)} val matches,",
          sum(len(s[0]) for s in train_seqs) // 4, "frames per view, device", device, flush=True)
    rng = np.random.default_rng(0)
    model = Policy().to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=a.lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, a.steps)
    start, best = time.time(), None
    for step in range(1, a.steps + 1):
        model.train()
        loss, _, _ = loss_and_acc(model, batch(train_seqs, a.batch, a.length, rng, device))
        opt.zero_grad()
        loss.backward()
        nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        opt.step()
        sched.step()
        if step % a.every == 0 or step == a.steps:
            model.eval()
            with torch.no_grad():
                vloss, vacc, vrec = loss_and_acc(model, batch(val_seqs, 256, a.length, rng, device))
            print(f"step {step} loss {loss.item():.3f} val {vloss.item():.3f} "
                  f"stick acc {vacc:.3f} button recall {vrec:.3f} {time.time() - start:.0f} s", flush=True)
            if best is None or vloss.item() < best:
                best = vloss.item()
                os.makedirs(os.path.dirname(a.out), exist_ok=True)
                torch.save({"state": model.state_dict(), "delay": DELAY, "matches": len(trn),
                            "step": step, "val": best}, a.out)
    print(f"saved {a.out} (val {best:.3f})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("op", choices=["train"])
    ap.add_argument("--rec", default="~/smash-ai/rec")
    ap.add_argument("--out", default=os.path.expanduser("~/smash-ai/models/bc.pt"))
    ap.add_argument("--steps", type=int, default=20000)
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--length", type=int, default=64)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--every", type=int, default=500)
    a = ap.parse_args()
    train(a)


if __name__ == "__main__":
    main()
