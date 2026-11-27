# wonder_where.py PNG : prints which bench screen of Super Mario Bros. Wonder the shot is nearest to (map, confirm,
# level, from the wonder_ref_*.png frames of the bench save) and its distance, by mean grey difference at 160x90.
import glob, os, sys
from PIL import Image
D = os.path.dirname(os.path.abspath(__file__))
g = lambda p: list(Image.open(p).convert("L").resize((160, 90)).get_flattened_data())
x = g(sys.argv[1])
d = {os.path.basename(p)[11:-4]: sum(abs(a - b) for a, b in zip(x, g(p))) / len(x)
     for p in glob.glob(os.path.join(D, "wonder_ref_*.png"))}
k = min(d, key=d.get)
print(k, round(d[k], 1))
