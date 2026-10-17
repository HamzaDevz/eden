# SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
# SPDX-License-Identifier: GPL-3.0-or-later

# gdbmem.py info | dump <start hex> <end hex> <out> | read <addr hex> <len hex> | cont
# Reads guest memory through Eden's GDB stub ([Debugging] use_gdbstub=true, then adb forward tcp:6543 tcp:6543).
# The game stays suspended until `cont`: a dump taken before it is the unrelocated image, which is what a cheat
# port needs (dump_nso writes files that adb cannot read).
import socket, sys, time

s = socket.create_connection(("127.0.0.1", 6543), timeout=60)
buf = b""


def send(p):
    s.sendall(b"$" + p.encode() + b"#" + b"%02x" % (sum(p.encode()) & 0xFF))


def recv():
    global buf
    while True:
        i = buf.find(b"$")
        j = buf.find(b"#", i + 1) if i >= 0 else -1
        if i >= 0 and j >= 0 and len(buf) >= j + 3:
            pkt = buf[i + 1:j]
            buf = buf[j + 3:]
            s.sendall(b"+")
            return pkt.decode()
        d = s.recv(1 << 20)
        if not d:
            raise EOFError
        buf += d


def cmd(p):
    send(p)
    return recv()


cmd("qSupported:multiprocess+")
op = sys.argv[1]
if op == "info":
    # Module list with load addresses.
    send("qRcmd," + "info".encode().hex())
    r = recv()
    while r.startswith("O") and r != "OK":
        print(bytes.fromhex(r[1:]).decode(), end="")
        r = recv()
    if r != "OK" and not r.startswith("E"):
        try:
            print(bytes.fromhex(r).decode())
        except ValueError:
            print(r)
elif op == "dump":
    a, e = int(sys.argv[2], 16), int(sys.argv[3], 16)
    t = time.time()
    with open(sys.argv[4], "wb") as f:
        while a < e:
            k = min(0x8000, e - a)
            r = cmd("m%x,%x" % (a, k))
            f.write(b"\0" * k if r.startswith("E") else bytes.fromhex(r))
            a += k
    print("done %.1fs" % (time.time() - t))
elif op == "read":
    print(cmd("m%s,%s" % (sys.argv[2], sys.argv[3])))
elif op == "cont":
    send("c")
