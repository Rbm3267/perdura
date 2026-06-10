#!/usr/bin/env python3
"""
Perdura MCP Server — the station.

Any MCP-compatible client (Claude Code, Claude Desktop, Cursor, custom agents)
can connect and become an ephemeral worker: board for a briefing, contribute
graph deltas, disembark. The graph persists in the station regardless of which
models have ever connected.

Connected models are WORKERS: per the design, workers never see authorship.
Worker-facing tools strip created_by and track records. Run a separate
instance with --operator to get the unredacted view for yourself.

Transport:
  stdio (local, default) — add to Claude Code or Claude Desktop config
  http  (remote)         — run on your Mini, models connect from your machines

Setup:
  pip install fastmcp        (or: pip install -e ".[server]")

Usage:
  python perdura_server.py                        # stdio, default graph path
  python perdura_server.py --operator             # stdio, attribution visible
  python perdura_server.py --http                 # HTTP on 127.0.0.1:8000
  python perdura_server.py --http --host 0.0.0.0  # expose beyond localhost
  python perdura_server.py --graph /path/to/perdura_graph.json

Client configuration snippets are at the bottom of this file.
"""

import argparse
import fcntl  # POSIX-only (macOS/Linux); the station is not Windows-portable
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))
from perdura import Graph, build_briefing, merge_delta, parse_delta

from fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------

mcp = FastMCP(
    name="perdura",
    instructions="""
You are connected to Perdura — a persistent knowledge graph where you are an
ephemeral worker. The graph is the system of record; you are not.

Workflow for a productive session:
1. Call perdura_questions to see what is open and which questions are most contested.
2. Call perdura_board to receive a bounded briefing for a question (or the most-contended one).
3. Think carefully. Then call perdura_contribute with a JSON delta of new nodes and edges.
4. Repeat steps 2-3 for as many questions as you want to work on.
5. Call perdura_show at any time to inspect the live graph state.

Contribution rules the conductor enforces:
- Return strict JSON only — your delta is schema-validated; malformed output is rejected and logged against your track record.
- Always pass your real model identity as worker_name (e.g. "claude-fable", "qwen3-14b") — it is how the graph builds per-model track records.
- New nodes need: ref (local handle), type, text, confidence (0-1), domain_tags.
- Edges need: type, src (ref or existing node id), dst (ref or existing node id).
- If you disagree with an existing claim, add your claim AND a 'contradicts' edge — disagreement is valuable signal, not something to suppress.
- If your claim replaces a stale one, use 'supersedes'.
- You never see who wrote prior nodes — this is intentional (anti-anchoring).
""",
)


# ---------------------------------------------------------------------------
# Graph access. Writes take an exclusive file lock; reads need no lock
# because Graph.save() is atomic (write-then-rename).
# ---------------------------------------------------------------------------

_GRAPH_PATH: str = "perdura_graph.json"
_OPERATOR: bool = False  # True exposes attribution; never for worker sessions


def get_graph() -> Graph:
    return Graph(_GRAPH_PATH)


def locked_write(fn):
    """Decorator: load graph, run fn(graph), save under an exclusive lock."""
    def wrapper(*args, **kwargs):
        lock_path = _GRAPH_PATH + ".lock"
        with open(lock_path, "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                g = Graph(_GRAPH_PATH)
                result = fn(g, *args, **kwargs)
                g.save()
                return result
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)
    return wrapper


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool(
    description="List all open questions in the graph, ordered by contention "
                "(most contested first). Returns ids, text, contention score, "
                "and domain tags. Start here to decide what to work on."
)
def perdura_questions() -> str:
    g = get_graph()
    questions = g.open_questions()
    if not questions:
        return json.dumps({"status": "no_open_questions",
                           "message": "The graph has no open questions. "
                                      "Use perdura_new_question to add one."})
    rows = []
    for q in questions:
        hood = g.neighborhood(q.id)
        rows.append({
            "id": q.id,
            "text": q.text,
            "domain_tags": q.domain_tags,
            "contention": g.contention(hood),
            "claim_count": sum(1 for n in g.live_nodes()
                               if n.id in hood and n.type == "claim"),
        })
    rows.sort(key=lambda r: -r["contention"])
    return json.dumps({"open_questions": rows, "total": len(rows)}, indent=2)


