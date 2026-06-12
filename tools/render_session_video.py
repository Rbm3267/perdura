"""
tools/render_session_video.py — replay a Perdura session as a short video.

Loads a graph JSON, computes a force layout on the final graph, then
replays nodes and edges in creation order: claims appear as the workers
boarded, contradicts edges flash red, and the HUD tracks turns and
contention. Output is an H.264 MP4 in the site's visual identity.

    .venv/bin/python tools/render_session_video.py \
        --graph /tmp/lock_run.json --out assets/perdura-session.mp4
"""

import argparse
import json
import math
import random

from PIL import Image, ImageDraw, ImageFont
import imageio.v2 as imageio

W, H = 1280, 720
INK = (10, 14, 31)
INK2 = (14, 20, 48)
PAPER = (240, 243, 250)
MUTED = (151, 163, 196)
FAINT = (91, 102, 136)
CYAN = (45, 217, 255)
AMBER = (255, 180, 84)
ROSE = (255, 93, 143)
GREEN = (61, 220, 151)
COLORS = {"question": CYAN, "claim": PAPER, "evidence": GREEN,
          "decision": AMBER, "rejected": ROSE}
FPS = 30
SECONDS_PER_EVENT = 0.22
HOLD_SECONDS = 3.0


def _font(size):
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def layout(nodes, edges, iters=600, seed=7):
    """Force layout on the final graph; replay reveals into fixed positions."""
    rng = random.Random(seed)
    pos = {n["id"]: [W / 2 + rng.uniform(-250, 250),
                     H / 2 + rng.uniform(-160, 160)] for n in nodes}
    ids = list(pos)
    for _ in range(iters):
        force = {i: [0.0, 0.0] for i in ids}
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                a, b = pos[ids[i]], pos[ids[j]]
                dx, dy = a[0] - b[0], a[1] - b[1]
                d2 = dx * dx + dy * dy + 0.01
                f = 5200 / d2
                d = math.sqrt(d2)
                force[ids[i]][0] += dx / d * f
                force[ids[i]][1] += dy / d * f
                force[ids[j]][0] -= dx / d * f
                force[ids[j]][1] -= dy / d * f
        for e in edges:
            if e["src"] not in pos or e["dst"] not in pos:
                continue
            a, b = pos[e["src"]], pos[e["dst"]]
            dx, dy = b[0] - a[0], b[1] - a[1]
            d = math.sqrt(dx * dx + dy * dy) + 0.01
            f = 0.011 * (d - 95)
            force[e["src"]][0] += dx / d * f * d * 0.1
            force[e["src"]][1] += dy / d * f * d * 0.1
            force[e["dst"]][0] -= dx / d * f * d * 0.1
            force[e["dst"]][1] -= dy / d * f * d * 0.1
        for i in ids:
            pos[i][0] += max(-9, min(9, force[i][0] + (W / 2 - pos[i][0]) * 0.003))
            pos[i][1] += max(-9, min(9, force[i][1] + (H / 2 - pos[i][1]) * 0.003))
            pos[i][0] = max(60, min(W - 60, pos[i][0]))
            pos[i][1] = max(90, min(H - 70, pos[i][1]))
    return pos


def render(graph_path, out_path):
    data = json.load(open(graph_path))
    nodes = {n["id"]: n for n in data["nodes"]}
    pos = layout(data["nodes"], data["edges"])

    # Replay events in creation order
    events = ([("node", n["created_at"], n) for n in data["nodes"]]
              + [("edge", e["created_at"], e) for e in data["edges"]])
    events.sort(key=lambda t: t[1])
    turn_times = sorted(e.get("ts", 0) for e in data.get("log", []))

    f_big, f_mid, f_small = _font(34), _font(20), _font(15)
    frames_per_event = max(1, int(FPS * SECONDS_PER_EVENT))
    writer = imageio.get_writer(out_path, fps=FPS, codec="libx264",
                                quality=7, macro_block_size=16,
                                ffmpeg_params=["-pix_fmt", "yuv420p"])

    shown_nodes, shown_edges = [], []
    contras = claims = 0
    title_text = "PERDURA — a real session, replayed"
    sub_text = "Claude + Gemini · contested seeds · adversarial boarding every 3rd turn"

    def draw_frame(flash=None, progress=1.0):
        img = Image.new("RGB", (W, H), INK)
        d = ImageDraw.Draw(img)
        # edges
        for e in shown_edges:
            a, b = pos.get(e["src"]), pos.get(e["dst"])
            if not a or not b:
                continue
            hot = e["type"] == "contradicts"
            col = ROSE if hot else (60, 70, 105)
            width = 3 if hot else 1
            if flash is e:
                col = tuple(min(255, int(c + (255 - c) * (1 - progress)))
                            for c in ROSE)
                width = 4
            d.line([*a, *b], fill=col, width=width)
        # nodes
        for n in shown_nodes:
            x, y = pos[n["id"]]
            r = 9 if n["type"] == "question" else 6
            if flash is n:
                r += int(6 * (1 - progress))
            col = COLORS.get(n["type"], MUTED)
            if n["type"] == "question":
                d.ellipse([x - r, y - r, x + r, y + r], outline=CYAN, width=3)
            else:
                d.ellipse([x - r, y - r, x + r, y + r], fill=col)
        # HUD
        d.text((40, 26), title_text, font=f_big, fill=PAPER)
        d.text((40, 70), sub_text, font=f_small, fill=FAINT)
        t = shown_nodes[-1]["created_at"] if shown_nodes else 0
        turn = sum(1 for tt in turn_times if tt <= t)
        cont = round(contras / max(1, claims), 3)
        hud = f"turn {turn:>2}/24    nodes {len(shown_nodes):>2}    " \
              f"contradicts {contras:>2}    contention {cont:.3f}"
        d.text((40, H - 46), hud, font=f_mid,
               fill=ROSE if contras else MUTED)
        d.text((W - 330, H - 42), "perdura.network", font=f_small, fill=FAINT)
        return img

    # opening hold
    for _ in range(int(FPS * 1.2)):
        writer.append_data(__import__("numpy").asarray(draw_frame()))

    np = __import__("numpy")
    for kind, _, obj in events:
        if kind == "node":
            shown_nodes.append(obj)
            if obj["type"] == "claim":
                claims += 1
        else:
            shown_edges.append(obj)
            if obj["type"] == "contradicts":
                contras += 1
        for f in range(frames_per_event):
            writer.append_data(np.asarray(
                draw_frame(flash=obj, progress=(f + 1) / frames_per_event)))

    for _ in range(int(FPS * HOLD_SECONDS)):
        writer.append_data(np.asarray(draw_frame()))
    writer.close()
    n_frames = int(FPS * 1.2) + len(events) * frames_per_event + int(FPS * HOLD_SECONDS)
    print(f"{out_path}: {len(events)} events, ~{n_frames / FPS:.0f}s @ {FPS}fps")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Render a session replay video")
    p.add_argument("--graph", default="perdura_graph.json")
    p.add_argument("--out", default="assets/perdura-session.mp4")
    args = p.parse_args()
    render(args.graph, args.out)
