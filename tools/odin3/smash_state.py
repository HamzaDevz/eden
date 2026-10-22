#!/usr/bin/env python3
"""Smash Ultimate 13.0.2 fighter state, streamed every frame through the game bridge.

Each field is a pointer chain from the FighterManager singleton, walked again by Eden on
every frame, so it follows the fighters across matches without re-resolving anything.
The current fighter is taken at slot 0 of its entry (Pokemon Trainer swaps are not
followed yet).

  smash_state.py [--players 2] [--every 30]   print the state of each fighter
"""
import argparse
import struct

from bridge import Bridge

FIGHTER_MANAGER = "main+0x52b94f8"  # lib::Singleton<app::FighterManager>::instance_

ACC = (0x4160, 0x20)  # current fighter, then its BattleObjectModuleAccessor

# name: (length, offsets after the entry pointer)
FIELDS = {
    "id": (8, (0x4160, 0x8)),  # battle object id, fighter kind
    "info": (0x4c, (0xf8, 0x90)),  # is_operation_cpu at +2, stock count at +0x48
    "pos": (0x80, (*ACC, 0x38, 0x30)),  # PostureModule: x, y, z, lr at +0x7c
    "dmg": (4, (*ACC, 0xa8, 0x18, 0)),  # DamageModule: damage(0)
    "status": (0x48, (*ACC, 0x40, 0x98)),  # StatusModule: kind, situation at +0x44
    "motion": (0xc, (*ACC, 0x88, 0x68)),  # MotionModule: hash40 kind, frame
    # ControlModule pad, also fed by the CPU: stick x, y, CONTROL_PAD_BUTTON bits at +0x6c
    "ctrl": (0x70, (*ACC, 0x48, 0x110, 0x10)),
    "slots": (0x8, (0x4150,)),  # fighters in the entry (Pokemon Trainer, Pyra/Mythra)
    "slot": (1, (0x5918,)),  # current one, only slot 0 is read above
}

# WorkModule arrays (table 0 instance, 1 status): floats, ints (8-byte stride), flag words.
WORK = {
    "wf0": (0x1e0, (*ACC, 0x50, 0x18, 0, 0x10, 0)),
    "wi0": (0x870, (*ACC, 0x50, 0x18, 0, 0x20, 0)),
    "wb0": (0x24, (*ACC, 0x50, 0x18, 0, 0x30, 0)),
    "wf1": (0xa0, (*ACC, 0x50, 0x18, 8, 0x10, 0)),
    "wi1": (0x100, (*ACC, 0x50, 0x18, 8, 0x20, 0)),
    "wb1": (0xc, (*ACC, 0x50, 0x18, 8, 0x30, 0)),
}


class Fighter:
    __slots__ = ("kind", "cpu", "stock", "x", "y", "lr", "damage", "status", "situation",
                 "motion", "motion_frame", "stick_x", "stick_y", "buttons", "slots", "slot")

    def __str__(self):
        return (f"kind {self.kind:#04x}{' cpu' if self.cpu else ''} stock {self.stock} "
                f"x {self.x:8.2f} y {self.y:8.2f} lr {self.lr:+.0f} {self.damage:5.1f}% "
                f"status {self.status:#x} sit {self.situation} "
                f"motion {self.motion:#x}@{self.motion_frame:.1f} "
                f"pad {self.stick_x:+.2f},{self.stick_y:+.2f} {self.buttons:#x}")


def watch_all(bridge, players, work=False):
    fields = {**FIELDS, **WORK} if work else FIELDS
    for i in range(players):
        for name, (length, offs) in fields.items():
            chain = " ".join(f"{o:#x}" for o in (0, 0x20 + i * 8, *offs))
            bridge.ask(f"watch {i}{name} {length:#x} {FIGHTER_MANAGER} {chain}", "ok")


def parse(frame, players):
    fighters = {}
    for i in range(players):
        w = {name: frame.watches.get(f"{i}{name}") for name in FIELDS}
        if any(v is None for v in w.values()):
            continue
        f = Fighter()
        f.kind = struct.unpack_from("<I", w["id"], 4)[0]
        f.cpu, f.stock = w["info"][2], struct.unpack_from("<I", w["info"], 0x48)[0]
        f.x, f.y = struct.unpack_from("<ff", w["pos"])
        f.lr = struct.unpack_from("<f", w["pos"], 0x7c)[0]
        f.damage = struct.unpack("<f", w["dmg"])[0]
        f.status = struct.unpack_from("<i", w["status"])[0]
        f.situation = struct.unpack_from("<i", w["status"], 0x44)[0]
        f.motion, f.motion_frame = struct.unpack("<Qf", w["motion"])
        f.stick_x, f.stick_y = struct.unpack_from("<ff", w["ctrl"])
        f.buttons = struct.unpack_from("<I", w["ctrl"], 0x6c)[0]
        f.slots, f.slot = struct.unpack("<Q", w["slots"])[0], w["slot"][0]
        fighters[i] = f
    return fighters


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--players", type=int, default=2)
    ap.add_argument("--every", type=int, default=30)
    ap.add_argument("--frames", type=int, default=0, help="stop after this many frames")
    a = ap.parse_args()
    b = Bridge(a.host)
    b.ask("unwatch *", "ok")
    watch_all(b, a.players)
    b.ask("stream 1", "ok")
    for n, frame in enumerate(b.frames(), 1):
        if n % a.every == 0:
            fighters = parse(frame, a.players)
            print(frame.index, " | ".join(f"p{i} {f}" for i, f in fighters.items()) or "-",
                  flush=True)
        if n == a.frames:
            break


if __name__ == "__main__":
    main()
