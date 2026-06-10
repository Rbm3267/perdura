#!/usr/bin/env python3
"""
Perdura — Phase 1: persistent knowledge graph + ephemeral LLM workers.

The graph is the system of record. Models board, receive a bounded briefing
(never the transcript), return a strict-JSON delta (nodes + edges), and
disembark. The conductor is deterministic code: it validates, attributes,
merges, and tracks contention.

Setup:
    pip install anthropic google-genai openai
    export ANTHROPIC_API_KEY=...  GEMINI_API_KEY=...
    ollama pull qwen3:14b

Usage:
    python perdura.py new "How should multi-agent memory be architected?"
    python perdura.py run --turns 6                 # round-robin boarding
    python perdura.py run --turns 2 --workers qwen   # cheap labor only
    python perdura.py show                           # print graph state
    python perdura.py demo                           # offline mock worker test

Graph persists to perdura_graph.json (override with --graph PATH).
"""

import argparse
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass, asdict, field

# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------

NODE_TYPES = {"question", "claim", "evidence", "decision", "rejected"}
EDGE_TYPES = {"supports", "contradicts", "refines", "answers", "depends_on"}


@dataclass
class Node:
    id: str
    type: str
    text: str
    domain_tags: list = field(default_factory=list)
    created_by: str = ""
    confidence: float = 0.5
    created_at: float = 0.0
    status: str = "open"          # questions: open/resolved
    superseded_by: str = None     # temporal record, never delete


@dataclass
class Edge:
    id: str
    type: str
    src: str
    dst: str
    created_by: str = ""
    created_at: float = 0.0


class Graph:
    def __init__(self, path):
        self.path = path
        self.nodes: dict[str, Node] = {}
        self.edges: dict[str, Edge] = {}
        self.log: list = []  # merge log: (ts, worker, accepted, rejected)
        if os.path.exists(path):
            self._load()

    # -- persistence --------------------------------------------------------
    def _load(self):
        data = json.load(open(self.path))
        self.nodes = {n["id"]: Node(**n) for n in data["nodes"]}
        self.edges = {e["id"]: Edge(**e) for e in data["edges"]}
        self.log = data.get("log", [])

    def save(self):
        json.dump(
            {"nodes": [asdict(n) for n in self.nodes.values()],
             "edges": [asdict(e) for e in self.edges.values()],
             "log": self.log},
            open(self.path, "w"), indent=2)

    # -- mutation (conductor-only) ------------------------------------------
    def add_node(self, type, text, created_by, confidence=0.5,
                 domain_tags=None, status="open"):
        nid = f"n_{uuid.uuid4().hex[:8]}"
        self.nodes[nid] = Node(
            id=nid, type=type, text=text.strip(),
            domain_tags=domain_tags or [], created_by=created_by,
            confidence=max(0.0, min(1.0, float(confidence))),
            created_at=time.time(), status=status)
        return nid

    def add_edge(self, type, src, dst, created_by):
        eid = f"e_{uuid.uuid4().hex[:8]}"
        self.edges[eid] = Edge(id=eid, type=type, src=src, dst=dst,
                               created_by=created_by, created_at=time.time())
        return eid

    def supersede(self, old_id, new_id):
        if old_id in self.nodes:
            self.nodes[old_id].superseded_by = new_id

    # -- queries -------------------------------------------------------------
    def live_nodes(self):
        return [n for n in self.nodes.values() if n.superseded_by is None]

    def open_questions(self):
        return [n for n in self.live_nodes()
                if n.type == "question" and n.status == "open"]

    def neighborhood(self, node_id, hops=2):
        """IDs of nodes within `hops` edges of node_id (the briefing subgraph)."""
        seen = {node_id}
        frontier = {node_id}
        for _ in range(hops):
            nxt = set()
            for e in self.edges.values():
                if e.src in frontier and e.dst not in seen:
                    nxt.add(e.dst)
                if e.dst in frontier and e.src not in seen:
                    nxt.add(e.src)
            seen |= nxt
            frontier = nxt
        return seen

    def contention(self, node_ids=None):
        """contradicts-edges per claim, confidence-weighted. The routing signal."""
        ids = node_ids or {n.id for n in self.live_nodes()}
        claims = [n for n in self.live_nodes()
                  if n.id in ids and n.type == "claim"]
        if not claims:
            return 0.0
        contra = sum(
            (self.nodes[e.src].confidence + self.nodes[e.dst].confidence) / 2
            for e in self.edges.values()
            if e.type == "contradicts" and e.src in ids and e.dst in ids
            and e.src in self.nodes and e.dst in self.nodes)
        return round(contra / len(claims), 3)


