# SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
# SPDX-License-Identifier: GPL-3.0-or-later

# Virtual amiibo for the AIs: a plain (unencrypted) NTAG215 in the layout Eden loads without keys
# (NTAG215File, src/core/hle/service/nfp/nfp_types.h), registered with the AI's name as nickname
# and owner Mii, so a game takes it as a figure of that name without any setup.
#   amiibo.py <name> <out.bin> [amiibo id hex, Mario by default]
import datetime, hashlib, struct, sys

MARIO = 0x0000000000000002  # Super Smash Bros. series Mario


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


if __name__ == "__main__":
    with open(sys.argv[2], "wb") as f:
        f.write(build(sys.argv[1], int(sys.argv[3], 16) if len(sys.argv) > 3 else MARIO))
