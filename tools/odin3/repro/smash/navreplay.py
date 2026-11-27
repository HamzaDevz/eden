# navreplay.py <outdir> [limit_s]: from a cold Smash boot to the first replay of Vault > Videos playing, by matching
# TV screencaps against refs/*.png; one bridge connection (tcp 6560). Exits 0 once "play" was pressed.
import glob, os, socket, subprocess, sys, time
import numpy as np
from PIL import Image
SP = os.path.dirname(os.path.abspath(__file__))
out, limit = sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else 600
os.makedirs(out, exist_ok=True)
adb = os.path.expanduser("~/Library/Android/sdk/platform-tools/adb")
small = lambda im: np.asarray(im.convert("L").resize((64, 36), Image.BILINEAR), dtype=np.float32)
R = {os.path.basename(p)[:-4]: small(Image.open(p)) for p in glob.glob(os.path.join(SP, "refs", "*.png"))}
B = {"a": 1, "b": 2, "plus": 0x400, "right": 0x4000}
sock = None
def press(*names, hold=0.12, wait=0.5):
    global sock
    for n in names:
        try:
            if sock is None: sock = socket.create_connection(("127.0.0.1", 6560), timeout=5)
            sock.sendall(f"pad 0 {B[n]:#x} 0 0 0 0\n".encode()); time.sleep(hold)
            sock.sendall(b"pad 0 0x0 0 0 0 0\n")
        except OSError:
            sock = None
        time.sleep(wait)
log = open(os.path.join(out, "nav.log"), "w")
t0 = time.time(); title_seen = False; unknown_since = None; n = 0; last = ""
while time.time() - t0 < limit:
    png = subprocess.run([adb, "exec-out", "screencap", "-p", "-d", os.environ["TV"]], capture_output=True).stdout
    try:
        im = Image.open(__import__("io").BytesIO(png)); s = small(im)
    except Exception:
        time.sleep(2); continue
    d = {k: float(np.abs(s - v).mean()) for k, v in R.items()}
    st = min(d, key=d.get); st = st if d[st] < 25 else "unknown"
    el = time.time() - t0
    print(f"{el:6.1f} {st} {d.get(st, min(d.values())):.0f}", file=log, flush=True)
    n += 1
    if n % 5 == 0 or st != "unknown": im.convert("RGB").resize((480, 270)).save(os.path.join(out, f"n{int(el):03d}-{st}.jpg"), quality=50)
    if st != "unknown": unknown_since = None; last = st
    if st == "unknown" and last.startswith("tuto"): press("a", wait=1.5)  # tutorial pages 2-4 of Videos
    elif st == "title": title_seen = True; press("a", wait=2.5)
    elif st == "menu_smash": title_seen = True; press("right", wait=1.2)
    elif st == "menu_coffre": press("a", wait=2.5)
    elif st == "coffre": press("right", wait=1.0); press("a", wait=2.5)
    elif st in ("vmenu", "tuto1", "tuto5", "list"): press("a", wait=2.5)
    elif st == "play":
        press("a", wait=0.5); print(f"{el:6.1f} PLAY", file=log, flush=True)
        if sock: sock.sendall(b"release 0\n"); time.sleep(0.05); sock.close()
        print(f"play pressed at {el:.0f}s"); sys.exit(0)
    elif st == "howto": press("b", wait=2.5)
    else:
        unknown_since = unknown_since or time.time()
        if not title_seen: press("a", wait=2.5)
        elif time.time() - unknown_since > 6: press("b", wait=2.5); unknown_since = None
        else: time.sleep(1.5)
print("timeout"); sys.exit(1)