# ---------------------------------------------------------------------------
# Briefing + delta extraction
# ---------------------------------------------------------------------------

BRIEFING_CHAR_BUDGET = 6000  # bounded regardless of graph size

DELTA_PROMPT = """\
You are an ephemeral worker contributing to a persistent knowledge graph.
You will see a briefing (open question + related nodes), NOT a transcript.
Your job: advance the graph. Respond with ONLY a JSON object, no markdown
fences, no prose, matching exactly this schema:

{{
  "new_nodes": [
    {{"ref": "a", "type": "claim|evidence|question|decision|rejected",
      "text": "...", "confidence": 0.0-1.0, "domain_tags": ["..."]}}
  ],
  "new_edges": [
    {{"type": "supports|contradicts|refines|answers|depends_on",
      "src": "a or existing node id", "dst": "a or existing node id"}}
  ],
  "supersedes": [ {{"old": "existing node id", "new": "ref like a"}} ],
  "resolve_questions": ["existing question node id if now answered"]
}}

Rules:
- "ref" is a short local handle (a, b, c...) for nodes you create this turn;
  use it in edges. Use existing node ids (n_xxxxxxxx) to link to prior work.
- Make 1-4 high-quality nodes. Quality over quantity.
- If you disagree with an existing claim, add your claim AND a "contradicts"
  edge to it. Disagreement is valuable signal — do not suppress it.
- If an existing claim is weak/stale and yours replaces it, use "supersedes".
- Add a new "question" node if your contribution surfaces one.
- Do NOT restate existing nodes as new nodes.

BRIEFING
========
Open question under consideration:
{question}

Related nodes (id | type | confidence | text):
{nodes}

Existing edges among them:
{edges}
"""


def build_briefing(graph: Graph, question: Node):
    ids = graph.neighborhood(question.id, hops=2)
    # Always include other open questions so workers can link across them
    ids |= {q.id for q in graph.open_questions()}
    nodes = [graph.nodes[i] for i in ids
             if graph.nodes[i].superseded_by is None]
    nodes.sort(key=lambda n: -n.confidence)

    lines, used = [], 0
    for n in nodes:
        line = f"{n.id} | {n.type} | {n.confidence:.2f} | {n.text}"
        if used + len(line) > BRIEFING_CHAR_BUDGET:
            break
        lines.append(line)
        used += len(line)
    shown = {l.split(" | ")[0] for l in lines}
    edge_lines = [f"{e.src} -[{e.type}]-> {e.dst}"
                  for e in graph.edges.values()
                  if e.src in shown and e.dst in shown]
    return DELTA_PROMPT.format(
        question=f"{question.id}: {question.text}",
        nodes="\n".join(lines) or "(none yet)",
        edges="\n".join(edge_lines) or "(none yet)")


def parse_delta(raw: str):
    """Tolerant JSON extraction: strip fences / surrounding prose."""
    s = raw.strip()
    if s.startswith("```"):
        s = s.split("```")[1]
        s = s[4:] if s.startswith("json") else s
    start, end = s.find("{"), s.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object found")
    return json.loads(s[start:end + 1])


