#!/usr/bin/env python3
"""Smash Ultimate 13.0.2 fighter state, streamed every frame through the game bridge.

Each field is a pointer chain from the FighterManager singleton, walked again by Eden on
every frame, so it follows the fighters across matches without re-resolving anything.
The current fighter is taken at slot 0 of its entry (Pokemon Trainer swaps are not
followed yet).

  smash_state.py [--players 2] [--every 30]   print the state of each fighter
"""
import argparse
import math
import struct

from bridge import Bridge

FIGHTER_MANAGER = "main+0x52b94f8"  # lib::Singleton<app::FighterManager>::instance_
# app::stage::get_stage_id(): u32 [[[[main+0x532a9d8]]+0x148]+8], Battlefield is 0, Final
# Destination 3
STAGE_WATCH = "watch stage 4 main+0x532a9d8 0 0x148 8"
# Static player table, one 0x260 entry per slot in FighterManager entry order: name tag in
# UTF-16 at +0x28 (10 characters), empty for CPUs.
PLAYERS, PLAYER_SIZE, NAME = 0x52c5730, 0x260, 0x28
# Static character select cards, one 0x1c8 entry per card (the 1-based card number at +0): u32
# kind at +8, ui_chara hash40 of the fighter at +0x10 (ui_chara_random until one is picked),
# amiibo nickname of a FIG card in UTF-16 at +0xd0 (10 characters, empty while it waits for one).
CARDS, CARD_SIZE, CARD_KIND, CARD_CHARA, CARD_NICK = 0x5308798, 0x1c8, 0x8, 0x10, 0xd0
HUMAN, CPU, FIG, NO_CARD = 0, 1, 2, 3
# Second card table, one 0x240 entry per card: s32 controller (npad, -1 for none) at +0, the
# name tag's record in the save data (TAGS) at +4, then a copy of the card at +0x88. The match
# takes each fighter's kind and controller from both.
CARD_PADS, CARD_PAD_SIZE, CARD_TAG, CARD_COPY = 0x53095b0, 0x240, 0x4, 0x88
CARD_LEVEL = 0x21  # u8, a CPU card's level, 1 to 9 on the select (poked to 1, 3/11: a CPU that barely hits)
MARIO = 0x0edaf3c863  # hash40("ui_chara_mario")
ENTRY_PAD = 0x10  # s32 controller of a FighterEntry, -1 for a CPU or a FIG
# The save data the game works on and writes to system_data.bin, at [[main+0x5314508]] (a second
# copy has no fixed path): a name tag there is a u32 count of characters, then its name in UTF-16
# and a 0 (10 characters at most, in 0x18 bytes that may keep an older name's after the 0).
SAVE = 0x5314508
TAGS, TAG_SIZE = 0x42f8, 0xf7d8  # its name tag records, each starting with the name (Hamz is 1)

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

# FighterInformation's data at [entry+0xf8], which "info" reads from +0x90: u32 falls per period
# at +0xcc (lua_bind dead_count), then the KOs it scored on each entry, u32 [3 periods][8] at
# +0xdc (total_beat_count).
KOS = (0x60, (0xf8, 0xdc))
TEAM = (4, (*ACC, 0xd8, 0x10))  # TeamModule: team number, each fighter's own outside team battles
# The DamageLog of the DamageModule (damage_log() adds 0x80), the last hit taken: s32 object_id_ of
# what dealt it, then owner_object_id_ (the fighter of a projectile), named by its table's keys.
HIT_BY = (8, (*ACC, 0xa8, 0x100))
# The log keeps its last hit while damage comes from elsewhere (~1 % with no one near, on 1/11): a
# fighter's own hit from farther than this (in stage units, real hits at 40 at most) is no one's.
REACH = 100
TEAM_BATTLE = 0x52c51e8  # byte, 1 with team battles in the rules, on the character select too

