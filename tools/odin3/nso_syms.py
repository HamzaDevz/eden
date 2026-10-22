#!/usr/bin/env python3
"""Dump the dynamic symbols of the running game's main module through the game bridge.

  nso_syms.py out.json   -> {"base", "build", "syms": {name: [main offset, size]}}
"""
import json
import struct
import sys

from bridge import Bridge


def read(b, addr, n):
    out = b""
    while n:
        k = min(n, 0x10000)
        r = b.ask(f"read {addr:#x} {k:#x}", "mem").split()
        if r[0] != "mem" or r[2] == "-":
            raise RuntimeError(f"read {addr:#x}: {r}")
        out += bytes.fromhex(r[2])
        addr += k
        n -= k
    return out


def main():
    b = Bridge()
    b.ask("stream 0", "ok")
    info = b.ask("info", "info").split()
    base = int(info[3].split("=")[1], 16)
    mod0 = base + struct.unpack("<I", read(b, base + 4, 4))[0]
    magic, dyn_off = struct.unpack("<4si", read(b, mod0, 8))
    assert magic == b"MOD0", magic
    tags = {}
    raw = read(b, mod0 + dyn_off, 0x2000)
    for i in range(0, len(raw), 16):
        tag, val = struct.unpack("<QQ", raw[i:i + 16])
        if tag == 0:
            break
        tags.setdefault(tag, val)
    symtab, strtab, strsz = tags[6], tags[5], tags[10]  # DT_SYMTAB, DT_STRTAB, DT_STRSZ
    nsyms = (strtab - symtab) // 24
    st = read(b, base + symtab, nsyms * 24)
    strs = read(b, base + strtab, strsz)
    syms = {}
    for i in range(nsyms):
        name_off, _, _, _, value, size = struct.unpack("<IBBHQQ", st[i * 24:(i + 1) * 24])
        if value:
            name = strs[name_off:strs.index(b"\0", name_off)].decode(errors="replace")
            syms[name] = [value, size]
    json.dump({"base": base, "build": info[2], "syms": syms}, open(sys.argv[1], "w"))
    print(f"{len(syms)} symbols, main {base:#x}", file=sys.stderr)
    b.ask("stream 1", "ok")


if __name__ == "__main__":
    main()
