# decide.py <root> <candidate prefix> <base prefix>: prints "cand" when every candidate session matches the base
# (per match type m1 = 1v1, m2 = 8p: fps >= base - 0.05, slow20 <= base + 0.15 pt, cpu_tmax <= 100, black <= base + 10,
# no fatal), else "base"; reasons on stderr.
import glob, os, re, statistics, sys
root, cand, base = sys.argv[1:4]
def runs(prefix, m):
    out = []
    for d in sorted(p for p in glob.glob(os.path.join(root, prefix + "[0-9]*")) if os.path.isdir(p)):
        s = os.path.join(d, m, "stats.txt"); a = os.path.join(d, m, "analyze.txt")
        if not os.path.exists(s): out.append(None); continue
        t = open(s).read() + " " + (open(a).read() if os.path.exists(a) else "fatal=1")
        g = lambda k: float(re.search(k + r"=([0-9.]+)", t).group(1))
        out.append(dict(fps=g("fps"), slow=g("slow20"), cpu=g("cpu_tmax"), black=g("black"), fatal=g("fatal")))
    return out
ok = True
for m in ("m1", "m2"):
    C, B = runs(cand, m), runs(base, m)
    if not C or not B or None in C or None in B:
        print(f"{m}: missing runs", file=sys.stderr); ok = False; continue
    mean = lambda L, k: statistics.mean(x[k] for x in L)
    print(f"{m}: cand fps={mean(C,'fps'):.2f} slow={mean(C,'slow'):.2f} cpu={max(x['cpu'] for x in C):.0f} black={mean(C,'black'):.0f}"
          f" | base fps={mean(B,'fps'):.2f} slow={mean(B,'slow'):.2f} cpu={max(x['cpu'] for x in B):.0f} black={mean(B,'black'):.0f}", file=sys.stderr)
    ok &= (mean(C, "fps") >= mean(B, "fps") - 0.05 and mean(C, "slow") <= mean(B, "slow") + 0.15
           and max(x["cpu"] for x in C) <= 100 and mean(C, "black") <= mean(B, "black") + 10
           and not any(x["fatal"] for x in C))
print("cand" if ok else "base")
