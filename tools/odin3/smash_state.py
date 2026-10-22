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

# name: (length, offsets after the entry pointer)
FIELDS = {
    "id": (8, (0x4160, 0x8)),  # battle object id, fighter kind
    "info": (0x4c, (0xf8, 0x90)),  # is_operation_cpu at +2, stock count at +0x48
    "pos": (0x80, (0x4160, 0x20, 0x38, 0x30)),  # PostureModule: x, y, z, lr at +0x7c
    "dmg": (4, (0x4160, 0x20, 0xa8, 0x18, 0)),  # DamageModule: damage(0)
    "status": (0x48, (0x4160, 0x20, 0x40, 0x98)),  # StatusModule: kind, situation at +0x44
    "motion": (0xc, (0x4160, 0x20, 0x88, 0x68)),  # MotionModule: hash40 kind, frame
}


class Fighter:
    __slots__ = ("kind", "cpu", "stock", "x", "y", "lr", "damage", "status", "situation",
                 "motion", "motion_frame")

    def __str__(self):
        return (f"kind {self.kind:#04x}{' cpu' if self.cpu else ''} stock {self.stock} "
                f"x {self.x:8.2f} y {self.y:8.2f} lr {self.lr:+.0f} {self.damage:5.1f}% "
                f"status {self.status:#x} sit {self.situation} "
                f"motion {self.motion:#x}@{self.motion_frame:.1f}")


def watch_all(bridge, players):
    for i in range(players):
        for name, (length, offs) in FIELDS.items():
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
