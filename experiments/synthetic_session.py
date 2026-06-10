"""
experiments/synthetic_session.py — Controlled session generator for Phase 0.

Builds a Perdura graph with KNOWN ground truth so the memoric binary
validation experiments measure against planted signal rather than vibes:

- Half the questions are CONTENDED: two camps assert lexically distinct,
  confidence-asymmetric positions, and explicit `contradicts` edges arrive
  only AFTER several claims exist (so experiment 1's temporal replay has
  genuine pre-contradiction checkpoints to predict from).
- Half are CONSENSUS: paraphrased agreeing claims, supports edges, and a
  decision node that promotes the reliable workers' claims.
- Three synthetic workers with planted reliability (good / mid / poor):
  the good worker's claims end up decision-linked, the poor worker's end up
  superseded or rejected, and per-turn merge-log acceptance rates mirror
  the same ordering — ground truth for experiment 2's correlation.

This is the NECESSARY-condition arm: it shows the encoding can detect
disagreement the mechanism was designed for. The sufficient-condition arm
is a real multi-model session (run locally where API keys live):

    python perdura.py new "..." ; python perdura.py run --turns 24
    python experiments/memoric_eval.py --graph perdura_graph.json

Usage:
    python experiments/synthetic_session.py --out /tmp/synthetic_graph.json
"""

import argparse
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from perdura import Graph

WORKERS = [
    ("sim-good", 0.92),   # planted reliability: claims get promoted
    ("sim-mid", 0.65),
    ("sim-poor", 0.30),   # claims get superseded/rejected
]

# (subject, consensus policy, camp A policy, camp B policy, domain)
TOPICS = [
    ("briefing assembly", "confidence ranking", "recency windows", "hub centrality", "architecture"),
    ("graph compaction", "supersede chains", "merge summarization", "hard pruning", "architecture"),
    ("worker escalation", "contention thresholds", "fixed cadence rotation", "cost ceilings", "economics"),
    ("track records", "outcome attribution", "self-reported confidence", "vote counting", "epistemology"),
    ("retrieval", "hybrid BM25 and dense search", "pure graph expansion", "exact keyword match", "engineering"),
    ("schema validation", "strict rejection", "lenient coercion", "schema-free merging", "engineering"),
    ("memory encoding", "bit-packed provenance", "full dense embeddings", "raw text storage", "research"),
    ("confidence calibration", "supersession feedback", "static priors", "majority smoothing", "epistemology"),
    ("question routing", "contention ordering", "round robin", "oldest first", "economics"),
    ("evidence weighting", "source-linked nodes", "uniform weights", "recency decay", "research"),
    ("conductor design", "deterministic merging", "LLM-mediated merging", "human review gates", "design"),
    ("domain tagging", "fixed bitmaps", "free-form labels", "learned clusters", "design"),
]

AGREE_TEMPLATES = [
    "{subject} should rely on {policy}.",
    "For {subject}, {policy} is the right default.",
    "Adopting {policy} keeps {subject} robust as the graph grows.",
    "Evidence from prior sessions favors {policy} for {subject}.",
    "{policy} handles {subject} with the least operational risk.",
]

CAMP_TEMPLATES = [
    "{subject} works best with {policy}.",
    "{policy} should drive {subject} going forward.",
    "The strongest approach to {subject} is {policy}.",
    "{policy} outperforms the alternatives for {subject}.",
]


def _tick():
    """Advance wall-clock so created_at ordering is strict."""
    time.sleep(0.002)


def _log_turn(g: Graph, worker: str, reliability: float, accepted: int, rng):
    rejected = sum(1 for _ in range(2) if rng.random() > reliability)
    g.log.append({"ts": time.time(), "worker": worker,
                  "accepted": accepted, "rejected": rejected})


