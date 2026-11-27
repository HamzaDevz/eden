# stats.py <dir> : frame pacing from sf.txt (SurfaceFlinger dumps separated by "@") + poll.csv averages
import csv, os, sys
d = sys.argv[1]
dumps = [[]]
for line in open(os.path.join(d, "sf.txt")):
    if line.startswith("@"): dumps.append([]); continue
    p = line.split()
    if len(p) == 3 and p[1].isdigit() and 0 < int(p[1]) < 9e18: dumps[-1].append(int(p[1]))
pairs = set()
for x in dumps:
    s = sorted(set(x)); pairs.update(zip(s, s[1:]))
ms = sorted((b - a) / 1e6 for a, b in pairs)
n = len(ms); tot = sum(ms) / 1000
out = f"frames={n} seen={tot:.0f}s fps={n / tot:.2f} slow20={100 * sum(m > 20 for m in ms) / n:.2f}% n25={sum(m > 25 for m in ms)} n34={sum(m > 34 for m in ms)} n50={sum(m > 50 for m in ms)} worst={ms[-1]:.0f}ms"
pc = os.path.join(d, "poll.csv")
if os.path.exists(pc):
    r = list(csv.DictReader(open(pc)))
    if r:
        m = lambda k: sum(float(x[k]) for x in r) / len(r)
        out += f" gpu_busy={m('gpu_busy'):.0f}% gpu_mhz={m('gpu_mhz'):.0f} cpu_tmax={max(float(x['cpu_tmax']) for x in r) / 1000:.0f}C gpu_tmax={max(float(x['gpu_tmax']) for x in r) / 1000:.0f}C"
print(out)
