# SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
# SPDX-License-Identifier: GPL-3.0-or-later

# Client of Eden's game bridge ([Debugging] use_game_bridge=true, port 6560; protocol in
# src/core/tools/game_bridge.cpp). Reach it with `adb forward tcp:6560 tcp:6560` or the console IP.
#   bridge.py [--host H] info | sym <name> | read <addr> <len>
#   bridge.py record <out.txt> [--watch id:len:base[:off...]]...   frames until Ctrl-C
#   bridge.py pad <player> <buttons hex> <lx> <ly> <rx> <ry> [hold_ms]
#   bridge.py mirror <from> <to>      copies one player's inputs onto another, every frame
# Players are npad indices: 0-7 for players 1-8, 8 for handheld.
import argparse, socket, sys, time

BUTTONS = ["a", "b", "x", "y", "lstick", "rstick", "l", "r", "zl", "zr", "plus", "minus",
           "left", "up", "right", "down"]


class Frame:
    def __init__(self, line):
        t = line.split()
        self.index, self.time_us = int(t[1]), int(t[2])
        self.pads, self.watches = {}, {}
        i = 3
        while i < len(t):
            if t[i] == "p":
                self.pads[int(t[i + 1])] = (int(t[i + 2], 16), *map(int, t[i + 3:i + 7]))
                i += 7
            else:
                self.watches[t[i + 1]] = None if t[i + 2] == "-" else bytes.fromhex(t[i + 2])
                i += 3


class Bridge:
    def __init__(self, host="127.0.0.1", port=6560):
        self.sock = socket.create_connection((host, port), timeout=10)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.file = self.sock.makefile("r")

    def send(self, line):
        self.sock.sendall((line + "\n").encode())

    def lines(self):
        for line in self.file:
            yield line.rstrip("\n")

    def ask(self, line, reply):
        # Frames keep streaming, so skip them until the reply arrives.
        self.send(line)
        for got in self.lines():
            if got.startswith(reply + " ") or got.startswith("err "):
                return got
        raise EOFError

    def frames(self, raw=False):
        for line in self.lines():
            if line.startswith("f "):
                yield line if raw else Frame(line)

    def watch(self, spec):
        wid, length, base, *offs = spec.split(":")
        return self.ask(f"watch {wid} {length} {base} {' '.join(offs)}".strip(), "ok")

    def pad(self, player, buttons=0, lx=0, ly=0, rx=0, ry=0):
        self.send(f"pad {player} {buttons:#x} {lx} {ly} {rx} {ry}")

    def release(self, player):
        self.send(f"release {player}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=6560)
    ap.add_argument("op")
    ap.add_argument("args", nargs="*")
    ap.add_argument("--watch", action="append", default=[])
    a = ap.parse_args()
    b = Bridge(a.host, a.port)

    if a.op == "info":
        print(b.ask("info", "info"))
    elif a.op == "sym":
        print(b.ask(f"sym {a.args[0]}", "sym"))
    elif a.op == "read":
        print(b.ask(f"read {a.args[0]} {a.args[1]}", "mem"))
    elif a.op == "record":
        for spec in a.watch:
            print(b.watch(spec))
        n, start, last_index, gaps = 0, time.time(), None, 0
        with open(a.args[0], "a") as out:
            try:
                for line in b.frames(raw=True):
                    out.write(f"{time.time():.4f} {line}\n")
                    index = int(line.split()[1])
                    gaps += last_index is not None and index != last_index + 1
                    last_index, n = index, n + 1
                    if n % 300 == 0:
                        print(f"{n} frames, {n / (time.time() - start):.1f}/s, {gaps} gaps",
                              file=sys.stderr)
            except KeyboardInterrupt:
                pass
        print(f"{n} frames written to {a.args[0]}", file=sys.stderr)
    elif a.op == "pad":
        player, buttons, lx, ly, rx, ry = a.args[:6]
        b.pad(int(player), int(buttons, 16), int(lx), int(ly), int(rx), int(ry))
        time.sleep(int(a.args[6]) / 1000 if len(a.args) > 6 else 0.1)
        b.release(int(player))
        time.sleep(0.1)
    elif a.op == "mirror":
        src, dst = int(a.args[0]), int(a.args[1])
        try:
            for f in b.frames():
                if src in f.pads:
                    b.pad(dst, *f.pads[src])
        except KeyboardInterrupt:
            b.release(dst)


if __name__ == "__main__":
    main()
