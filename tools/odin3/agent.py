# SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
# SPDX-License-Identifier: GPL-3.0-or-later

# agent.py <game> <outdir> [--port 6560] [--hours 2] ...: plays a game with no script, through the game bridge
# (bridge.py), to see how far it gets on a build: a vision model looks at each frame and answers with a few
# button/stick actions. The game is paused while the model thinks (bridge step), so a slow model only makes
# the run longer, never the game harder. Same bridge on the PC's eden-cli (odin3-lab run-agent.sh) and on the Odin.
# Writes <outdir>/agent.log (one JSON line per decision), frames/<step>.jpg (480x270), milestones/<step>.jpg
# (960x540) and summary.txt; --checkpoint CMD runs CMD <outdir>/saves/<step> at each milestone (copy of the saves).
# A screen that stops changing makes the model change its approach, then a burst of random input; a step the
# game never finishes (no frame for --hang seconds) ends the run as a hang. --model random plays random input
# (no model): checks the bench, and a crash or hang test on its own.
import argparse, base64, io, json, os, random, subprocess, time, urllib.request
import numpy as np
from PIL import Image
from bridge import BUTTONS, Bridge

DIRS = {"n": 0, "ne": 45, "e": 90, "se": 135, "s": 180, "sw": 225, "w": 270, "nw": 315}
GAP = 6  # released frames after each action, so two presses of a button are two presses

SYSTEM = """You are playing {game} on a Nintendo Switch, alone, to get as far as possible in the main story.
Goal: {goal}
Each turn you see the current screen and answer with up to 4 actions, run one after another. An action holds
some buttons and/or sticks for a number of frames (60 = 1 second), then releases everything.
Buttons: a b x y l r zl zr plus minus up down left right (d-pad) lstick rstick (clicks).
stick moves the character (left stick), camera turns the camera (right stick): n ne e se s sw w nw (n = up), or none.
A menu press is frames 6. Walking somewhere is 30 to 180 frames. Use the game's own prompts and tutorials.
Keep notes: what the objective is, where you are, what worked and what did not. They are all you remember
beyond the last few turns. Set milestone true only when a clear step of the game was just reached (a level or
chapter cleared, a new area, a boss beaten, a save made)."""

SCHEMA = {
    "type": "object",
    "properties": {
        "screen": {"type": "string"},
        "progress": {"type": "string"},
        "notes": {"type": "string"},
        "milestone": {"type": "boolean"},
        "actions": {"type": "array", "maxItems": 4, "items": {
            "type": "object",
            "properties": {
                "buttons": {"type": "array", "items": {"type": "string", "enum": BUTTONS}},
                "stick": {"type": "string", "enum": ["none", *DIRS]},
                "camera": {"type": "string", "enum": ["none", *DIRS]},
                "frames": {"type": "integer"}},
            "required": ["buttons", "stick", "camera", "frames"]}}},
    "required": ["screen", "progress", "notes", "milestone", "actions"],
}


def axis(d):
    if d not in DIRS:
        return 0, 0
    r = np.radians(DIRS[d])
    return int(round(np.sin(r) * 32767)), int(round(np.cos(r) * 32767))  # y > 0 is up on the Switch


def image(shot):
    w, h, bgra = shot
    return Image.frombuffer("RGBA", (w, h), bgra, "raw", "BGRA", 0, 1).convert("RGB")


def jpeg(img, size=None, quality=80):
    buf = io.BytesIO()
    (img.resize(size, Image.BILINEAR) if size else img).save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def thumb(img):
    return np.asarray(img.resize((64, 36), Image.BILINEAR).convert("L"), dtype=np.float32)


def ask(a, game, notes, history, hint, img):
    turns = "\n".join(f"- {h['progress']} | did {h['did']} | screen {'changed' if h['changed'] else 'UNCHANGED'}"
                      for h in history[-8:]) or "- (first turn)"
    prompt = (f"Notes so far: {notes or '(none)'}\nLast turns:\n{turns}\n{hint}\n"
              "Answer with JSON: screen (what you see), progress (where you are in the game), notes (updated), "
              "milestone, actions.")
    body = {"model": a.model, "stream": False, "think": a.think, "format": SCHEMA, "keep_alive": "30m",
            "options": {"temperature": 0.7, "num_predict": 1024},
            "messages": [{"role": "system", "content": SYSTEM.format(game=game, goal=a.goal)},
                         {"role": "user", "content": prompt, "images": [base64.b64encode(jpeg(img)).decode()]}]}
    req = urllib.request.Request(a.ollama + "/api/chat", json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=900) as r:
        reply = json.load(r)
    return json.loads(reply["message"]["content"]), reply


