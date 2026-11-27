# ocr.py <png>: the Smash match timer (top right) in seconds, or nothing
import re, subprocess, sys
from PIL import Image
im = Image.open(sys.argv[1]).convert("L")
w, h = im.size
c = im.crop((int(w * 0.855), int(h * 0.025), int(w * 0.995), int(h * 0.1)))
c = c.resize((c.width * 2, c.height * 2)).point(lambda v: 0 if v > 225 else 255)
c.save(sys.argv[1] + ".t.png")
out = subprocess.run(["tesseract", sys.argv[1] + ".t.png", "-", "--psm", "7", "-c",
                      "tessedit_char_whitelist=0123456789:,."], capture_output=True, text=True).stdout.strip()
m = re.match(r"(\d):(\d\d)[,.]?(\d\d)", out)
print(f"{int(m[1]) * 60 + int(m[2]) + int(m[3]) / 100:.2f}" if m else "", out, file=sys.stdout)
