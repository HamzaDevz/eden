# SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
# SPDX-License-Identifier: GPL-3.0-or-later

# Virtual amiibo for the AIs: a plain (unencrypted) NTAG215 in the layout Eden loads without keys
# (NTAG215File, src/core/hle/service/nfp/nfp_types.h), registered with the AI's name as nickname
# and owner Mii, so a game takes it as a figure of that name without any setup.
#   amiibo.py <name> <out.bin> [amiibo id hex, Mario by default] [a FIG Smash registered: its game data, at level 50]
#   amiibo.py strength <strength.json> <bin>...   sets each AI's FIG to its level (with_level()), prints
#                                                 the files it rewrote
import datetime, hashlib, json, os, struct, sys, zlib

MARIO = 0x0000000000000002  # Super Smash Bros. series Mario

# Smash Ultimate's game data, in the application area once it registered the figure: its CRC32
# (zlib's, from 0 instead of ~0) first, then at XP the experience Smash shows as the FIG's level
# and as a CPU, both u16 LE, with its thresholds (TagMo's AppDataSSBU).
SMASH, AREA, XP = 0x34f80200, 0xdc, 0x70
LEVEL_XP = (0x000, 0x008, 0x016, 0x029, 0x03f, 0x05a, 0x078, 0x09b, 0x0c3, 0x0ee,
            0x11c, 0x14a, 0x178, 0x1aa, 0x1dc, 0x210, 0x244, 0x278, 0x2ac, 0x2e1,
            0x316, 0x34b, 0x380, 0x3b6, 0x3ec, 0x422, 0x458, 0x48f, 0x4c6, 0x4fd,
            0x53b, 0x57e, 0x5c6, 0x613, 0x665, 0x6bc, 0x718, 0x776, 0x7dc, 0x843,
            0x8ac, 0x919, 0x99b, 0xa3b, 0xaef, 0xbb7, 0xc89, 0xd65, 0xe55, 0xf48)
CPU_XP = (0x000, 0x03f, 0x0d2, 0x1b2, 0x2ed, 0x475, 0x643, 0x811, 0xacd)  # CPU levels 1 to 9


def build(name, amiibo_id=MARIO):
    seed = hashlib.sha256(name.encode()).digest()
    part1, part2, nintendo_id = bytes([0x04]) + seed[:2], seed[2:5], seed[5]
    check1 = 0x88 ^ part1[0] ^ part1[1] ^ part1[2]
    check2 = part2[0] ^ part2[1] ^ part2[2] ^ nintendo_id
    today = datetime.date.today()
    date = struct.pack(">H", ((today.year - 2000) << 9) | (today.month << 5) | today.day)
    name16 = name[:10].ljust(10, "\0")

    mii = bytearray(0x60)
    mii[0] = 3  # version
    mii[0x1a:0x2e] = name16.encode("utf-16-le")
    mii[0x2e] = mii[0x2f] = 64  # height, build

    d = bytearray(0x21c)
    d[0], d[1] = check2, 0x48
    struct.pack_into("<HI", d, 2, 0xe00f, 0xeeff10f1)  # static lock, capability container
    d[0x28] = 0xa5
    d[0x2c] = 0x10  # amiibo_initialized: registered, no game data yet
    d[0x30:0x34] = date + date
    d[0x38:0x4c] = name16.encode("utf-16-be")
    d[0x4c:0xac] = mii
    d[0x1d4:0x1dc] = part1 + bytes([check1]) + part2 + bytes([nintendo_id])
    d[0x1dc:0x1e4] = amiibo_id.to_bytes(8, "big")
    struct.pack_into("<III", d, 0x208, 0xbd0f0001, 0x04000000, 0x5f)  # dynamic lock, CFG0, CFG1
    return bytes(d)


def from_fig(name, amiibo_id, fig):
    """name's figure holding the game data of fig, one Smash registered (Extras › amiibo): a FIG
    without going through that menu, for another fighter too (3/11)."""
    d = bytearray(build(name, amiibo_id))
    d[0x2a], d[0x2c] = fig[0x2a], fig[0x2c]  # write counter, flags: game data in
    d[0xac:0x1b8] = fig[0xac:0x1b8]  # Smash's title and application ids, then its area
    return bytes(d)


def xp_level(xp):
    return sum(xp >= t for t in LEVEL_XP)


def level(data):
    """The level an AI's FIG shows after its name (shown_name()), else Smash's on its card."""
    nick = data[0x38:0x4c].decode("utf-16-be").split("\0")[0].rsplit(" ", 1)
    return int(nick[1]) if len(nick) == 2 and nick[1].isdigit() else xp_level(struct.unpack_from("<H", data, AREA + XP)[0])


def shown_name(name, n):
    """The nickname of an AI's FIG at level n: its name followed by n (Hamza, 1/11), which goes on
    where Smash's card stops at 50."""
    return f"{name[:9 - len(str(n))]} {n}"


def named(nick, names):
    """The one of names whose FIG shows nick (shown_name()), else nick."""
    base = nick.rsplit(" ", 1)[0] if nick.rsplit(" ", 1)[-1].isdigit() else None
    return next((n for n in names if n[:10] == nick or base and n.startswith(base)), nick)


def with_level(data, name, lv):
    """data of name's FIG at level lv (smash_rl.fit_levels(), open above 50). Unchanged when Smash
    has not written its game data there yet."""
    if struct.unpack_from(">I", data, 0xb6)[0] != SMASH:
        return data
    n = max(int(lv), 1)  # as Smash's card shows its experience
    i = min(max(int(lv), 1), len(LEVEL_XP)) - 1
    xp = round(LEVEL_XP[i] + (LEVEL_XP[min(i + 1, len(LEVEL_XP) - 1)] - LEVEL_XP[i]) * min(max(lv - 1 - i, 0), 1))
    d = bytearray(data)
    struct.pack_into("<HH", d, AREA + XP, xp, min(xp, CPU_XP[-1]))
    struct.pack_into("<I", d, AREA, zlib.crc32(d[AREA + 4:AREA + 0xd8], 0xffffffff))
    d[0x38:0x4c] = name[:10].ljust(10, "\0").encode("utf-16-be")  # the bare name (Hamza, 1/11)
    # Smash keeps a figure's data and nickname for the session by its UID, which Eden takes from a
    # plain file's first 7 bytes: the internal number (never checked) bumped makes each change another figure.
    if d != data:
        d[1] = (d[1] + 1) % 256
    return bytes(d)


def set_level(path, lv):
    """with_level() on the file at path, named after it: its levels before and after when that
    rewrote it, else None."""
    with open(path, "rb") as f:
        data = f.read()
    new = with_level(data, os.path.splitext(os.path.basename(path))[0], lv)
    if new == data:
        return None
    with open(path + ".tmp", "wb") as f:
        f.write(new)
    os.replace(path + ".tmp", path)
    return level(data), level(new)


if __name__ == "__main__":
    if sys.argv[1] == "strength":
        with open(sys.argv[2]) as f:
            levels = json.load(f)
        for path in sys.argv[3:]:
            name = os.path.splitext(os.path.basename(path))[0]
            if name in levels and set_level(path, levels[name]):
                print(path)
    else:
        amiibo_id = int(sys.argv[3], 16) if len(sys.argv) > 3 else MARIO
        if len(sys.argv) > 4:
            with open(sys.argv[4], "rb") as f:
                data = with_level(from_fig(sys.argv[1], amiibo_id, f.read()), sys.argv[1], 50)
        else:
            data = build(sys.argv[1], amiibo_id)
        with open(sys.argv[2], "wb") as f:
            f.write(data)
