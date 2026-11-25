# infield.py PNG : exit 0 when the field control hint (L/A buttons) is visible, i.e. not in battle
import sys
from PIL import Image
im = Image.open(sys.argv[1]).convert("L").crop((225, 228, 245, 275))
sys.exit(0 if sum(p > 230 for p in im.get_flattened_data()) > 8 else 1)