def merge_delta(graph: Graph, delta: dict, worker: str):
    """Conductor: validate, attribute, merge. Returns (accepted, rejected)."""
    accepted, rejected = 0, 0
    ref_map = {}

    for n in delta.get("new_nodes", []):
        if n.get("type") not in NODE_TYPES or not n.get("text"):
            rejected += 1
            continue
        nid = graph.add_node(
            type=n["type"], text=n["text"], created_by=worker,
            confidence=n.get("confidence", 0.5),
            domain_tags=n.get("domain_tags", []))
        ref_map[n.get("ref", nid)] = nid
        accepted += 1

    def resolve(ref):
        return ref_map.get(ref, ref if ref in graph.nodes else None)

    for e in delta.get("new_edges", []):
        src, dst = resolve(e.get("src", "")), resolve(e.get("dst", ""))
        if e.get("type") in EDGE_TYPES and src and dst and src != dst:
            graph.add_edge(e["type"], src, dst, worker)
            accepted += 1
        else:
            rejected += 1

    for s in delta.get("supersedes", []):
        old, new = s.get("old"), resolve(s.get("new", ""))
        if old in graph.nodes and new:
            graph.supersede(old, new)

    for qid in delta.get("resolve_questions", []):
        if qid in graph.nodes and graph.nodes[qid].type == "question":
            graph.nodes[qid].status = "resolved"

    graph.log.append({"ts": time.time(), "worker": worker,
                      "accepted": accepted, "rejected": rejected})
    return accepted, rejected


# ---------------------------------------------------------------------------
# Workers (ephemeral — note: no per-worker state anywhere)
# ---------------------------------------------------------------------------

class ClaudeWorker:
    name = "claude"

    def __init__(self, model="claude-sonnet-4-5"):
        import anthropic
        self.client, self.model = anthropic.Anthropic(), model

    def generate(self, prompt):
        r = self.client.messages.create(model=self.model, max_tokens=1500,
                                        messages=[{"role": "user",
                                                   "content": prompt}])
        return r.content[0].text


class GeminiWorker:
    name = "gemini"

    def __init__(self, model="gemini-2.5-flash"):
        from google import genai
        self.client, self.model = genai.Client(), model

    def generate(self, prompt):
        return self.client.models.generate_content(
            model=self.model, contents=prompt).text


class QwenWorker:
    name = "qwen"

    def __init__(self, model="qwen3:14b", base_url="http://localhost:11434/v1"):
        from openai import OpenAI
        self.client = OpenAI(base_url=base_url, api_key="local")
        self.model = model

    def generate(self, prompt):
        r = self.client.chat.completions.create(
            model=self.model, messages=[{"role": "user", "content": prompt}])
        return r.choices[0].message.content


class MockWorker:
    """Offline worker for testing the loop without API keys."""
    name = "mock"
    _i = 0

    def generate(self, prompt):
        MockWorker._i += 1
        i = MockWorker._i
        qid = prompt.split("Open question under consideration:\n")[1].split(":")[0]
        return json.dumps({
            "new_nodes": [
                {"ref": "a", "type": "claim",
                 "text": f"Mock claim #{i}: briefings should be confidence-ranked.",
                 "confidence": 0.6, "domain_tags": ["architecture"]},
                {"ref": "b", "type": "evidence",
                 "text": f"Mock evidence #{i}: bounded briefings keep cost flat.",
                 "confidence": 0.7, "domain_tags": ["architecture"]},
            ],
            "new_edges": [
                {"type": "answers", "src": "a", "dst": qid},
                {"type": "supports", "src": "b", "dst": "a"},
            ],
            "supersedes": [],
            "resolve_questions": [],
        })


WORKER_FACTORIES = {
    "claude": lambda a: ClaudeWorker(a.claude_model),
    "gemini": lambda a: GeminiWorker(a.gemini_model),
    "qwen":   lambda a: QwenWorker(a.qwen_model, a.qwen_url),
    "mock":   lambda a: MockWorker(),
}


# ---------------------------------------------------------------------------
# Conductor loop + CLI
# ---------------------------------------------------------------------------

