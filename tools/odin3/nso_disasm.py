#!/usr/bin/env python3
"""Disassemble live game code through the game bridge (needs capstone).

  nso_disasm.py syms.json <symbol substring | 0x main offset>...
"""
import json
import re
import sys

from capstone import CS_ARCH_ARM64, CS_MODE_ARM, Cs

from bridge import Bridge


def main():
    d = json.load(open(sys.argv[1]))
    base, syms = d["base"], d["syms"]
    b = Bridge()
    b.ask("stream 0", "ok")
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    for key in sys.argv[2:]:
        if key.startswith("0x"):
            name, addr = key, base + int(key, 16)
        else:
            hits = [n for n in syms if key in n]
            exact = [n for n in hits if re.search(rf"\d{re.escape(key)}_implE", n) or n.endswith(key)]
            name = (exact or hits)[0]
            addr = base + syms[name][0]
        code = bytes.fromhex(b.ask(f"read {addr:#x} 0x80", "mem").split()[2])
        print(f"== {name} @ main+{addr - base:#x}")
        for ins in md.disasm(code, addr):
            print(f"  {ins.address - base:#9x}  {ins.mnemonic:6} {ins.op_str}")
            if ins.mnemonic in ("ret", "b", "br"):
                break
    b.ask("stream 1", "ok")


if __name__ == "__main__":
    main()