@mcp.tool(
    description="Board for a question: receive a bounded briefing containing "
                "the question and its 2-hop graph neighborhood. If question_id "
                "is omitted, you board for the most-contended open question. "
                "The briefing is your only context — there is no transcript."
)
def perdura_board(question_id: Optional[str] = None) -> str:
    g = get_graph()
    questions = g.open_questions()
    if not questions:
        return json.dumps({"error": "No open questions. Add one with perdura_new_question."})

    if question_id:
        q = g.nodes.get(question_id)
        if not q or q.type != "question":
            return json.dumps({"error": f"{question_id} is not a question node."})
        if q.superseded_by:
            return json.dumps({"error": f"{question_id} has been superseded "
                                        f"by {q.superseded_by}."})
        if q.status != "open":
            return json.dumps({"error": f"{question_id} is already resolved."})
    else:
        questions.sort(
            key=lambda q: -g.contention(g.neighborhood(q.id)))
        q = questions[0]

    briefing = build_briefing(g, q)
    hood = g.neighborhood(q.id)
    return json.dumps({
        "question_id": q.id,
        "question": q.text,
        "contention": g.contention(hood),
        "global_contention": g.contention(),
        "briefing_prompt": briefing,
        "instructions": (
            "Read the briefing_prompt carefully — it contains the question, "
            "related nodes, and existing edges. Then call perdura_contribute "
            "with your delta JSON. Use the delta schema exactly as documented "
            "in the briefing_prompt."
        ),
    }, indent=2)


@mcp.tool(
    description="Contribute a graph delta: new nodes, new edges, supersedes, "
                "and resolved questions. The conductor will validate, attribute "
                "the delta to your worker_name, and merge it. Returns a merge "
                "report with accepted/rejected counts and updated contention. "
                "Pass your real model identity as worker_name — it becomes "
                "part of the graph's track record."
)
def perdura_contribute(delta_json: str, worker_name: str = "mcp-client") -> str:
    @locked_write
    def _merge(g, delta_json, worker_name):
        try:
            delta = parse_delta(delta_json)
        except Exception as e:
            # Parse failures are track-record signal too (design claim #2)
            g.log.append({"ts": time.time(), "worker": worker_name,
                          "accepted": 0, "rejected": 0,
                          "error": str(e)[:200]})
            return {"status": "rejected", "reason": f"JSON parse failed: {e}",
                    "tip": "Return only a JSON object matching the delta schema."}
        accepted, rejected = merge_delta(g, delta, worker_name)
        return {
            "status": "merged",
            "worker": worker_name,
            "accepted": accepted,
            "rejected": rejected,
            "global_contention": g.contention(),
            "live_nodes": len(g.live_nodes()),
            "open_questions": len(g.open_questions()),
        }
    result = _merge(delta_json, worker_name)
    return json.dumps(result, indent=2)


@mcp.tool(
    description="Add a new question to the graph. The question becomes an "
                "open node that workers can board for. domain_tags help the "
                "router (Phase 3) match specialist models to the question."
)
def perdura_new_question(
    text: str,
    domain_tags: Optional[list[str]] = None,
) -> str:
    @locked_write
    def _add(g, text, domain_tags):
        qid = g.add_node("question", text, created_by="user",
                         confidence=1.0, domain_tags=domain_tags or [])
        return {"question_id": qid, "text": text,
                "message": f"Question added. Board it with perdura_board('{qid}')."}
    result = _add(text, domain_tags)
    return json.dumps(result, indent=2)


@mcp.tool(
    description="Show the live graph state: nodes, edges, global contention, "
                "and summary counts. Attribution and per-worker track records "
                "are only included when the station runs in --operator mode; "
                "workers never see authorship."
)
def perdura_show() -> str:
    g = get_graph()
    live = g.live_nodes()

    def node_view(n):
        d = {"id": n.id, "type": n.type, "text": n.text,
             "confidence": n.confidence, "domain_tags": n.domain_tags,
             "status": n.status if n.type == "question" else None}
        if _OPERATOR:
            d["created_by"] = n.created_by
        return d

    def edge_view(e):
        d = {"id": e.id, "type": e.type, "src": e.src, "dst": e.dst}
        if _OPERATOR:
            d["created_by"] = e.created_by
        return d

    out = {
        "summary": {
            "live_nodes": len(live),
            "superseded_nodes": len(g.nodes) - len(live),
            "edges": len(g.edges),
            "global_contention": g.contention(),
            "open_questions": len(g.open_questions()),
        },
        "nodes": [node_view(n) for n in sorted(live, key=lambda n: n.created_at)],
        "edges": [edge_view(e) for e in g.edges.values()],
    }
    if _OPERATOR:
        stats: dict = {}
        for entry in g.log:
            s = stats.setdefault(entry["worker"],
                                 {"accepted": 0, "rejected": 0, "errors": 0})
            s["accepted"] += entry.get("accepted", 0)
            s["rejected"] += entry.get("rejected", 0)
            s["errors"] += 1 if entry.get("error") else 0
        out["track_records"] = stats
    return json.dumps(out, indent=2)