def run_turns(graph: Graph, workers: list, turns: int):
    """Round-robin boarding (Phase 3 replaces this with the router)."""
    for t in range(turns):
        questions = graph.open_questions()
        if not questions:
            print("No open questions — the train is at rest.")
            break
        # Most-contended open question first
        questions.sort(
            key=lambda q: -graph.contention(graph.neighborhood(q.id)))
        q = questions[0]
        worker = workers[t % len(workers)]

        print(f"\n[turn {t+1}] {worker.name} boards for {q.id}: "
              f"{q.text[:60]}...")
        briefing = build_briefing(graph, q)
        try:
            delta = parse_delta(worker.generate(briefing))
            acc, rej = merge_delta(graph, delta, worker.name)
            print(f"  merged: {acc} accepted, {rej} rejected | "
                  f"global contention: {graph.contention()}")
        except Exception as e:
            graph.log.append({"ts": time.time(), "worker": worker.name,
                              "accepted": 0, "rejected": 0,
                              "error": str(e)[:200]})
            print(f"  delta rejected entirely ({e})")
        graph.save()


def show(graph: Graph):
    live = graph.live_nodes()
    print(f"\nGraph: {len(live)} live nodes "
          f"({len(graph.nodes) - len(live)} superseded), "
          f"{len(graph.edges)} edges, contention {graph.contention()}\n")
    for n in sorted(live, key=lambda n: n.created_at):
        flag = " [RESOLVED]" if n.type == "question" and n.status == "resolved" else ""
        print(f"  {n.id} [{n.type}{flag}] ({n.created_by}, "
              f"{n.confidence:.2f}) {n.text}")
    if graph.edges:
        print()
        for e in graph.edges.values():
            print(f"  {e.src} -[{e.type}]-> {e.dst}  ({e.created_by})")
    # Per-model merge stats — the seed of Phase 2 track records
    stats = {}
    for entry in graph.log:
        s = stats.setdefault(entry["worker"], {"accepted": 0, "rejected": 0,
                                               "errors": 0})
        s["accepted"] += entry.get("accepted", 0)
        s["rejected"] += entry.get("rejected", 0)
        s["errors"] += 1 if entry.get("error") else 0
    if stats:
        print("\nWorker merge stats (proto track record):")
        for w, s in stats.items():
            print(f"  {w}: {s['accepted']} accepted, "
                  f"{s['rejected']} rejected, {s['errors']} failed turns")


def main():
    p = argparse.ArgumentParser(description="Perdura Phase 1")
    p.add_argument("command", choices=["new", "run", "show", "demo"])
    p.add_argument("text", nargs="?", help="question text (for `new`)")
    p.add_argument("--graph", default="perdura_graph.json")
    p.add_argument("--turns", type=int, default=6)
    p.add_argument("--workers", default="qwen,claude,gemini",
                   help="comma list: qwen,claude,gemini,mock")
    p.add_argument("--claude-model", default="claude-sonnet-4-5")
    p.add_argument("--gemini-model", default="gemini-2.5-flash")
    p.add_argument("--qwen-model", default="qwen3:14b")
    p.add_argument("--qwen-url", default="http://localhost:11434/v1")
    args = p.parse_args()

    graph = Graph(args.graph)

    if args.command == "new":
        if not args.text:
            sys.exit("Provide the question text: perdura.py new \"...\"")
        qid = graph.add_node("question", args.text, created_by="user",
                             confidence=1.0)
        graph.save()
        print(f"Boarded new question {qid}: {args.text}")

    elif args.command == "run":
        names = [w.strip() for w in args.workers.split(",")]
        workers = [WORKER_FACTORIES[n](args) for n in names]
        run_turns(graph, workers, args.turns)
        show(graph)

    elif args.command == "show":
        show(graph)

    elif args.command == "demo":
        demo_path = "perdura_demo_graph.json"
        if os.path.exists(demo_path):
            os.remove(demo_path)
        g = Graph(demo_path)
        g.add_node("question",
                   "How should briefing context be bounded as the graph grows?",
                   created_by="user", confidence=1.0)
        g.save()
        run_turns(g, [MockWorker()], turns=3)
        show(g)
        print(f"\nDemo graph written to {demo_path}")


if __name__ == "__main__":
    main()
