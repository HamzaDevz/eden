# SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
# SPDX-License-Identifier: GPL-3.0-or-later

# glitch.py <outdir>: looks for rendering glitches in the stream play.py saved (stream.h264, 480x270, every frame).
# A glitch is a patch of the picture that changes for one or two frames and then comes back as it was: flicker,
# geometry missing or spiking for a frame. Motion does not come back, so it is not counted; the camera pan is
# measured and compensated first, or a striped texture (stairs) that scrolls by one period looks like it came back.
# Writes glitches.txt (events per segment), glitches/<segment>_<s>.png for the worst events (before, glitch,
# after, the changed blocks outlined) and sheet.png, one frame every 10 s, to look at the run as a whole.
import collections, os, shutil, subprocess, sys
import numpy as np
from PIL import Image, ImageDraw

out = sys.argv[1]
B, T, K = 8, 28, 0.35  # block size on the half-size picture, block change that counts, how close "back" must be
HANN = np.outer(np.hanning(95), np.hanning(200))
times = [float(x) for x in open(os.path.join(out, "stream.txt"))]
marks = [(float(p[0]), p[2]) for p in (l.split() for l in open(os.path.join(out, "play.log")))
         if len(p) == 3 and p[1] == "mark"]


def segment(t):
    return next((name for t0, name in reversed(marks) if t >= t0), "-")


def blocks(a, b, valid):
    d = np.abs(a - b).mean(axis=2)
    d[~valid] = np.nan  # pixels a warp wrapped around: a block holding any is never counted
    h, w = d.shape[0] // B * B, d.shape[1] // B * B
    return np.nan_to_num(d[:h, :w].reshape(h // B, B, w // B, B).mean(axis=(1, 3)))


def pan(a, b):
    """Camera shift (dy, dx) such that b is a rolled by it, by phase correlation away from the HUD."""
    a, b = (x[20:115, 20:220].mean(axis=2) * HANN for x in (a, b))
    r = np.fft.rfft2(b) * np.conj(np.fft.rfft2(a))
    r = np.fft.irfft2(r / (np.abs(r) + 1e-9), s=a.shape)
    dy, dx = np.unravel_index(np.argmax(r), r.shape)
    return (dy - a.shape[0] if dy > a.shape[0] // 2 else dy, dx - a.shape[1] if dx > a.shape[1] // 2 else dx)


def warp(img, s, valid):
    """img moved by s; valid loses the rows and columns that wrapped around."""
    dy, dx = s
    v = np.roll(valid, (dy, dx), axis=(0, 1))
    if dy > 0:
        v[:dy] = False
    elif dy < 0:
        v[dy:] = False
    if dx > 0:
        v[:, :dx] = False
    elif dx < 0:
        v[:, dx:] = False
    return np.roll(img, (dy, dx), axis=(0, 1)), v


# Same decoder flags as play.py, so frame i here is frame i there.
dec = subprocess.Popen(["ffmpeg", "-v", "error", "-probesize", "32", "-analyzeduration", "0", "-flags", "low_delay",
                        "-f", "h264", "-i", os.path.join(out, "stream.h264"), "-fps_mode", "passthrough",
                        "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
size = 480 * 270 * 3
win = collections.deque(maxlen=4)  # (index, full frame, half-size frame, camera pan since the previous frame)
ALL = np.ones((135, 240), bool)
events, sheet, last_sheet = [], [], -10.0
i = 0
while len(buf := dec.stdout.read(size)) == size:
    full = np.frombuffer(buf, np.uint8).reshape(270, 480, 3)
    half = full.reshape(135, 2, 240, 2, 3).mean(axis=(1, 3))
    t = times[i] if i < len(times) else times[-1]
    if t - last_sheet >= 10 and t >= 0:
        sheet.append((t, full))
        last_sheet = t
    win.append((i, full, half, pan(win[-1][2], half) if win else (0, 0)))
    if len(win) == 4:
        (_, f0, h0, _), (i1, f1, h1, s1), (_, f2, h2, s2), (_, f3, h3, s3) = win
        # Everything is compared where f1 sees it. One frame out (f1 between f0 and f2), or two (f1 and f2
        # between f0 and f3).
        w0, v0 = warp(h0, s1, ALL)
        w2, v2 = warp(h2, (-s2[0], -s2[1]), ALL)
        w3, v3 = warp(h3, (-s2[0] - s3[0], -s2[1] - s3[1]), ALL)
        for a, b, c, after in ((blocks(w0, h1, v0), blocks(h1, w2, v2), blocks(w0, w2, v0 & v2), f2),
                               (blocks(w0, h1, v0), blocks(w2, w3, v2 & v3), blocks(w0, w3, v0 & v3), f3)):
            m = (np.minimum(a, b) > T) & (c < K * np.minimum(a, b))
            if m.sum() >= 2:
                events.append((int(m.sum()), times[i1] if i1 < len(times) else 0, f0, f1, after, m))
                break
    i += 1
dec.wait()

# Consecutive detections of one glitch count once, with its worst frame.
merged = []
for e in events:
    if merged and e[1] - merged[-1][1] < 0.2:
        merged[-1] = max(merged[-1], e, key=lambda x: x[0])
    else:
        merged.append(e)
per = collections.Counter(segment(e[1]) for e in merged)
with open(os.path.join(out, "glitches.txt"), "w") as f:
    f.write(f"glitches={len(merged)} frames={i} times={len(times)} " +
            " ".join(f"{s}:{n}" for s, n in per.items()) + "\n")
print(open(os.path.join(out, "glitches.txt")).read().strip())

shutil.rmtree(os.path.join(out, "glitches"), ignore_errors=True)  # captures of an earlier pass would mislead
os.makedirs(os.path.join(out, "glitches"))
for n, t, before, mid, after, m in sorted(merged, key=lambda e: -e[0])[:24]:
    im = Image.fromarray(np.concatenate([before, mid, after], axis=1))
    d = ImageDraw.Draw(im)
    for y, x in zip(*np.nonzero(m)):
        d.rectangle([480 + x * B * 2, y * B * 2, 480 + (x + 1) * B * 2 - 1, (y + 1) * B * 2 - 1], outline=(255, 0, 255))
    im.save(os.path.join(out, "glitches", f"{segment(t)}_{t:05.1f}.png"))

if sheet:
    W, H, cols = 240, 135, 6
    s = Image.new("RGB", (W * cols, H * ((len(sheet) + cols - 1) // cols)))
    for k, (t, f) in enumerate(sheet):
        im = Image.fromarray(f).resize((W, H))
        ImageDraw.Draw(im).text((3, H - 12), f"{t:.0f}s {segment(t)}", fill=(255, 0, 255))
        s.paste(im, ((k % cols) * W, (k // cols) * H))
    s.save(os.path.join(out, "sheet.png"))