# WorkModule arrays (table 0 instance, 1 status): floats, ints (8-byte stride), flag words.
WORK = {
    "wf0": (0x1e0, (*ACC, 0x50, 0x18, 0, 0x10, 0)),
    "wi0": (0x870, (*ACC, 0x50, 0x18, 0, 0x20, 0)),
    "wb0": (0x24, (*ACC, 0x50, 0x18, 0, 0x30, 0)),
    "wf1": (0xa0, (*ACC, 0x50, 0x18, 8, 0x10, 0)),
    "wi1": (0x100, (*ACC, 0x50, 0x18, 8, 0x20, 0)),
    "wb1": (0xc, (*ACC, 0x50, 0x18, 8, 0x30, 0)),
}
# Single WorkModule values, watched on their own when the arrays are not: (array, index), found
# in the CPU's matches on 31/10. Jumps used (0 on the ground, up to 6 for Kirby), hitstun frames
# left, shield health (50 when full).
EYES = {"jumps": ("wi0", 2), "hitstun": ("wf0", 19), "shield": ("wf0", 6)}


class Fighter:
    __slots__ = ("kind", "cpu", "stock", "x", "y", "lr", "damage", "status", "situation",
                 "motion", "motion_frame", "stick_x", "stick_y", "buttons", "slots", "slot",
                 "jumps", "hitstun", "shield", "stage")

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
        for name, (table, j) in EYES.items():
            size = 8 if table.startswith("wi") else 4
            chain = " ".join(f"{o:#x}" for o in (0, 0x20 + i * 8, *WORK[table][1][:-1], j * size))
            bridge.ask(f"watch {i}{name} {size:#x} {FIGHTER_MANAGER} {chain}", "ok")
        chain = " ".join(f"{o:#x}" for o in (0, 0x20 + i * 8, *KOS[1]))
        bridge.ask(f"watch {i}kos {KOS[0]:#x} {FIGHTER_MANAGER} {chain}", "ok")
        chain = " ".join(f"{o:#x}" for o in (0, 0x20 + i * 8, *TEAM[1]))
        bridge.ask(f"watch {i}team {TEAM[0]:#x} {FIGHTER_MANAGER} {chain}", "ok")
        chain = " ".join(f"{o:#x}" for o in (0, 0x20 + i * 8, *HIT_BY[1]))
        bridge.ask(f"watch {i}hitby {HIT_BY[0]:#x} {FIGHTER_MANAGER} {chain}", "ok")
        bridge.ask(f"watch {i}tag 0x14 main+{PLAYERS + i * PLAYER_SIZE + NAME:#x}", "ok")


def watch_cards(bridge):
    for i in range(8):
        bridge.ask(f"watch {i}card {CARD_NICK + 0x14:#x} main+{CARDS + i * CARD_SIZE:#x}", "ok")
        bridge.ask(f"watch {i}cpad {CARD_TAG + 4} main+{CARD_PADS + i * CARD_PAD_SIZE:#x}", "ok")


def cards(frame):
    """Kind and fighter hash40 of each character select card that is not closed."""
    out = {}
    for i in range(8):
        raw = frame.watches.get(f"{i}card")
        if raw:
            kind = int.from_bytes(raw[CARD_KIND:CARD_KIND + 4], "little")
            if kind != NO_CARD:
                chara = int.from_bytes(raw[CARD_CHARA:CARD_CHARA + 8], "little")
                out[i] = kind, chara & (1 << 40) - 1
    return out


def card_owners(frame):
    """Amiibo nickname and controller (-1 for none) of each character select card."""
    out = {}
    for i in range(8):
        raw, pad = frame.watches.get(f"{i}card"), frame.watches.get(f"{i}cpad")
        if raw and pad:
            nick = raw[CARD_NICK:CARD_NICK + 0x14].decode("utf-16-le", "replace").split("\0")[0]
            out[i] = nick, struct.unpack_from("<i", pad)[0]
    return out


def card_tags(frame):
    """Name tag record (tag_name()) of each character select card."""
    return {i: struct.unpack_from("<i", raw, CARD_TAG)[0] for i in range(8)
            for raw in [frame.watches.get(f"{i}cpad")] if raw and len(raw) >= CARD_TAG + 4}


