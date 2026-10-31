#!/usr/bin/env python3
"""Imitation (behavior cloning) of the fighters recorded by smash_record.py (needs torch).

A GRU reads, every frame, both fighters' state from DELAY frames ago plus the pad it sent
on the previous frame, and predicts the pad for this frame: stick x and y (9 bins each)
and the attack, special, jump and guard buttons. The delay covers the bridge round trip,
so the model plays later with the information it really has. Matches are mirrored at
random (x, facing and stick x flipped) to double the data.

Agent also plays without torch (on the console, in Termux) from a numpy copy of the model.

  smash_bc.py train [--rec ~/smash-ai/rec] [--steps 20000] [--out ~/smash-ai/models/bc.pt]
  smash_bc.py npz --model ~/smash-ai/models/ultimus.pt --out ~/smash-ai/models/ultimus.npz
"""
import argparse
import os
import time

import numpy as np
try:
    import torch
    from torch import nn
except ImportError:  # the console plays the numpy copy of the model
    torch = nn = None

import smash_dataset
import smash_state

DELAY = 3
KINDS, STATUSES, MOTIONS = 128, 1024, 8192
BINS = 9  # stick values -1 .. 1 by 0.25
BUTTON_BITS = (0, 1, 2, 3)  # CONTROL_PAD_BUTTON attack, special, jump, guard
CONT = 2 * 10 + 2  # per fighter continuous features, then the relative position
SIGHT = 2 * 5 + 2  # then since 31/10, what the state alone hid: per fighter, then the stage
STAGES = (0, 3)  # Battlefield, Final Destination
EDGE = 80  # |x| of both stages' ledges
# Drawn as they were every frame, a 2 % chance of A made some 70 attacks a minute, and a stick
# drawn toward an edge turned them into smashes in the void: 51 a minute against 3 for the CPU 9
# (31/10). The stick's logits are divided by this before drawing, so only clear directions are
# played. Not the buttons': a press is a rare event every frame, and sharpened it nearly vanished
# (1 attack a minute, 4 to 13 % dealt a match instead of 120 to 165).
TEMPERATURE = 0.3
# On the stage, an attack or a special other than the fireball (stick at rest) is only pressed with
# the other fighter within REACH on both axes. Ultimus hit with 10 % of its presses, half of its
# misses from 30 or more away; this cuts 63 % of them and keeps 73 % of the presses that hit (31/10).
REACH = 30
# Status kinds (StatusModule) read off Mario's own presses on 31/10: shield, jab to air attack,
# grab startups, Mario holding the other fighter (0x3d, then 0x40 throwing), held by it, then the
# specials.
SHIELD = range(0x1B, 0x1F)
ATTACK = range(0x27, 0x37)
GRAB = range(0x37, 0x3C)
HOLDING = range(0x38, 0x3E)
HELD = range(0x41, 0x45)
SPECIAL = 0x1DC
A, B, X, ZR = 0x1, 0x2, 0x4, 0x200
RULES = 4  # version of Agent.rules, kept in the match header


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


def sight(d, i):
    """Distance to the nearer ledge (below 0 off the stage), jumps used, hitstun frames left,
    shield health and standing on a platform."""
    return np.stack([(EDGE - np.abs(d["x"][:, i])) / 100, np.minimum(d["jumps"][:, i], 6) / 3,
                     np.clip(d["hitstun"][:, i], 0, 180) / 60, d["shield"][:, i] / 50,
                     (d["situation"][:, i] == 0) & (d["y"][:, i] > 5)], 1).astype(np.float32)


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
    stage = np.stack([d["stage"] == 0, d["stage"] == 3], 1).astype(np.float32)
    cont = np.concatenate([c0, c1, rel, sight(d, me), sight(d, other), stage], 1)
    cat = np.concatenate([k0, k1], 1)
    stick, buttons = actions(d, me, sign)
    return cont, cat, stick, buttons


class Policy(nn.Module if nn else object):
    def __init__(self, hidden=256):
        super().__init__()
        self.kind = nn.Embedding(KINDS, 16)
        self.status = nn.Embedding(STATUSES, 32)
        self.motion = nn.Embedding(MOTIONS, 32)
        self.prev_stick = nn.Embedding(BINS, 8)
        width = CONT + SIGHT + 2 * (16 + 32 + 32) + 2 * 8 + len(BUTTON_BITS)
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

    def step(self, cont, cat, prev_stick, prev_buttons, h):
        """One frame: stick logits (2, BINS) and button logits as numpy, and the GRU state."""
        t = lambda a, dt: torch.tensor(np.asarray(a)[None, None], dtype=dt)
        with torch.no_grad():
            logits, blogits, _, h = self(t(cont, torch.float32), t(cat, torch.long),
                                         t(prev_stick, torch.long), t(prev_buttons, torch.float32), h)
        return logits[0, 0].numpy(), blogits[0, 0].numpy(), h


