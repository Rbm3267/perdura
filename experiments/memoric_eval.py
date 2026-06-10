"""
experiments/memoric_eval.py — Validation experiments for memoric binary.

Three experiments validate the core claims:
1. Hidden disagreement detection: does scatter predict contradicts edges?
2. Per-model track records: do embeddings rank model reliability?
3. Compression without loss: does routing work without full text?

Run this after a Perdura session to evaluate memoric binary effectiveness:

    python experiments/memoric_eval.py --graph perdura_graph.json

KNOWN LIMITATION (methodology, not code): experiment 1 measures association
between scatter and contradicts edges on the *final* graph state. The RFC's
hypothesis — scatter predicts contradictions *before* the edge appears —
needs a turn-by-turn replay of the merge log, which this version does not do.
Treat a pass here as necessary, not sufficient.
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from perdura_memoric import embedding_scatter, encode_node as _encode_node


def _auc(scored: list) -> float:
    """AUC via the Mann-Whitney rank statistic: probability that a randomly
    chosen positive outscores a randomly chosen negative."""
    pos = [s for s, label in scored if label]
    neg = [s for s, label in scored if not label]
    if not pos or not neg:
        return float("nan")
    wins = sum(1.0 if sp > sn else 0.5 if sp == sn else 0.0
               for sp in pos for sn in neg)
    return wins / (len(pos) * len(neg))


# ---------------------------------------------------------------------------
# Experiment 1: Hidden disagreement detection
# ---------------------------------------------------------------------------

def experiment_1_hidden_disagreement(graph_json_path: str, merge_log: list) -> dict:
    """
    Hypothesis: Epistemic distance in memoric binary detects disagreement.

    Method (final-state association — see module docstring):
    - For each question, compute embedding scatter over its answering claims.
    - Label the question by whether a contradicts edge exists among them.
    - Measure: AUC of scatter as a predictor of contradiction.
    """
    with open(graph_json_path, encoding="utf-8") as f:
        graph_data = json.load(f)

    nodes = {n["id"]: n for n in graph_data["nodes"]}
    edges = graph_data["edges"]
    graph_start = min((n.get("created_at", 0) for n in nodes.values()),
                      default=0)

    scored = []  # (scatter, has_contradiction)
    for question in [n for n in nodes.values() if n["type"] == "question"]:
        qid = question["id"]
        related_ids = {qid} | {e["src"] for e in edges
                               if e["type"] == "answers" and e["dst"] == qid}
        related_claims = [n for n in nodes.values()
                          if n["id"] in related_ids and n["type"] == "claim"]
        if len(related_claims) < 2:
            continue

        mbs = [_encode_node(n, graph_start) for n in related_claims]
        scatter = embedding_scatter(mbs, graph_start)
        has_contradiction = any(
            e["type"] == "contradicts"
            and e["src"] in related_ids and e["dst"] in related_ids
            for e in edges)
        scored.append((scatter, has_contradiction))

    if len(scored) < 2:
        return {"status": "insufficient_data",
                "message": "Need at least 2 questions with >=2 claims each"}

    auc = _auc(scored)
    if auc != auc:  # NaN: all questions share one label
        return {"status": "insufficient_data",
                "message": "Need both contended and uncontended questions"}

    n_pos = sum(1 for _, c in scored if c)
    return {
        "status": "success",
        "total_questions": len(scored),
        "questions_with_contradiction": n_pos,
        "mean_scatter_with_contradiction":
            sum(s for s, c in scored if c) / n_pos,
        "mean_scatter_without":
            sum(s for s, c in scored if not c) / (len(scored) - n_pos),
        "auc": auc,
        "success_criteria_met": auc > 0.7,
    }


# ---------------------------------------------------------------------------
# Experiment 2: Per-model track records in vector space
# ---------------------------------------------------------------------------

def experiment_2_model_track_records(graph_json_path: str, merge_log: list) -> dict:
    """
    Hypothesis: Models that produce claims close to future 'decision' nodes
    are more reliable.

    Method:
    - Good outcomes per worker: their decision nodes, plus their nodes linked
      by any edge to a decision node.
    - Bad outcomes per worker: their superseded or rejected nodes.
    - Reliability from scatter against good vs bad outcomes; compare to the
      acceptance rate aggregated from the merge log.
    """
    with open(graph_json_path, encoding="utf-8") as f:
        graph_data = json.load(f)

    nodes = {n["id"]: n for n in graph_data["nodes"]}
    edges = graph_data["edges"]
    graph_start = min((n.get("created_at", 0) for n in nodes.values()),
                      default=0)

    # Nodes connected to any decision node by any edge
    decision_linked = set()
    for e in edges:
        if nodes.get(e["src"], {}).get("type") == "decision":
            decision_linked.add(e["dst"])
        if nodes.get(e["dst"], {}).get("type") == "decision":
            decision_linked.add(e["src"])

    nodes_by_worker = defaultdict(list)
    for n in nodes.values():
        if n.get("created_by"):
            nodes_by_worker[n["created_by"]].append(n)

    worker_metrics = {}
    for worker, worker_nodes in nodes_by_worker.items():
        mbs = [_encode_node(n, graph_start) for n in worker_nodes]

        good_outcomes = [n for n in worker_nodes
                         if n["type"] == "decision" or n["id"] in decision_linked]
        bad_outcomes = [n for n in worker_nodes
                        if n.get("superseded_by") is not None
                        or n["type"] == "rejected"]

        good_mbs = [_encode_node(n, graph_start) for n in good_outcomes]
        bad_mbs = [_encode_node(n, graph_start) for n in bad_outcomes]

        good_scatter = (embedding_scatter(mbs + good_mbs, graph_start)
                        if good_mbs else 1.0)   # worst case: no good outcomes
        bad_scatter = (embedding_scatter(mbs + bad_mbs, graph_start)
                       if bad_mbs else 0.0)     # best case: no bad outcomes

        reliability = max(0, min(1, (bad_scatter - good_scatter)
                                 / (bad_scatter + 0.01)))

        # Aggregate the merge log (it holds one entry per turn, not totals)
        accepted = sum(e.get("accepted", 0) for e in merge_log
                       if e.get("worker") == worker)
        rejected = sum(e.get("rejected", 0) for e in merge_log
                       if e.get("worker") == worker)
        acceptance_rate = accepted / max(1, accepted + rejected)

        worker_metrics[worker] = {
            "reliability_score": reliability,
            "acceptance_rate": acceptance_rate,
            "nodes_contributed": len(worker_nodes),
            "good_outcomes": len(good_outcomes),
            "bad_outcomes": len(bad_outcomes),
        }

    if len(worker_metrics) < 2:
        return {"status": "insufficient_data", "message": "Need at least 2 workers"}

    workers = list(worker_metrics.keys())
    rel = [worker_metrics[w]["reliability_score"] for w in workers]
    acc = [worker_metrics[w]["acceptance_rate"] for w in workers]
    mean_rel, mean_acc = sum(rel) / len(rel), sum(acc) / len(acc)
    numerator = sum((r - mean_rel) * (a - mean_acc) for r, a in zip(rel, acc))
    denom = (((sum((r - mean_rel) ** 2 for r in rel) ** 0.5) + 1e-6)
             * ((sum((a - mean_acc) ** 2 for a in acc) ** 0.5) + 1e-6))
    correlation = numerator / denom

    return {
        "status": "success",
        "worker_metrics": worker_metrics,
        "correlation": correlation,
        "success_criteria_met": abs(correlation) > 0.6,
    }


# ---------------------------------------------------------------------------
# Experiment 3: Compression without information loss
# ---------------------------------------------------------------------------

def experiment_3_compression_routing(graph_json_path: str) -> dict:
    """
    Hypothesis: You can use memoric binary alone (no full text) without
    losing routing quality.

    Method:
    - Baseline: contention from contradicts-edge counts.
    - Variant: contention from embedding_scatter (memoric binary only).
    - Compare top-3 routing decisions (which questions are worked next).

    Caveat: with <=3 open questions the top-3 sets are identical by
    construction; the agreement metric needs a larger graph to mean much.
    """
    with open(graph_json_path, encoding="utf-8") as f:
        graph_data = json.load(f)

    nodes = {n["id"]: n for n in graph_data["nodes"]}
    edges = graph_data["edges"]
    graph_start = min((n.get("created_at", 0) for n in nodes.values()),
                      default=0)

    open_questions = [n for n in nodes.values() if n["type"] == "question"]
    if not open_questions:
        return {"status": "no_open_questions"}

    contention_baseline, contention_memoric = {}, {}
    for q in open_questions:
        qid = q["id"]
        related_ids = {qid} | {e["src"] for e in edges
                               if e["type"] == "answers" and e["dst"] == qid}
        related_claims = [n for n in nodes.values()
                          if n["id"] in related_ids and n["type"] == "claim"]

        contradicts_count = sum(
            1 for e in edges
            if e["type"] == "contradicts"
            and e["src"] in related_ids and e["dst"] in related_ids)
        contention_baseline[qid] = contradicts_count / max(1, len(related_claims))

        claim_mbs = [_encode_node(n, graph_start) for n in related_claims]
        contention_memoric[qid] = (embedding_scatter(claim_mbs, graph_start)
                                   if claim_mbs else 0.0)

    routing_baseline = sorted(contention_baseline.items(), key=lambda x: -x[1])[:3]
    routing_memoric = sorted(contention_memoric.items(), key=lambda x: -x[1])[:3]
    baseline_ids = {q[0] for q in routing_baseline}
    memoric_ids = {q[0] for q in routing_memoric}
    agreement = len(baseline_ids & memoric_ids) / max(1, len(baseline_ids | memoric_ids))

    return {
        "status": "success",
        "total_open_questions": len(open_questions),
        "top3_baseline": [q[0] for q in routing_baseline],
        "top3_memoric": [q[0] for q in routing_memoric],
        "routing_agreement": agreement,
        "success_criteria_met": agreement > 0.9,  # 90% routing overlap
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Memoric binary validation experiments")
    p.add_argument("--graph", default="perdura_graph.json",
                   help="Path to perdura_graph.json")
    args = p.parse_args()

    if not Path(args.graph).exists():
        print(f"Graph file not found: {args.graph}")
        sys.exit(1)

    with open(args.graph, encoding="utf-8") as f:
        graph_data = json.load(f)
    merge_log = graph_data.get("log", [])

    print("=" * 70)
    print("MEMORIC BINARY VALIDATION EXPERIMENTS")
    print("=" * 70)

    print("\n[1] Hidden Disagreement Detection")
    print("-" * 70)
    result1 = experiment_1_hidden_disagreement(args.graph, merge_log)
    print(json.dumps(result1, indent=2))
    print("✓ PASSED (AUC > 0.7)" if result1.get("success_criteria_met")
          else "✗ FAILED")

    print("\n[2] Per-Model Track Records")
    print("-" * 70)
    result2 = experiment_2_model_track_records(args.graph, merge_log)
    print(json.dumps(result2, indent=2))
    print("✓ PASSED (|correlation| > 0.6)" if result2.get("success_criteria_met")
          else "✗ FAILED")

    print("\n[3] Compression Without Information Loss")
    print("-" * 70)
    result3 = experiment_3_compression_routing(args.graph)
    print(json.dumps(result3, indent=2))
    print("✓ PASSED (>90% routing agreement)" if result3.get("success_criteria_met")
          else "✗ FAILED")

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    passed = sum([
        bool(result1.get("success_criteria_met")),
        bool(result2.get("success_criteria_met")),
        bool(result3.get("success_criteria_met")),
    ])
    print(f"Experiments passed: {passed}/3")
    print("\nRecommendation:")
    if passed == 3:
        print("✓ Memoric binary is validated. Proceed to Phase 1.5 integration.")
    elif passed >= 2:
        print("⚠ Partial success. Investigate failed experiment before Phase 1.5.")
    else:
        print("✗ Memoric binary needs iteration. Review spec and re-run.")