def random_actions(rng):
    return [{"buttons": rng.sample(["a", "b", "a", "x", "y", "zr"], rng.randint(0, 1)),
             "stick": rng.choice([*DIRS]), "camera": rng.choice(["none", "none", *DIRS]),
             "frames": rng.randint(20, 90)} for _ in range(4)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("game")
    ap.add_argument("out")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=6560)
    ap.add_argument("--ollama", default="http://192.168.1.53:11434")
    ap.add_argument("--model", default="sofena-vision")
    ap.add_argument("--think", action="store_true")
    ap.add_argument("--goal", default="progress through the main story, as a first-time player would")
    ap.add_argument("--hours", type=float, default=2)
    ap.add_argument("--steps", type=int, default=0)
    ap.add_argument("--hang", type=float, default=180)
    ap.add_argument("--checkpoint")
    a = ap.parse_args()
    for d in ("frames", "milestones", "saves"):
        os.makedirs(os.path.join(a.out, d), exist_ok=True)
    log = open(os.path.join(a.out, "agent.log"), "a")
    rng = random.Random(1)

    b = Bridge(a.host, a.port)
    b.sock.settimeout(a.hang)
    pads = []
    for line in b.lines():  # the pads the game has connected, from the first frame line
        if line.startswith("f ") and " p " in line:
            t = line.split()
            pads = [int(t[i + 1]) for i in range(3, len(t)) if t[i] == "p"]
            break
    b.ask("stream 0", "ok")
    paused_at = int(b.pause().split()[2])
    time.sleep(3)  # nothing may move while the model thinks: 1 or 2 frames (to the step's end) if frozen, 180 if not
    pause_check = int(b.ask("step 1", "ok").split()[2]) - paused_at

    def run(act):
        bits = sum(1 << BUTTONS.index(n) for n in act["buttons"] if n in BUTTONS)
        lx, ly = axis(act["stick"])
        rx, ry = axis(act["camera"])
        for p in pads:
            b.pad(p, bits, lx, ly, rx, ry)
        b.step(max(1, min(int(act["frames"]), 300)))
        for p in pads:
            b.release(p)

    start, frames, notes, history, model_s = time.time(), 0, "", [], 0.0
    img = image(b.step(1, 540))
    prev, still, step, status = thumb(img), 0, 0, "time limit"
    while time.time() - start < a.hours * 3600 and (not a.steps or step < a.steps):
        step += 1
        hint = ""
        if still >= 4:
            hint = (f"WARNING: the screen has not changed for {still} turns: your last actions do nothing here. "
                    "Do something different (another button, another direction, open or close a menu).")
        t0 = time.time()
        try:
            d, reply = ({"screen": "", "progress": "random", "notes": "", "milestone": False,
                         "actions": random_actions(rng)}, {}) if a.model == "random" else \
                ask(a, a.game, notes, history, hint, img)
        except (ValueError, KeyError) as e:  # a truncated or empty answer: wait a little and look again
            d, reply = {"screen": "", "progress": f"(bad answer: {e})", "notes": notes, "milestone": False,
                        "actions": [{"buttons": [], "stick": "none", "camera": "none", "frames": 30}]}, {}
        dt = time.time() - t0
        model_s += dt
        acts = d["actions"][:4] or [{"buttons": [], "stick": "none", "camera": "none", "frames": 30}]
        if still >= 12:
            acts, d["progress"] = random_actions(rng), d["progress"] + " (random burst)"
        t1 = time.time()
        try:
            for act in acts:
                run(act)
            shot = b.step(GAP, 540)
        except (TimeoutError, OSError) as e:
            status = f"hang at step {step}: no frame for {a.hang:.0f} s ({e}) while playing {acts}"
            break
        frames += sum(max(1, min(int(x["frames"]), 300)) + GAP for x in acts)
        img = image(shot)
        cur = thumb(img)
        changed = float(np.abs(cur - prev).mean()) > 2.0
        still = 0 if changed else still + 1
        prev, notes = cur, d["notes"][:1500]
        did = "; ".join(f"{'+'.join(x['buttons']) or '-'}/{x['stick']}/{x['camera']}x{x['frames']}" for x in acts)
        history.append({"progress": d["progress"][:200], "did": did, "changed": changed})
        with open(os.path.join(a.out, "frames", f"{step:05}.jpg"), "wb") as f:
            f.write(jpeg(img, (480, 270), 70))
        if d["milestone"]:
            img.save(os.path.join(a.out, "milestones", f"{step:05}.jpg"), quality=90)
            if a.checkpoint:
                subprocess.run([*a.checkpoint.split(), os.path.join(a.out, "saves", f"{step:05}")])
        log.write(json.dumps({"step": step, "t": round(time.time() - start, 1), "frames": frames,
                              "model_s": round(dt, 1), "emu_s": round(time.time() - t1, 1),
                              "tokens_in": reply.get("prompt_eval_count"), "tokens_out": reply.get("eval_count"),
                              "changed": changed, "milestone": d["milestone"], "screen": d["screen"][:300],
                              "progress": d["progress"][:200], "did": did, "notes": notes}) + "\n")
        log.flush()
    wall = time.time() - start
    milestones = sorted(os.listdir(os.path.join(a.out, "milestones")))
    with open(os.path.join(a.out, "summary.txt"), "w") as f:
        f.write(f"game={a.game} model={a.model} think={a.think} pause_check={pause_check} frames end={status}\n"
                f"steps={step} game_time={frames / 60:.0f}s wall={wall:.0f}s model={model_s:.0f}s "
                f"game_s_per_wall_h={frames / 60 / max(wall, 1) * 3600:.0f}\n"
                f"milestones={len(milestones)} {' '.join(m[:-4] for m in milestones)}\n"
                f"last progress: {history[-1]['progress'] if history else '-'}\nnotes: {notes}\n")
    print(open(os.path.join(a.out, "summary.txt")).read(), end="")


if __name__ == "__main__":
    main()