class NumpyPolicy:
    """Policy.step in numpy, from the arrays npz() wrote."""

    def __init__(self, path):
        self.w = dict(np.load(path))
        self.version = str(self.w.pop("version")) if "version" in self.w else None

    def step(self, cont, cat, prev_stick, prev_buttons, h):
        w, sigmoid = self.w, lambda v: 1 / (1 + np.exp(-v))
        x = np.concatenate([cont, w["kind.weight"][cat[0]], w["status.weight"][cat[1]],
                            w["motion.weight"][cat[2]], w["kind.weight"][cat[3]],
                            w["status.weight"][cat[4]], w["motion.weight"][cat[5]],
                            w["prev_stick.weight"][prev_stick[0]],
                            w["prev_stick.weight"][prev_stick[1]], np.float32(prev_buttons)])
        x = np.maximum(w["inp.0.weight"] @ x + w["inp.0.bias"], 0)
        x = np.maximum(w["inp.3.weight"] @ x + w["inp.3.bias"], 0)
        h = np.zeros(len(x), np.float32) if h is None else h
        # torch.nn.GRU: reset, update and new gates, in that order.
        gi = np.split(w["gru.weight_ih_l0"] @ x + w["gru.bias_ih_l0"], 3)
        gh = np.split(w["gru.weight_hh_l0"] @ h + w["gru.bias_hh_l0"], 3)
        r, z = sigmoid(gi[0] + gh[0]), sigmoid(gi[1] + gh[1])
        h = (1 - z) * np.tanh(gi[2] + r * gh[2]) + z * h
        return ((w["stick.weight"] @ h + w["stick.bias"]).reshape(2, BINS),
                w["buttons.weight"] @ h + w["buttons.bias"], h)