def build(path: str, seed: int = 7) -> Graph:
    rng = random.Random(seed)
    p = Path(path)
    if p.exists():
        p.unlink()
    g = Graph(path)

    for i, (subject, consensus, camp_a, camp_b, domain) in enumerate(TOPICS):
        contended = i % 2 == 0
        qid = g.add_node("question",
                         f"What approach should {subject} take?",
                         created_by="user", confidence=1.0,
                         domain_tags=[domain])
        _tick()

        if contended:
            # Two camps, lexically distinct policies, asymmetric confidence.
            camp_nodes: dict[str, list[str]] = {"a": [], "b": []}
            order = ["a", "b", "a", "b", "a"][: rng.randint(4, 5)]
            for j, camp in enumerate(order):
                worker, rel = WORKERS[(i + j) % len(WORKERS)]
                policy = camp_a if camp == "a" else camp_b
                conf = (rng.uniform(0.78, 0.92) if camp == "a"
                        else rng.uniform(0.45, 0.62))
                text = rng.choice(CAMP_TEMPLATES).format(
                    subject=subject, policy=policy)
                nid = g.add_node("claim", text, created_by=worker,
                                 confidence=conf, domain_tags=[domain])
                g.add_edge("answers", nid, qid, worker)
                camp_nodes[camp].append(nid)
                _log_turn(g, worker, rel, accepted=2, rng=rng)
                _tick()
            # contradicts edges arrive only now — AFTER the claims existed,
            # giving experiment 1 pre-contradiction checkpoints to predict.
            for a_id in camp_nodes["a"][:2]:
                for b_id in camp_nodes["b"][:2]:
                    if rng.random() < 0.75:
                        g.add_edge("contradicts", a_id, b_id,
                                   rng.choice(WORKERS)[0])
                        _tick()
        else:
            # Consensus: paraphrases of one position, tight confidence band.
            claim_ids, claim_workers = [], []
            templates = rng.sample(AGREE_TEMPLATES, rng.randint(3, 4))
            for j, tpl in enumerate(templates):
                worker, rel = WORKERS[(i + j) % len(WORKERS)]
                nid = g.add_node("claim",
                                 tpl.format(subject=subject, policy=consensus),
                                 created_by=worker,
                                 confidence=rng.uniform(0.62, 0.78),
                                 domain_tags=[domain])
                g.add_edge("answers", nid, qid, worker)
                claim_ids.append(nid)
                claim_workers.append(worker)
                _log_turn(g, worker, rel, accepted=2, rng=rng)
                _tick()
            g.add_edge("supports", claim_ids[1], claim_ids[0], claim_workers[1])
            _tick()

            # Decision promotes the good worker's claims (exp 2 ground truth)
            did = g.add_node("decision",
                             f"Adopt {consensus} for {subject}.",
                             created_by="sim-good", confidence=0.85,
                             domain_tags=[domain])
            _tick()
            for nid, w in zip(claim_ids, claim_workers):
                if w in ("sim-good", "sim-mid"):
                    g.add_edge("depends_on", did, nid, "sim-good")
                    _tick()

            # The poor worker's consensus claim gets superseded (bad outcome)
            for nid, w in zip(claim_ids, claim_workers):
                if w == "sim-poor":
                    rid = g.add_node("claim",
                                     f"Refined: {consensus} for {subject}, "
                                     f"with explicit failure handling.",
                                     created_by="sim-good", confidence=0.8,
                                     domain_tags=[domain])
                    g.add_edge("refines", rid, nid, "sim-good")
                    g.supersede(nid, rid)
                    _tick()

    # A few rejected-path nodes from the poor worker (exp 2 bad outcomes)
    for k in range(3):
        g.add_node("rejected",
                   f"Abandoned: global re-embedding pass #{k} — cost grows "
                   f"with graph size and duplicates derived state.",
                   created_by="sim-poor", confidence=0.3,
                   domain_tags=["architecture"])
        _tick()

    g.save()
    return g


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Synthetic Phase 0 session")
    ap.add_argument("--out", default="/tmp/synthetic_graph.json")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    g = build(args.out, args.seed)
    live = g.live_nodes()
    contras = sum(1 for e in g.edges.values() if e.type == "contradicts")
    print(f"Synthetic session: {len(live)} live nodes "
          f"({len(g.nodes)} total), {len(g.edges)} edges "
          f"({contras} contradicts), {len(g.log)} log turns → {args.out}")
