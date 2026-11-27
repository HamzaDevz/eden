# align.py <png_out> [limit_s]: TV screencaps until the replay timer reads 2:18.5-2:29.9, then sleeps until 2:18.00
# and exits 0 (prints the timer seen); exits 1 on timeout. Sleeps through mid-match instead of polling.
import os, subprocess, sys, time
SP = os.path.dirname(os.path.abspath(__file__))
out, limit = sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else 420
adb = os.path.expanduser("~/Library/Android/sdk/platform-tools/adb")
end = time.time() + limit
while time.time() < end:
    t0 = time.time()
    png = subprocess.run([adb, "exec-out", "screencap", "-p", "-d", os.environ["TV"]], capture_output=True).stdout
    open(out, "wb").write(png)
    r = subprocess.run([sys.executable, "-I", os.path.join(SP, "ocr.py"), out], capture_output=True, text=True).stdout.split()
    if not r or not r[0][0].isdigit():
        time.sleep(1); continue
    T = float(r[0])
    if 138.5 < T < 149.9:
        d = T - 138 - (time.time() - t0 - 0.3)
        if d > 0: time.sleep(d)
        print(f"seen {T:.2f}, slept {d:.2f}")
        sys.exit(0)
    time.sleep(20 if 20 < T < 138.5 else 1)
print("timeout"); sys.exit(1)
