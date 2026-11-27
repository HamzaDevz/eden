# jb_scr.py <png> : which Jamboree route screen this frame is (map, reprendre, title) or "other"
import os, sys
from PIL import Image
import numpy as np
D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jb_ref")
f = lambda p: np.asarray(Image.open(p).convert("RGB").resize((64, 36)), np.float32)
x = f(sys.argv[1])
d = {n: np.abs(f(os.path.join(D, n + ".png")) - x).mean() for n in ("map", "reprendre", "title")}
n = min(d, key=d.get)
print(n if d[n] < 18 else "other")
