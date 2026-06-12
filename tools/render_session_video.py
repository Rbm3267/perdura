"""
tools/render_session_video.py — replay a Perdura session as a short video.

Left: the graph growing in creation order (contradicts edges flash red).
Right: the conversation — what each worker actually wrote as it boarded,
with challenges called out. HUD tracks turns and contention.

    .venv/bin/python tools/render_session_video.py \
        --graph /tmp/lock_run.json --out assets/perdura-session.mp4
"""

import argparse
import json
import math
import random
import textwrap

import numpy as np
from PIL import Image, ImageDraw, ImageFont
import imageio.v2 as imageio

W, H = 1280, 720
PANEL_X = 760                      # graph area 0..PANEL_X, feed PANEL_X..W
INK = (10, 14, 31)
INK2 = (14, 20, 48)
PAPER = (240, 243, 250)
MUTED = (151, 163, 196)
FAINT = (91, 102, 136)
CYAN = (45, 217, 255)
AMBER = (255, 180, 84)
ROSE = (255, 93, 143)
GREEN = (61, 220, 151)
NODE_COLORS = {"question": CYAN, "claim": PAPER, "evidence": GREEN,
               "decision": AMBER, "rejected": ROSE}
WORKER_COLORS = {"claude": AMBER, "gemini": CYAN, "user": FAINT}
FPS = 30
# Pacing per event kind — claims linger so the text is readable
SECONDS = {"question": 0.6, "claim": 1.45, "evidence": 0.9, "decision": 1.0,
           "rejected": 0.8, "contradicts": 1.3, "edge": 0.05}
HOLD_SECONDS = 3.0
WRAP = 40
MAX_LINES = 4


def _font(size, mono=True):
    name = "DejaVuSansMono.ttf" if mono else "DejaVuSans.ttf"
    try:
        return ImageFont.truetype(
            f"/usr/share/fonts/truetype/dejavu/{name}", size)
    except OSError:
        return ImageFont.load_default()


def layout(nodes, edges, iters=600, seed=7):
    rng = random.Random(seed)
    cx, cy = PANEL_X / 2, (H - 60) / 2 + 40
    pos = {n["id"]: [cx + rng.uniform(-230, 230), cy + rng.uniform(-160, 160)]
           for n in nodes}
    ids = list(pos)
    for _ in range(iters):
        force = {i: [0.0, 0.0] for i in ids}
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                a, b = pos[ids[i]], pos[ids[j]]
                dx, dy = a[0] - b[0], a[1] - b[1]
                d2 = dx * dx + dy * dy + 0.01
                f = 4400 / d2
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
            f = 0.011 * (d - 85)
            for nid, s in ((e["src"], 1), (e["dst"], -1)):
                force[nid][0] += s * dx / d * f * d * 0.1
                force[nid][1] += s * dy / d * f * d * 0.1
        for i in ids:
            pos[i][0] += max(-9, min(9, force[i][0] + (cx - pos[i][0]) * 0.004))
            pos[i][1] += max(-9, min(9, force[i][1] + (cy - pos[i][1]) * 0.004))
            pos[i][0] = max(40, min(PANEL_X - 40, pos[i][0]))
            pos[i][1] = max(110, min(H - 80, pos[i][1]))
    return pos


def feed_entry(kind, obj, nodes):
    """(color, header, wrapped body lines) for the conversation panel."""
    if kind == "node":
        who = obj.get("created_by") or "?"
        col = WORKER_COLORS.get(who, MUTED)
        head = f"{who} · {obj['type']}"
        if obj["type"] != "question":
            head += f" · conf {obj.get('confidence', 0):.2f}"
        body = textwrap.wrap(obj["text"], WRAP)[:MAX_LINES]
        if len(textwrap.wrap(obj["text"], WRAP)) > MAX_LINES:
            body[-1] = body[-1][:WRAP - 1] + "…"
        return (col, head, body)
    # contradicts edge: show the challenge
    target = nodes.get(obj["dst"], {})
    who = obj.get("created_by") or "?"
    body = textwrap.wrap(f'challenges: "{target.get("text", "")}"', WRAP)[:2]
    if body:
        body[-1] = body[-1][:WRAP - 1] + ("…" if len(body[-1]) >= WRAP - 1 else "")
    return (ROSE, f"{who} ⚡ contradicts", body)