def names(frame, players):
    """Name tag of each player slot, "" when none."""
    out = {}
    for i in range(players):
        raw = frame.watches.get(f"{i}tag")
        out[i] = raw.decode("utf-16-le", "replace").split("\0")[0] if raw else ""
    return out


def kos(frame, players):
    """{fighter: (falls, {entry: KOs it scored on that one}, team)} of the fighters that read (KOS, TEAM)."""
    out = {}
    for i in range(players):
        info, raw, team = (frame.watches.get(f"{i}{w}") for w in ("info", "kos", "team"))
        if info and raw and len(raw) == KOS[0] and team:
            beat = struct.unpack("<24I", raw)
            out[i] = (sum(struct.unpack_from("<3I", info, 0x3c)), {j: beat[j] + beat[8 + j] + beat[16 + j] for j in range(8)},
                      int.from_bytes(team, "little"))
    return out



def damage(frame, players):
    """{fighter: (damage, fighter that dealt its last hit or None)} of the fighters that read it."""
    ids, out = {}, {}
    for i in range(players):
        raw = frame.watches.get(f"{i}id")
        if raw and len(raw) >= 4:
            ids[int.from_bytes(raw[:4], "little")] = i
    for i in range(players):
        hurt, hit = frame.watches.get(f"{i}dmg"), frame.watches.get(f"{i}hitby")
        if hurt and len(hurt) == 4 and hit and len(hit) == HIT_BY[0]:
            obj, owner = struct.unpack("<2I", hit)
            by = ids.get(obj, ids.get(owner))
            here, there = frame.watches.get(f"{i}pos"), by is not None and frame.watches.get(f"{by}pos")
            if obj in ids and here and there and \
                    math.dist(struct.unpack_from("<ff", here), struct.unpack_from("<ff", there)) > REACH:
                by = None
            out[i] = struct.unpack("<f", hurt)[0], by
    return out

def tag_name(bridge, main, record):
    """The name of that name tag record in the save data, "" for none. main is the base of the
    main module ("info")."""
    holder = bridge.read(main + SAVE, 8)
    save = holder and bridge.read(int.from_bytes(holder, "little"), 8)
    data = save and 0 <= record < 64 and bridge.read(int.from_bytes(save, "little") + TAGS + record * TAG_SIZE, 0x18)
    count = int.from_bytes(data[:4], "little") if data else 0
    return data[4:4 + count * 2].decode("utf-16-le", "replace") if 0 < count <= 10 else ""


def rename_tag(bridge, main, tag, new):
    """Names new the name tag tag, or tag followed by a number (an earlier call), in the save data:
    the records it renamed. main is the base of the main module ("info")."""
    holder = bridge.read(main + SAVE, 8)
    save = holder and bridge.read(int.from_bytes(holder, "little"), 8)
    done = []
    for record in range(64) if save else ():
        data = bridge.read(int.from_bytes(save, "little") + TAGS + record * TAG_SIZE, 0x18)
        if not data:
            break
        count = int.from_bytes(data[:4], "little")
        name = data[4:4 + count * 2].decode("utf-16-le", "replace") if 0 < count <= 10 else ""
        after = name[len(tag):]
        if name.startswith(tag) and (not after or after[0] == " " and after[1:].lstrip("-").removesuffix("k").isdigit()):
            name16 = struct.pack("<I", len(new)) + (new[:10] + "\0").encode("utf-16-le")
            bridge.poke("tag", name16, f"main+{SAVE:#x}", 0, TAGS + record * TAG_SIZE)
            bridge.unpoke("tag")
            done.append(record)
    return done


def parse(frame, players):
    fighters, stage = {}, frame.watches.get("stage")
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
        for name, (table, j) in EYES.items():
            size, raw = 8 if table.startswith("wi") else 4, frame.watches.get(f"{i}{name}")
            if raw is None and frame.watches.get(f"{i}{table}"):  # recorded with the whole arrays
                raw = frame.watches[f"{i}{table}"][j * size:(j + 1) * size]
            setattr(f, name, struct.unpack("<q" if size == 8 else "<f", raw)[0] if raw else math.nan)
        f.stage = int.from_bytes(stage, "little") if stage else -1
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
