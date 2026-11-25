# infield.py PNG [v] : exit 0 when the field control hint (« L Se déplacer / A Sauter ») is on screen, i.e. Mario
# can walk (not in battle, cutscene, title or a white fade). Template hud_field.png = that hint cropped from a
# 540x303 frame; its white glyphs must be bright and their dark outlines dark in the screenshot.
import os, sys
from PIL import Image, ImageFilter
BOX = (220, 225, 330, 278)
T = Image.open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "hud_field.png")).convert("L")
near = T.point(lambda v: 255 if v > 215 else 0).filter(ImageFilter.MaxFilter(5))
t, n = list(T.get_flattened_data()), list(near.get_flattened_data())
x = list(Image.open(sys.argv[1]).convert("L").resize((540, 303)).crop(BOX).get_flattened_data())
bright = [xi for ti, xi in zip(t, x) if ti > 215]
outline = [xi for ti, ni, xi in zip(t, n, x) if ti < 90 and ni]
b = sum(v > 190 for v in bright) / len(bright)
o = sum(v < 130 for v in outline) / len(outline)
if len(sys.argv) > 2:
    print(round(b, 2), round(o, 2), len(outline))
sys.exit(0 if b > 0.8 and o > 0.7 else 1)