class Agent:
    """Plays fighter `me` with a trained Policy, one step per bridge frame."""
    RULES = RULES
    STATE = ("kind", "status", "situation", "x", "y", "lr", "damage", "stock", "motion_frame",
             "stick_x", "stick_y", "buttons", *smash_state.EYES)

    def __init__(self, path, me):
        self.path, self.mtime = os.path.expanduser(path), None
        self.me, self.rng = me, np.random.default_rng()
        # One small step per frame: more threads only spun, 12 ms a frame instead of 1.5 on the
        # PC, and in lockstep the game waited for them.
        if torch:
            torch.set_num_threads(1)
        self.reload()
        self.reset()

    def reload(self):
        """Loads the model again if its file changed, True when it did."""
        mtime = os.path.getmtime(self.path)
        if mtime == self.mtime:
            return False
        self.model = load(self.path)
        self.mtime = mtime
        return True

    def reset(self):
        self.h, self.prev = None, None
        self.stick, self.buttons = [BINS // 2] * 2, [0.0] * len(BUTTON_BITS)
        self.recovery = self.since = self.jumps = 0  # recovery 0 drifting, 1 jumped, 2 up special
        self.rose = False
        self.sent, self.wait, self.fire = (0, 0.0, 0.0), 0, 0

    def recover(self, me, prev):
        """Mario's way back to the stage, or None while the model plays.

        The model falls on its own (it comes back about half the time, the CPU 96 %): once off
        the stage, drift toward the ledge, jump when falling, then up special once close enough
        or low, as the CPU does around y -50."""
        ax = abs(me.x)
        if me.situation != 2 or ax < EDGE - 2 and me.y > -5:
            self.recovery = self.jumps = 0
            return None
        if ax <= EDGE + 5 and me.y > -5:
            return None  # just above the ledge
        if me.damage > prev.damage:
            self.recovery = min(self.recovery, 1)  # a hit gives the up special back
        side = 1 if me.x > 0 else -1
        # Under the stage, out past the ledge first.
        lx = side if ax < EDGE + 3 and me.y < -5 else -side if ax > EDGE + 8 else 0
        falling, self.since = me.y < prev.y, self.since + 1
        self.rose |= me.y > prev.y
        low = me.y < -45
        if self.recovery == 0 and falling and not self.buttons[2]:
            self.recovery, self.since, self.rose, self.jumps = 1, 0, False, self.jumps + 1
            return 0x4, lx, 0  # jump
        if self.recovery == 1 and not self.rose and self.since > 8 and self.jumps < 3 and not low:
            self.recovery = 0  # still in hitstun, or no jump left: try again
        elif self.recovery == 1 and self.since > 4 and falling and (ax < EDGE + 15 and me.y < 0 or low):
            self.recovery, self.since = 2, 0
            return 0x2, lx * 0.25, 1  # up special, a little toward the stage
        return 0, lx, 0

    def rules(self, me, o, pme, po):
        """Mario's pad above the stage.

        Shield an attack close by and grab out of shield once the shield is hit, grab a shield,
        follow a hit with up tilts, up smash what falls from above, poke close by, rush in with
        short hops, dash attacks and dash grabs, aerials in the air, fireballs from afar, and wait
        at the ledge for a fighter coming back."""
        side = 1 if o.x > me.x else -1
        dx, dy = abs(o.x - me.x), o.y - me.y
        prev_mask, plx, ply = self.sent
        neutral = abs(plx) < 0.2 and abs(ply) < 0.2
        self.wait, self.fire = max(0, self.wait - 1), max(0, self.fire - 1)

        def press(mask, lx=0.0, ly=0.0):
            # A and B must be released a frame to count again.
            return (mask & ~(prev_mask & (A | B)) if prev_mask & mask & (A | B) else mask), lx, ly

        def smash(lx, ly):
            # A smash takes the stick from rest to the rim with A on the same frame.
            return press(A, lx, ly) if neutral else (0, 0.0, 0.0)

        if me.status in HELD:
            return (A if prev_mask & A == 0 else 0), (1.0 if plx <= 0 else -1.0), 0.0  # mash out
        if me.hitstun > 0:
            return 0, -np.sign(me.x) * 0.7, 0.7  # in, and up against the blast zone
        if me.status in HOLDING:
            return 0, float(np.sign(me.x) or side), 0.0  # throw toward the nearest ledge
        if me.situation == 1:
            return (0 if prev_mask & X else X), 0.0, 0.0  # jump from the ledge
        if me.situation != 0:
            # In the air: aerials on a fighter close by, else drift toward it.
            if dx < 22 and -15 < dy < 25:
                if dy > 8:
                    return press(A, 0.0, 1.0)  # up air
                return press(A, -me.lr, 0.0) if (o.x > me.x) != (me.lr > 0) else press(A)  # back or neutral air
            return 0, float(side), 0.0
        shielding = me.status in SHIELD
        attacking = o.status in ATTACK or o.status >= SPECIAL
        if shielding and me.shield < pme.shield - 1:
            return press(A | ZR)  # the shield took a hit: grab out of it
        if dx < 22 and -10 < dy < 18 and o.status in GRAB:
            return press(ZR, 0.0, -1.0)  # spot dodge the grab
        if dx < 22 and -10 < dy < 20 and attacking and me.shield > 15:
            return ZR, 0.0, 0.0
        o_off = abs(o.x) > EDGE + 5 or o.y < -5 and abs(o.x) > 50 and o.situation == 2
        if o_off or o.situation == 1:
            # At the ledge, facing out: fireballs while it is out there, then what gets up.
            spot = np.sign(o.x) * (EDGE - 15)
            if abs(me.x - spot) > 4:
                return 0, float(np.sign(spot - me.x)) * 0.6, 0.0
            if me.lr != side:
                return 0, side * 0.3, 0.0
            if o_off and not self.fire:
                self.fire = 45
                return press(B)
            return 0, 0.0, 0.0
        close = dx < 22 and -10 < dy < 18
        if o.hitstun > 0 and dx < 20:
            if 5 < dy < 35:
                return press(A, 0.0, 0.45)  # up tilt
            if o.damage > 100 and dy < 8:
                return smash(side, 0.0)
            return press(A)
        if dx < 15 and 10 <= dy < 30 and o.situation == 2 and o.y < po.y:
            return smash(0.0, 1.0)  # up smash, Mario's head is intangible
        if close:
            if o.status in SHIELD:
                return press(A | ZR)  # grab beats shield
            # Hamza found it not aggressive enough waiting 45 frames between pokes (31/10).
            if not self.wait:
                self.wait = 12
                r = self.rng.random()
                if o.damage > 90 and abs(dy) < 8 and r < 0.4:
                    return smash(side, 0.0)
                if dy > 5:
                    return press(A, 0.0, 0.45)  # up tilt
                if r < 0.35:
                    return press(A)  # jab
                if r < 0.6:
                    return press(A, side * 0.5, 0.0)  # forward tilt
                if r < 0.8:
                    return press(A, 0.0, -0.5)  # down tilt
                return press(A | ZR)
            return 0, 0.0, 0.0
        if abs(me.x) > EDGE - 12 and abs(o.x) < abs(me.x):
            return 0, -np.sign(me.x) * 0.6, 0.0  # away from our own ledge
        if dx > 50:
            if me.lr != side:
                return 0, side * 0.3, 0.0
            if not self.fire:
                self.fire = 40
                return press(B)
        elif dx < 35 and not self.wait:
            self.wait = 20
            r = self.rng.random()
            if r < 0.4:
                return press(X, side * 0.6, 0.0)  # short hop in: the air rules take it
            return press(A if r < 0.7 else A | ZR, float(side), 0.0)  # dash attack or dash grab
        return 0, float(side), 0.0  # run in, also between fireballs (it stood 19 % of the time)

    def pad(self, mask, lx, ly):
        return mask, int(lx * 32767), int(ly * 32767)

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
        d["stage"] = np.full(2, me.stage)
        cont, cat, _, _ = perspective(d, 0, 1)
        logits, blogits, self.h = self.model.step(cont[-1], cat[-1], self.stick, self.buttons, self.h)
        probs = np.exp((logits - logits.max(-1, keepdims=True)) / TEMPERATURE)
        self.stick = [int(self.rng.choice(BINS, p=p / p.sum())) for p in probs]
        bprobs = 1 / (1 + np.exp(-blogits))
        self.buttons = [float(self.rng.random() < p) for p in bprobs]
        o = fighters[other]
        if abs(me.x) < EDGE + 5 and me.y > -5 and (abs(o.x - me.x) > REACH or abs(o.y - me.y) > REACH):
            self.buttons[0] = 0.0
            if self.stick != [BINS // 2] * 2:
                self.buttons[1] = 0.0
        back = self.recover(me, prev.get(self.me, me)) or self.rules(me, o, prev.get(self.me, me), prev.get(other, o))
        if back:
            mask, lx, ly = back
            self.sent = back
            # What it sends is the model's previous pad on the next frame.
            self.stick = [int(round(v * (BINS // 2))) + BINS // 2 for v in (lx, ly)]
            self.buttons = [float(bool(mask & m)) for m in (0x1, 0x2, 0x4, 0x200)]
            return self.pad(mask, lx, ly)
        # attack A, special B, jump X, guard ZR
        mask = sum(m for m, on in zip((0x1, 0x2, 0x4, 0x200), self.buttons) if on)
        lx, ly = ((s - BINS // 2) / (BINS // 2) for s in self.stick)
        self.sent = mask, lx, ly
        return self.pad(mask, lx, ly)


def load(path):
    """Policy from a checkpoint; imitation ones have no value head yet, older ones no sight: its
    inputs start unused, so the model plays as before until training weighs them."""
    if path.endswith(".npz"):
        return NumpyPolicy(path)
    checkpoint = torch.load(path, map_location="cpu")
    model, state = Policy(), checkpoint["state"]
    w = state["inp.0.weight"]
    if w.shape[1] == model.inp[0].in_features - SIGHT:
        state["inp.0.weight"] = torch.cat([w[:, :CONT], w.new_zeros(len(w), SIGHT), w[:, CONT:]], 1)
    model.load_state_dict(state, strict=False)
    model.version = checkpoint.get("version")  # set by smash_rl.py, which compares versions
    return model.eval()


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
        # Ultimus' matches recorded before its sight have none to learn from.
        if (len(d["frame"]) < 200 or d["slots"].max() > 1 or stages - set(STAGES)
                or np.isnan(d["shield"]).any()):
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


def npz(a):
    """The model's arrays for NumpyPolicy, written next to it first then renamed."""
    model = load(os.path.expanduser(a.model))
    arrays = {k: v.numpy() for k, v in model.state_dict().items()}
    if model.version is not None:
        arrays["version"] = np.array(str(model.version))
    with open(a.out + ".part", "wb") as f:
        np.savez(f, **arrays)
    os.replace(a.out + ".part", a.out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("op", choices=["train", "npz"])
    ap.add_argument("--model", default="~/smash-ai/models/ultimus.pt")
    ap.add_argument("--rec", default="~/smash-ai/rec")
    ap.add_argument("--out", default=os.path.expanduser("~/smash-ai/models/bc.pt"))
    ap.add_argument("--steps", type=int, default=20000)
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--length", type=int, default=64)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--every", type=int, default=500)
    a = ap.parse_args()
    a.out = os.path.expanduser(a.out)
    train(a) if a.op == "train" else npz(a)


if __name__ == "__main__":
    main()