def render(graph_path, out_path):
    data = json.load(open(graph_path))
    nodes = {n["id"]: n for n in data["nodes"]}
    pos = layout(data["nodes"], data["edges"])

    events = ([("node", n["created_at"], n) for n in data["nodes"]]
              + [("edge", e["created_at"], e) for e in data["edges"]])
    events.sort(key=lambda t: t[1])
    turn_times = sorted(e.get("ts", 0) for e in data.get("log", []))

    f_title, f_hud = _font(30), _font(17)
    f_head, f_body, f_small = _font(15), _font(15, mono=False), _font(13)

    writer = imageio.get_writer(out_path, fps=FPS, codec="libx264",
                                quality=7, macro_block_size=16)

    shown_nodes, shown_edges, feed = [], [], []
    contras = claims = 0

    def draw_frame(flash=None, progress=1.0):
        img = Image.new("RGB", (W, H), INK)
        d = ImageDraw.Draw(img)
        # ── conversation panel ──
        d.rectangle([PANEL_X, 0, W, H], fill=INK2)
        d.line([PANEL_X, 0, PANEL_X, H], fill=(40, 50, 85), width=2)
        d.text((PANEL_X + 24, 24), "THE CONVERSATION", font=f_head, fill=FAINT)
        y = 56
        line_h, gap = 19, 14
        # most recent entries that fit
        visible = []
        budget = H - 70 - y
        for entry in reversed(feed):
            need = line_h + len(entry[2]) * line_h + gap
            if budget - need < 0:
                break
            visible.append(entry)
            budget -= need
        for col, head, body in reversed(visible):
            d.text((PANEL_X + 24, y), head, font=f_head, fill=col)
            y += line_h
            for line in body:
                d.text((PANEL_X + 24, y), line, font=f_body, fill=MUTED)
                y += line_h
            y += gap
        # ── graph ──
        for e in shown_edges:
            a, b = pos.get(e["src"]), pos.get(e["dst"])
            if not a or not b:
                continue
            hot = e["type"] == "contradicts"
            col, width = (ROSE, 3) if hot else ((60, 70, 105), 1)
            if flash is e:
                width = 4
            d.line([*a, *b], fill=col, width=width)
        for n in shown_nodes:
            x, y2 = pos[n["id"]]
            r = 9 if n["type"] == "question" else 6
            if flash is n:
                r += int(6 * (1 - progress))
            if n["type"] == "question":
                d.ellipse([x - r, y2 - r, x + r, y2 + r], outline=CYAN, width=3)
            else:
                d.ellipse([x - r, y2 - r, x + r, y2 + r],
                          fill=NODE_COLORS.get(n["type"], MUTED))
        # ── HUD ──
        d.text((40, 24), "PERDURA — a real session, replayed",
               font=f_title, fill=PAPER)
        d.text((40, 64), "Claude + Gemini · contested seeds · "
                         "adversarial boarding every 3rd turn",
               font=f_small, fill=FAINT)
        t = shown_nodes[-1]["created_at"] if shown_nodes else 0
        turn = sum(1 for tt in turn_times if tt <= t)
        cont = round(contras / max(1, claims), 3)
        d.text((40, H - 44),
               f"turn {turn:>2}/24   nodes {len(shown_nodes):>2}   "
               f"contradicts {contras:>2}   contention {cont:.3f}",
               font=f_hud, fill=ROSE if contras else MUTED)
        return np.asarray(img)

    for _ in range(int(FPS * 1.2)):
        writer.append_data(draw_frame())

    for kind, _, obj in events:
        if kind == "node":
            shown_nodes.append(obj)
            claims += obj["type"] == "claim"
            feed.append(feed_entry("node", obj, nodes))
            secs = SECONDS.get(obj["type"], 0.8)
        else:
            shown_edges.append(obj)
            if obj["type"] == "contradicts":
                contras += 1
                feed.append(feed_entry("contradicts", obj, nodes))
                secs = SECONDS["contradicts"]
            else:
                secs = SECONDS["edge"]
        n_frames = max(1, int(FPS * secs))
        for f in range(n_frames):
            writer.append_data(draw_frame(flash=obj,
                                          progress=(f + 1) / n_frames))

    for _ in range(int(FPS * HOLD_SECONDS)):
        writer.append_data(draw_frame())
    writer.close()
    print(f"{out_path}: {len(events)} events rendered")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Render a session replay video")
    p.add_argument("--graph", default="perdura_graph.json")
    p.add_argument("--out", default="assets/perdura-session.mp4")
    args = p.parse_args()
    render(args.graph, args.out)