@mcp.tool(
    description="Inspect a specific node: its metadata, all edges connected "
                "to it, and its lineage (what it superseded, what superseded "
                "it). Authorship is only included in --operator mode."
)
def perdura_node(node_id: str) -> str:
    g = get_graph()
    if node_id not in g.nodes:
        return json.dumps({"error": f"Node {node_id} not found."})
    n = g.nodes[node_id]
    connected = []
    for e in g.edges.values():
        if e.src == node_id or e.dst == node_id:
            d = {"edge_id": e.id, "type": e.type,
                 "direction": "out" if e.src == node_id else "in",
                 "other": e.dst if e.src == node_id else e.src}
            if _OPERATOR:
                d["created_by"] = e.created_by
            connected.append(d)
    lineage = {}
    if n.superseded_by:
        lineage["superseded_by"] = n.superseded_by
    superseded_this = [nid for nid, nd in g.nodes.items()
                       if nd.superseded_by == node_id]
    if superseded_this:
        lineage["supersedes"] = superseded_this

    out = {
        "id": n.id, "type": n.type, "text": n.text,
        "confidence": n.confidence, "domain_tags": n.domain_tags,
        "created_at": n.created_at, "status": n.status,
        "is_live": n.superseded_by is None,
        "connected_edges": connected, "lineage": lineage,
    }
    if _OPERATOR:
        out["created_by"] = n.created_by
    return json.dumps(out, indent=2)


@mcp.tool(
    description="Get contention breakdown: global score and per-question "
                "scores. High contention means the graph disagrees with itself "
                "there — that is where expensive models should focus."
)
def perdura_contention() -> str:
    g = get_graph()
    per_question = []
    for q in g.open_questions():
        hood = g.neighborhood(q.id)
        per_question.append({
            "question_id": q.id,
            "text": q.text[:80],
            "contention": g.contention(hood),
            "hood_size": len(hood),
        })
    per_question.sort(key=lambda r: -r["contention"])
    return json.dumps({
        "global_contention": g.contention(),
        "per_question": per_question,
        "routing_signal": (
            "High contention subgraphs are where frontier or specialist "
            "models earn their keep. Low contention = local model territory."
        ),
    }, indent=2)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Perdura MCP Station")
    p.add_argument("--http", action="store_true",
                   help="Run as HTTP server (remote workers) instead of stdio")
    p.add_argument("--port", type=int, default=8000,
                   help="HTTP port (default 8000)")
    p.add_argument("--host", default="127.0.0.1",
                   help="HTTP host. Default 127.0.0.1; the server has NO "
                        "auth, so expose 0.0.0.0 only on a trusted network "
                        "(e.g. behind Tailscale or an SSH tunnel).")
    p.add_argument("--graph", default="perdura_graph.json",
                   help="Path to the graph JSON file (use an absolute path "
                        "in client configs — clients set arbitrary CWDs)")
    p.add_argument("--operator", action="store_true",
                   help="Expose attribution and track records. For your own "
                        "sessions only — never for worker models.")
    args = p.parse_args()

    _GRAPH_PATH = args.graph
    _OPERATOR = args.operator
    if not os.path.exists(_GRAPH_PATH):
        Graph(_GRAPH_PATH).save()

    if args.http:
        print(f"[perdura] Station open on http://{args.host}:{args.port}",
              file=sys.stderr)
        print(f"[perdura] Graph: {os.path.abspath(_GRAPH_PATH)}", file=sys.stderr)
        mcp.run(transport="http", host=args.host, port=args.port)
    else:
        # stdio — the default for Claude Code / Claude Desktop
        mcp.run(transport="stdio")


# ---------------------------------------------------------------------------
# Configuration snippets (copy-paste ready; use absolute paths throughout)
# ---------------------------------------------------------------------------
#
# ── Claude Code (project scope: .mcp.json at the repo root) ─────────────────
# {
#   "mcpServers": {
#     "perdura": {
#       "command": "/abs/path/perdura/.venv/bin/python",
#       "args": ["/abs/path/perdura/perdura_server.py",
#                "--graph", "/abs/path/perdura/perdura_graph.json"]
#     }
#   }
# }
#
# ── Claude Code (user scope, any project) ────────────────────────────────────
# claude mcp add --scope user perdura -- \
#   /abs/path/perdura/.venv/bin/python /abs/path/perdura/perdura_server.py \
#   --graph /abs/path/perdura/perdura_graph.json
#
# ── Claude Desktop (~/Library/Application Support/Claude/claude_desktop_config.json)
# {
#   "mcpServers": {
#     "perdura": {
#       "command": "/abs/path/perdura/.venv/bin/python",
#       "args": ["/abs/path/perdura/perdura_server.py",
#                "--graph", "/abs/path/perdura/perdura_graph.json"]
#     }
#   }
# }
#
# ── Remote (Mini as station, laptop as worker) ────────────────────────────────
# On the Mini (reachable only via trusted network/Tailscale):
#   python perdura_server.py --http --host 0.0.0.0 --port 8000 \
#     --graph /abs/path/perdura_graph.json
# Claude Code client:
#   claude mcp add --transport http perdura http://mini.local:8000/mcp
# Or in .mcp.json:
#   { "mcpServers": { "perdura": { "type": "http", "url": "http://mini.local:8000/mcp" } } }
#
# ── Multiple workers simultaneously ──────────────────────────────────────────
# Each client gets its own connection. Writes are serialised by the file
# lock in locked_write; reads are safe because Graph.save() is atomic
# (write-then-rename), so a boarding worker never sees a half-written file.
