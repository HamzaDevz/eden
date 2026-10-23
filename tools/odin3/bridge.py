# SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
# SPDX-License-Identifier: GPL-3.0-or-later

# Client of Eden's game bridge ([Debugging] use_game_bridge=true, port 6560; protocol in
# src/core/tools/game_bridge.cpp). Reach it with `adb forward tcp:6560 tcp:6560` or the console IP.
#   bridge.py [--host H] info | sym <name> | read <addr> <len>
#   bridge.py record <out.txt> [--watch id:len:base[:off...]]...   frames until Ctrl-C
#   bridge.py pad <player> <buttons hex> <lx> <ly> <rx> <ry> [hold_ms]
#   bridge.py mirror <from> <to>      copies one player's inputs onto another, every frame
#   bridge.py shot <out.png> [height]   the next presented frame (Vulkan or OpenGL renderer)
# Players are npad indices: 0-7 for players 1-8, 8 for handheld.
import argparse, socket, struct, sys, time, zlib

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
        self.synced = False

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
            if self.synced and got.startswith("f "):
                self.tick(got)
        raise EOFError

    def frames(self, raw=False):
        for line in self.lines():
            if line.startswith("f "):
                yield line if raw else Frame(line)
                if self.synced:  # the caller is done with this frame
                    self.tick(line)

    def sync(self, slack):
        """Lockstep: the game waits for each frame to be handled, at most slack frames late."""
        self.synced = slack > 0
        return self.ask(f"sync {slack}", "ok")

    def tick(self, line):
        self.send("tick " + line.split(" ", 2)[1])

    def watch(self, spec):
        wid, length, base, *offs = spec.split(":")
        return self.ask(f"watch {wid} {length} {base} {' '.join(offs)}".strip(), "ok")

    def pad(self, player, buttons=0, lx=0, ly=0, rx=0, ry=0):
        self.send(f"pad {player} {buttons:#x} {lx} {ly} {rx} {ry}")

    def release(self, player):
        self.send(f"release {player}")

    def shot(self, height=360):
        got = self.ask(f"shot {height}", "shot")
        if got.startswith("err "):
            raise RuntimeError(got)
        _, w, h, data = got.split()
        return int(w), int(h), bytes.fromhex(data)


def write_png(path, width, height, bgra):
    row = width * 4
    raw = b"".join(b"\0" + bytes(bgra[y * row + x + c] for x in range(0, row, 4) for c in (2, 1, 0))
                   for y in range(height))
    chunk = lambda kind, body: (struct.pack(">I", len(body)) + kind + body
                                + struct.pack(">I", zlib.crc32(kind + body)))
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


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
    elif a.op == "shot":
        write_png(a.args[0], *b.shot(int(a.args[1]) if len(a.args) > 1 else 360))
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
