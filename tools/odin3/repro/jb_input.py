# jb_input.py <secs> : Jamboree autoplay through the game bridge, A every 0.8 s and + every 4th press (board turns,
# minigame explanations started with +).
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bridge
b = bridge.Bridge(); end = time.time() + float(sys.argv[1]); j = 0
while time.time() < end:
    j += 1
    try:
        b.pad(0, 0x400 if j % 4 == 0 else 0x1); time.sleep(0.12); b.pad(0); time.sleep(0.68)
    except OSError:
        time.sleep(0.3); b = bridge.Bridge()
b.pad(0); b.release(0)
