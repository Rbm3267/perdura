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
    # local labor: LM Studio serving qwen3-14b (default), or Ollama via
    # --qwen-url http://localhost:11434/v1 --qwen-model qwen3:14b

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
import re
import sys
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, asdict, field

from perdura_store import store_for

try:
    import fcntl
except ImportError:          # Windows: no advisory locks; single writer only
    fcntl = None


@contextmanager
def graph_write_lock(path):
    """Advisory write lock shared with the MCP station (same lockfile).
    Writers must reload-merge-save inside it so concurrent conductors and
    station workers never lose updates (issue #10)."""
    if fcntl is None:
        yield
        return
    with open(path + ".lock", "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)

# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------

NODE_TYPES = {"question", "claim", "evidence", "decision", "rejected"}
EDGE_TYPES = {"supports", "contradicts", "refines", "answers", "depends_on"}

# Default worker models — pinned here and nowhere else (issue #8).
# gemini-2.5-flash verified still current against the live model list
# 2026-06-10; bump claude alongside provider releases.
DEFAULT_CLAUDE_MODEL = "claude-sonnet-4-6"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_QWEN_MODEL = "qwen3-14b"
DEFAULT_QWEN_URL = "http://localhost:1234/v1"

# Conductor tuning
NEAR_DUP_HAMMING = 8   # simhash distance at/below which claims are linked (#2)
EXPLORE_EVERY = 3      # every Nth turn boards the least-worked question (#9)


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
        # Store chosen by extension: .db/.sqlite[3] = SQLite (WAL), else
        # JSON file. Same lock, same reload-merge-save discipline either way.
        self.store = store_for(path)
        self.nodes: dict[str, Node] = {}
        self.edges: dict[str, Edge] = {}
        self.log: list = []  # merge log: (ts, worker, accepted, rejected)
        # Default tracks Phase 0 status (issue #11): the memoric blend is
        # the thing under test, so it stays out of the default routing
        # signal until experiments 1 and 3 clear their bars
        # (docs/phase0-validation.md). Opt in with --memoric-weight 0.5.
        self.memoric_weight: float = 0.0
        if self.store.exists():
            self._load()

    # -- persistence --------------------------------------------------------
    def _load(self):
        data = self.store.load()
        self.nodes = {n["id"]: Node(**n) for n in data["nodes"]}
        self.edges = {e["id"]: Edge(**e) for e in data["edges"]}
        self.log = data.get("log", [])

    def save(self):
        # Saves are atomic per store (write-rename for JSON, one transaction
        # for SQLite) so concurrent readers (e.g. MCP workers boarding
        # mid-merge) never see a truncated graph.
        self.store.save([asdict(n) for n in self.nodes.values()],
                        [asdict(e) for e in self.edges.values()],
                        self.log)

    # -- mutation (conductor-only) ------------------------------------------
    def add_node(self, type, text, created_by, confidence=0.5,
                 domain_tags=None, status="open"):
        nid = f"n_{uuid.uuid4().hex[:8]}"
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):  # models emit "high", null, etc.
            confidence = 0.5
        self.nodes[nid] = Node(
            id=nid, type=type, text=text.strip(),
            domain_tags=domain_tags or [], created_by=created_by,
            confidence=max(0.0, min(1.0, confidence)),
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

    def contention(self, node_ids=None, memoric_weight=None):
        """contradicts-edges per claim, confidence-weighted. The routing signal.

        memoric_weight (or self.memoric_weight, default 0.0) blends in
        embedding scatter from memoric binary, computed on demand:
        (1-w)*edge_signal + w*scatter. Pass 0 to reproduce the original
        edge-only metric bit-for-bit; the encoding is derived state and
        is never persisted (docs/memoric-binary.md §6.1).
        """
        w = self.memoric_weight if memoric_weight is None else memoric_weight
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
        edge_signal = contra / len(claims)
        if not w:
            return round(edge_signal, 3)
        # Lazy import: the core loop has no Phase 0 dependency unless opted in
        from perdura_memoric import embedding_scatter, encode_node
        start = min((n.created_at for n in self.nodes.values()), default=0.0)
        scatter = embedding_scatter([encode_node(n, start) for n in claims])
        return round((1 - w) * edge_signal + w * scatter, 3)


# ---------------------------------------------------------------------------
# Briefing + delta extraction
# ---------------------------------------------------------------------------

BRIEFING_CHAR_BUDGET = 6000  # bounded regardless of graph size

# Adversarial boarding (devil's-advocate critic). Homogeneous frontier
# workers converge — the real session produced 1 contradicts edge in 18
# turns (docs/phase0-validation.md). This prompt block deterministically
# manufactures contention: the worker must attack the strongest live claim
# rather than extend it. Injected on every Nth turn via --adversarial-every.
ADVERSARIAL_PREAMBLE = """\
You are boarding as a CRITIC this turn. Consensus is a failure mode here —
your job is to stress-test, not to agree.

- Find the single strongest or highest-confidence claim in the briefing that
  you can legitimately challenge. Add a claim stating the counter-position
  AND a "contradicts" edge from your claim to it. Do not manufacture a
  disagreement you don't believe — but if the claim overreaches, is
  underspecified, or ignores a real failure case, say so explicitly.
- If a claim is sound but oversold, contribute a lower-confidence refinement
  that names its limits (a "refines" edge), rather than rubber-stamping it.
- Do NOT add new supporting claims or new questions this turn. Pressure the
  existing graph.

"""


AUDIT_PROMPT = """\
You are boarding as a STANCE AUDITOR for a persistent knowledge graph.
Below are pairs of claims that are topically close but UNLINKED — written
independently by different workers. Lexical collision located them; only
stance judgment can classify them. For each pair, decide the relationship:

- Opposing positions -> add a "contradicts" edge between them.
- One strengthens or sharpens the other -> add a "supports" or "refines" edge.
- Merely sharing vocabulary, no real relation -> add nothing for that pair.

Respond with ONLY a JSON object, no markdown fences, no prose:

{{
  "new_nodes": [],
  "new_edges": [
    {{"type": "contradicts|supports|refines",
      "src": "existing node id", "dst": "existing node id"}}
  ],
  "supersedes": [],
  "resolve_questions": []
}}

Add NO new nodes. Use only the node ids shown below.

CLAIM PAIRS
===========
{pairs}
"""


def build_audit_briefing(pairs):
    """Briefing for a stance-audit turn: collision-band claim pairs."""
    blocks = [f"{a.id} | {' '.join((a.text or '').split())}\n"
              f"{b.id} | {' '.join((b.text or '').split())}" for a, b in pairs]
    return AUDIT_PROMPT.format(pairs="\n\n".join(blocks))


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

{adversarial}BRIEFING
========
Open question under consideration:
{question}

Related nodes (id | type | confidence | text):
{nodes}

Existing edges among them:
{edges}
"""


def build_briefing(graph: Graph, question: Node, retriever=None,
                   mask_confidence=False, adversarial=False):
    # Phase 1.5: candidate selection is pluggable. Default reproduces the
    # Phase 1 behavior exactly (2-hop neighborhood + open questions).
    if retriever is None:
        from perdura_retrieval import GraphRetriever
        retriever = GraphRetriever()
    ids = set(retriever.retrieve(graph, question))
    # Global guardrails (issue #2): live decisions are always in scope —
    # a fresh, structurally isolated question still inherits settled policy.
    ids |= {n.id for n in graph.live_nodes() if n.type == "decision"}
    nodes = [graph.nodes[i] for i in ids
             if graph.nodes[i].superseded_by is None]
    nodes.sort(key=lambda n: -n.confidence)

    lines, used = [], 0
    for n in nodes:
        text = " ".join(n.text.split())  # keep the one-line briefing format
        # Masked variant for the anchoring experiment (issue #6): workers
        # see content but neither authorship nor confidence.
        conf = "--" if mask_confidence else f"{n.confidence:.2f}"
        line = f"{n.id} | {n.type} | {conf} | {text}"
        if used + len(line) > BRIEFING_CHAR_BUDGET:
            break
        lines.append(line)
        used += len(line)
    shown = {l.split(" | ")[0] for l in lines}
    # Edge section is bounded too (a third of the node budget) — retrieval
    # can surface nodes from across the graph, and the boundedness invariant
    # covers the whole briefing, not just node lines. contradicts edges are
    # the contention signal, so they survive truncation first.
    edge_lines, eused = [], 0
    for e in sorted(graph.edges.values(),
                    key=lambda e: e.type != "contradicts"):
        if e.src in shown and e.dst in shown:
            line = f"{e.src} -[{e.type}]-> {e.dst}"
            if eused + len(line) > BRIEFING_CHAR_BUDGET // 3:
                break
            edge_lines.append(line)
            eused += len(line)
    return DELTA_PROMPT.format(
        adversarial=ADVERSARIAL_PREAMBLE if adversarial else "",
        question=f"{question.id}: {question.text}",
        nodes="\n".join(lines) or "(none yet)",
        edges="\n".join(edge_lines) or "(none yet)")


def parse_delta(raw: str):
    """Tolerant JSON *extraction* (schema validation stays in merge_delta).

    Handles the failure modes small local models actually produce:
    reasoning blocks (qwen3 emits <think>...</think> before the JSON),
    markdown fences, surrounding prose, and trailing commas.
    """
    s = re.sub(r"<think>.*?(</think>|$)", "", raw, flags=re.DOTALL)
    s = re.sub(r"```(?:json)?", "", s).strip()

    # Try each '{' as a candidate start; raw_decode stops at the matching
    # close brace, so prose before/after and braces inside strings are fine.
    decoder = json.JSONDecoder()
    last_err = None
    for m in re.finditer(r"\{", s):
        candidate = s[m.start():]
        # Trailing-comma repair is a last resort: the regex can't see string
        # boundaries, so text like "yes, }" inside a value could be altered.
        # It only runs after a strict parse failed, and a wrong repair still
        # has to survive merge_delta's schema validation.
        for attempt in (candidate,
                        re.sub(r",\s*([}\]])", r"\1", candidate)):
            try:
                obj, _ = decoder.raw_decode(attempt)
            except json.JSONDecodeError as e:
                last_err = e
                continue
            if isinstance(obj, dict):
                return obj
    raise ValueError(f"no JSON object found ({last_err or 'no braces'})")


REPAIR_PROMPT = """\
Your previous reply could not be parsed as a delta ({error}).
Reply again with ONLY the JSON object — no <think> block, no markdown
fences, no prose — matching exactly the schema you were given.

Previous reply:
{raw}
"""


def merge_delta(graph: Graph, delta: dict, worker: str):
    """Conductor: validate, attribute, merge. Returns (accepted, rejected)."""
    accepted, rejected = 0, 0
    ref_map = {}

    def items(key):
        """Field may be null, missing, or a non-list; elements non-dicts."""
        val = delta.get(key)
        return val if isinstance(val, list) else []

    for n in items("new_nodes"):
        if (not isinstance(n, dict) or n.get("type") not in NODE_TYPES
                or not isinstance(n.get("text"), str) or not n["text"].strip()):
            rejected += 1
            continue
        nid = graph.add_node(
            type=n["type"], text=n["text"], created_by=worker,
            confidence=n.get("confidence", 0.5),
            domain_tags=n.get("domain_tags", []))
        ref = n.get("ref")
        ref_map[ref if isinstance(ref, str) else nid] = nid
        accepted += 1

    def resolve(ref):
        if not isinstance(ref, str):
            return None
        return ref_map.get(ref, ref if ref in graph.nodes else None)

    for e in items("new_edges"):
        if not isinstance(e, dict):
            rejected += 1
            continue
        src, dst = resolve(e.get("src")), resolve(e.get("dst"))
        if e.get("type") in EDGE_TYPES and src and dst and src != dst:
            graph.add_edge(e["type"], src, dst, worker)
            accepted += 1
        else:
            rejected += 1

    for s in items("supersedes"):
        if not isinstance(s, dict):
            continue
        old, new = s.get("old"), resolve(s.get("new"))
        if isinstance(old, str) and old in graph.nodes and new:
            graph.supersede(old, new)

    for qid in items("resolve_questions"):
        if (isinstance(qid, str) and qid in graph.nodes
                and graph.nodes[qid].type == "question"):
            graph.nodes[qid].status = "resolved"

    # Near-duplicate consolidation (issue #2): deterministic, conductor-
    # authored. A new claim within NEAR_DUP_HAMMING simhash bits of an
    # existing live claim still merges (nothing is suppressed), but gains a
    # refines edge to its nearest neighbor so paraphrases form one
    # structure instead of parallel tracks.
    new_claims = {nid for nid in ref_map.values()
                  if graph.nodes[nid].type == "claim"}
    if new_claims:
        from perdura_memoric import simhash_48bits
        hashes = {n.id: simhash_48bits(n.text)
                  for n in graph.live_nodes() if n.type == "claim"}
        linked = {(e.src, e.dst) for e in graph.edges.values()}
        for nid in new_claims:
            h = hashes.get(nid)
            best, best_d = None, NEAR_DUP_HAMMING + 1
            for oid, oh in hashes.items():
                if oid == nid or oid in new_claims:
                    continue
                d = (h ^ oh).bit_count()
                if d < best_d:
                    best, best_d = oid, d
            if best and best_d <= NEAR_DUP_HAMMING and (nid, best) not in linked:
                graph.add_edge("refines", nid, best, "conductor")

    graph.log.append({"ts": time.time(), "worker": worker,
                      "accepted": accepted, "rejected": rejected})
    return accepted, rejected


# ---------------------------------------------------------------------------
# Workers (ephemeral — note: no per-worker state anywhere)
# ---------------------------------------------------------------------------

class ClaudeWorker:
    name = "claude"

    def __init__(self, model=DEFAULT_CLAUDE_MODEL):
        import anthropic
        self.client, self.model = anthropic.Anthropic(), model

    def generate(self, prompt):
        r = self.client.messages.create(model=self.model, max_tokens=1500,
                                        messages=[{"role": "user",
                                                   "content": prompt}])
        return r.content[0].text


class GeminiWorker:
    name = "gemini"

    def __init__(self, model=DEFAULT_GEMINI_MODEL):
        from google import genai
        # Explicit key: the client's env auto-detection can construct a
        # closed transport when other Google creds are half-present.
        self.client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
        self.model = model

    def generate(self, prompt):
        return self.client.models.generate_content(
            model=self.model, contents=prompt).text


class QwenWorker:
    """Local model via any OpenAI-compatible server.

    Defaults target LM Studio (http://localhost:1234/v1, model "qwen3-14b").
    For Ollama: --qwen-url http://localhost:11434/v1 --qwen-model qwen3:14b
    """
    name = "qwen"

    def __init__(self, model=DEFAULT_QWEN_MODEL, base_url=DEFAULT_QWEN_URL):
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

def run_turns(graph: Graph, workers: list, turns: int, retriever=None,
              mask_confidence=False, adversarial_every=0, audit_every=0):
    """Round-robin boarding (Phase 3 replaces this with the router)."""
    for t in range(turns):
        worker = workers[t % len(workers)]
        # Stance-audit turn (the inversion finding): the collision detector
        # locates topically-close unlinked claim pairs deterministically; a
        # cheap worker judges agree-vs-oppose — the one thing a hash can't.
        # Its edges-only delta goes through the normal validate/merge.
        if audit_every > 0 and (t + 1) % audit_every == 0:
            from perdura_memoric import collision_candidates
            pairs = collision_candidates(graph)
            if pairs:
                print(f"\n[turn {t+1}] {worker.name} boards as stance "
                      f"auditor ({len(pairs)} collision pairs)")
                briefing = build_audit_briefing(pairs)
                _board(graph, worker, briefing)
                continue

        questions = graph.open_questions()
        if not questions:
            print("No open questions — the train is at rest.")
            break
        explore = (t + 1) % EXPLORE_EVERY == 0 and len(questions) > 1
        if explore:
            # Exploration turn (issue #9): pure most-contended-first starves
            # fresh questions (no claims -> contention 0 -> never boarded).
            # Bandit placeholder until the Phase 3 router owns selection.
            answer_counts = {}
            for e in graph.edges.values():
                if e.type == "answers":
                    answer_counts[e.dst] = answer_counts.get(e.dst, 0) + 1
            questions.sort(
                key=lambda q: (answer_counts.get(q.id, 0), q.created_at))
        else:
            questions.sort(
                key=lambda q: -graph.contention(graph.neighborhood(q.id)))
        q = questions[0]
        # Adversarial turn: board as a critic to manufacture contention that
        # homogeneous workers won't produce on their own.
        adversarial = adversarial_every > 0 and (t + 1) % adversarial_every == 0

        flags = (" (explore)" if explore else "") + \
                (" (adversarial)" if adversarial else "")
        print(f"\n[turn {t+1}] {worker.name} boards for {q.id}{flags}: "
              f"{q.text[:60]}...")
        briefing = build_briefing(graph, q, retriever,
                                  mask_confidence=mask_confidence,
                                  adversarial=adversarial)
        _board(graph, worker, briefing)


def _board(graph: Graph, worker, briefing):
    """Generate, parse (with one repair round), merge under lock."""
    try:
        raw = worker.generate(briefing)
        try:
            delta = parse_delta(raw)
        except ValueError as e:
            # One repair round; schema validation in merge_delta is
            # unchanged — we only re-ask for parseable output.
            print(f"  unparseable delta ({e}); retrying with repair prompt")
            delta = parse_delta(worker.generate(
                briefing + "\n\n" + REPAIR_PROMPT.format(
                    error=e, raw=raw[:2000])))
        # Generation ran outside the lock; the merge reloads under it so
        # a concurrent conductor or MCP station never loses our delta,
        # nor we theirs (issue #10).
        with graph_write_lock(graph.path):
            fresh = Graph(graph.path)
            fresh.memoric_weight = graph.memoric_weight
            acc, rej = merge_delta(fresh, delta, worker.name)
            fresh.save()
        graph.nodes, graph.edges, graph.log = (
            fresh.nodes, fresh.edges, fresh.log)
        print(f"  merged: {acc} accepted, {rej} rejected | "
              f"global contention: {graph.contention()}")
    except Exception as e:
        with graph_write_lock(graph.path):
            fresh = Graph(graph.path)
            fresh.log.append({"ts": time.time(), "worker": worker.name,
                              "accepted": 0, "rejected": 0,
                              "error": str(e)[:200]})
            fresh.save()
        graph.nodes, graph.edges, graph.log = (
            fresh.nodes, fresh.edges, fresh.log)
        print(f"  delta rejected entirely ({e})")


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

    # Per-claim outcome lineage (issue #7) — the substrate Phase 2 track
    # records compute from: what HAPPENED to each worker's claims after
    # merge, not just whether they merged.
    contradicted, promoted = set(), set()
    decision_ids = {n.id for n in graph.nodes.values() if n.type == "decision"}
    for e in graph.edges.values():
        if e.type == "contradicts":
            contradicted.update((e.src, e.dst))
        if e.src in decision_ids:
            promoted.add(e.dst)
        if e.dst in decision_ids:
            promoted.add(e.src)
    lineage = {}
    for n in graph.nodes.values():
        if n.type != "claim" or not n.created_by:
            continue
        L = lineage.setdefault(n.created_by, {"claims": 0, "superseded": 0,
                                              "contradicted": 0, "promoted": 0})
        L["claims"] += 1
        L["superseded"] += n.superseded_by is not None
        L["contradicted"] += n.id in contradicted
        L["promoted"] += n.id in promoted
    if lineage:
        print("\nClaim outcome lineage (Phase 2 substrate):")
        for w, L in sorted(lineage.items()):
            print(f"  {w}: {L['claims']} claims — {L['promoted']} promoted, "
                  f"{L['contradicted']} contradicted, "
                  f"{L['superseded']} superseded")


def main():
    p = argparse.ArgumentParser(description="Perdura Phase 1")
    p.add_argument("command", choices=["new", "run", "show", "demo", "viz",
                                       "track", "ui", "redact"])
    p.add_argument("text", nargs="?",
                   help="question text (for `new`) / node id (for `redact`)")
    p.add_argument("--graph", default="perdura_graph.json",
                   help="graph path; .db/.sqlite[3] extension selects the "
                        "SQLite store (WAL) instead of the JSON file")
    p.add_argument("--out", default="perdura_mindmap.html",
                   help="output path (for `viz`)")
    p.add_argument("--port", type=int, default=8800,
                   help="port for the Station dashboard (for `ui`)")
    p.add_argument("--turns", type=int, default=6)
    p.add_argument("--workers", default="qwen,claude,gemini",
                   help="comma list: qwen,claude,gemini,mock")
    p.add_argument("--claude-model", default=DEFAULT_CLAUDE_MODEL)
    p.add_argument("--gemini-model", default=DEFAULT_GEMINI_MODEL)
    p.add_argument("--qwen-model", default=DEFAULT_QWEN_MODEL,
                   help="local model id as served (LM Studio: qwen3-14b; "
                        "Ollama: qwen3:14b)")
    p.add_argument("--qwen-url", default=DEFAULT_QWEN_URL,
                   help="OpenAI-compatible base URL (LM Studio default; "
                        "Ollama: http://localhost:11434/v1)")
    p.add_argument("--memoric-weight", type=float, default=0.0,
                   help="contention blend (1-w)*edges + w*memoric scatter. "
                        "Default 0.0 (edge-only) until Phase 0 validation "
                        "passes — see docs/phase0-validation.md")
    p.add_argument("--mask-confidence", action="store_true",
                   help="hide confidence scores from worker briefings "
                        "(anchoring experiment, issue #6)")
    p.add_argument("--audit-every", type=int, default=0, metavar="N",
                   help="every Nth turn, a stance auditor classifies "
                        "collision-band claim pairs (agree/oppose) — "
                        "surfaces ORGANIC latent disagreement (0 = off)")
    p.add_argument("--adversarial-every", type=int, default=0, metavar="N",
                   help="every Nth turn, board as a devil's-advocate critic "
                        "to manufacture contention (0 = off). Counters the "
                        "consensus collapse seen with homogeneous workers — "
                        "see docs/phase0-validation.md")
    p.add_argument("--retriever", choices=["graph", "hybrid", "chroma"],
                   default="graph",
                   help="briefing candidate selection (Phase 1.5): graph = "
                        "2-hop neighborhood (default/baseline), hybrid = "
                        "BM25 + dense + expansion, chroma = hybrid with a "
                        "persistent ChromaDB index")
    args = p.parse_args()

    graph = Graph(args.graph)
    graph.memoric_weight = max(0.0, min(1.0, args.memoric_weight))

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
        from perdura_retrieval import RETRIEVERS
        run_turns(graph, workers, args.turns,
                  retriever=RETRIEVERS[args.retriever](),
                  mask_confidence=args.mask_confidence,
                  adversarial_every=args.adversarial_every,
                  audit_every=args.audit_every)
        show(graph)

    elif args.command == "show":
        show(graph)

    elif args.command == "viz":
        from perdura_viz import write
        print(f"Mind map written to {write(graph, args.out)}")

    elif args.command == "track":
        from perdura_track import scorecard
        print(scorecard(graph))

    elif args.command == "ui":
        from perdura_station import serve
        serve(args.graph, port=args.port)

    elif args.command == "redact":
        # Operator-only compliance escape hatch (docs/enterprise.md): the
        # node's text payload is destroyed; type, confidence, attribution,
        # edges, and supersession lineage all survive, so the epistemic
        # record stays intact. Memoric encodings are derived on demand, so
        # no stale copy of the text outlives this.
        if not args.text:
            sys.exit("Provide the node id: perdura.py redact n_xxxxxxxx")
        with graph_write_lock(args.graph):
            g = Graph(args.graph)
            node = g.nodes.get(args.text)
            if node is None:
                sys.exit(f"No such node: {args.text}")
            node.text = "[redacted]"
            g.log.append({"ts": time.time(), "worker": "operator",
                          "accepted": 0, "rejected": 0, "redacted": node.id})
            g.save()
        print(f"Redacted {args.text}: text destroyed; structure, "
              f"attribution, and lineage preserved.")

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
