"""
experiments/memoric_eval.py — Validation experiments for memoric binary.

Three experiments validate the core claims:
1. Hidden disagreement detection: does scatter predict contradicts edges?
2. Per-model track records: do embeddings rank model reliability?
3. Compression without loss: does routing work without full text?

Run this after a Perdura session to evaluate memoric binary effectiveness:

    python experiments/memoric_eval.py --graph perdura_graph.json
    python experiments/memoric_eval.py --graph perdura_graph.json --semantic simhash

Experiment 1 replays each question's claims in arrival order and tests
whether scatter predicts contradictions *before* the explicit edge exists
(the RFC's actual hypothesis). --semantic switches the 48-bit semantic
hash between blake2b (RFC v0.1 spec) and simhash (open question 6).
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import perdura_memoric as pm
from perdura_memoric import embedding_scatter

# Semantic hash under test — switchable via --semantic (RFC open question 6)
SEMANTIC_FN = pm.blake2b_48bits


def _encode_node(n: dict, graph_start: float):
    return pm.encode_node(n, graph_start, semantic_fn=SEMANTIC_FN)


def _question_claims(qid: str, nodes: dict, edges: list, hops: int = 3) -> list:
    """Claims belonging to a question's neighborhood, sorted by arrival.

    Direct `answers` edges only catch flat graphs; real workers build
    chains (claim refines claim answers question), so we expand the same
    way the conductor's contention does — n-hop traversal — with one extra
    hop for the refinement chains observed in real sessions.
    """
    seen, frontier = {qid}, {qid}
    for _ in range(hops):
        nxt = set()
        for e in edges:
            if e["src"] in frontier and e["dst"] not in seen:
                nxt.add(e["dst"])
            if e["dst"] in frontier and e["src"] not in seen:
                nxt.add(e["src"])
        seen |= nxt
        frontier = nxt
    return sorted((nodes[i] for i in seen
                   if i in nodes and nodes[i]["type"] == "claim"),
                  key=lambda n: n.get("created_at", 0))


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
    Hypothesis: memoric scatter detects disagreement BEFORE an explicit
    contradicts edge appears.

    Method (temporal replay):
    - For each question, replay its answering claims in arrival order.
    - At each checkpoint with >=2 claims and no contradicts edge among them
      yet, compute scatter; label = does a contradicts edge arrive later?
    - Primary metric: AUC of checkpoint scatter as a predictor of *future*
      contradiction. The final-state association AUC is reported for
      reference only.

    Per docs/phase0-validation.md's exogeneity finding, --adversarial-every
    contradictions are manufactured by a critic that attacks wherever it
    boards, regardless of preceding scatter — by construction there is no
    hidden-disagreement signal to predict there. Scoring is restricted to
    contradicts edges NOT tagged boarding_mode "adversarial" (organic worker
    turns and --audit-every stance-auditor classifications both count, since
    neither is exogenous). auc_replay_including_adversarial reports the old
    unfiltered metric for comparison.
    """
    with open(graph_json_path, encoding="utf-8") as f:
        graph_data = json.load(f)

    nodes = {n["id"]: n for n in graph_data["nodes"]}
    edges = graph_data["edges"]
    graph_start = min((n.get("created_at", 0) for n in nodes.values()),
                      default=0)

    def replay_and_final(exclude_adversarial):
        replay = []   # (scatter at pre-contradiction checkpoint, contradiction coming?)
        final = []    # (final scatter, contradiction present?) — reference only
        for question in [n for n in nodes.values() if n["type"] == "question"]:
            qid = question["id"]
            claims = _question_claims(qid, nodes, edges)
            related_ids = {qid} | {n["id"] for n in claims}
            if len(claims) < 2:
                continue

            contra_times = sorted(
                e.get("created_at", 0) for e in edges
                if e["type"] == "contradicts"
                and e["src"] in related_ids and e["dst"] in related_ids
                and not (exclude_adversarial
                         and e.get("boarding_mode", "organic") == "adversarial"))
            first_contra = contra_times[0] if contra_times else None

            mbs = [_encode_node(n, graph_start) for n in claims]
            final.append((embedding_scatter(mbs, graph_start), bool(contra_times)))

            for k in range(2, len(claims) + 1):
                t = claims[k - 1].get("created_at", 0)
                if first_contra is not None and first_contra <= t:
                    break  # contradiction is explicit from here on — nothing to predict
                replay.append((embedding_scatter(mbs[:k], graph_start),
                               first_contra is not None))
        return replay, final

    replay, final = replay_and_final(exclude_adversarial=True)

    if len(replay) < 4:
        return {"status": "insufficient_data",
                "message": "Need more pre-contradiction checkpoints "
                           "(>=2 claims per question, edges arriving late)"}

    auc_replay = _auc(replay)
    if auc_replay != auc_replay:  # NaN: all checkpoints share one label
        return {"status": "insufficient_data",
                "message": "Need both contended and uncontended questions"}

    replay_all, _ = replay_and_final(exclude_adversarial=False)
    n_pos = sum(1 for _, c in replay if c)
    return {
        "status": "success",
        "questions_evaluated": len(final),
        "replay_checkpoints": len(replay),
        "checkpoints_preceding_contradiction": n_pos,
        "mean_scatter_before_contradiction":
            sum(s for s, c in replay if c) / max(1, n_pos),
        "mean_scatter_no_contradiction":
            sum(s for s, c in replay if not c) / max(1, len(replay) - n_pos),
        "auc_replay": auc_replay,
        "auc_final_state_reference": _auc(final),
        "auc_replay_including_adversarial": _auc(replay_all),
        "success_criteria_met": auc_replay > 0.7,
    }


# ---------------------------------------------------------------------------
# Experiment 2: Per-model track records in vector space
# ---------------------------------------------------------------------------

def experiment_2_model_track_records(graph_json_path: str, merge_log: list) -> dict:
    """
    Hypothesis: Models that produce claims close to future 'decision' nodes
    are more reliable.

    Method:
    - Good outcomes (global): decision nodes plus nodes a decision links to.
    - Bad outcomes (global): superseded or rejected nodes.
    - Per worker, reliability = d_bad / (d_good + d_bad), where d_* is the
      worker's claims' mean epistemic distance to each outcome set (self
      excluded; outcome/worker nodes encoded as live, since the stale flag
      zeroes epistemic distance by design and would erase the signal).
    - Compare to acceptance rate aggregated from the merge log.
    """
    with open(graph_json_path, encoding="utf-8") as f:
        graph_data = json.load(f)

    nodes = {n["id"]: n for n in graph_data["nodes"]}
    edges = graph_data["edges"]
    graph_start = min((n.get("created_at", 0) for n in nodes.values()),
                      default=0)

    def enc_live(n):
        m = dict(n)
        m["superseded_by"] = None
        return _encode_node(m, graph_start)

    # Global outcome sets
    decision_linked = set()
    for e in edges:
        if nodes.get(e["src"], {}).get("type") == "decision":
            decision_linked.add(e["dst"])
        if nodes.get(e["dst"], {}).get("type") == "decision":
            decision_linked.add(e["src"])
    good = [(n["id"], enc_live(n)) for n in nodes.values()
            if n["type"] == "decision" or n["id"] in decision_linked]
    bad = [(n["id"], enc_live(n)) for n in nodes.values()
           if n.get("superseded_by") is not None or n["type"] == "rejected"]

    if not good or not bad:
        return {"status": "insufficient_data",
                "message": "Need both decision-linked and superseded/rejected "
                           "nodes in the graph"}

    from perdura_memoric import epistemic_distance

    def mean_dist(claims, outcome_set):
        ds = [epistemic_distance(mb, omb, graph_start)
              for cid, mb in claims
              for oid, omb in outcome_set if oid != cid]
        return sum(ds) / len(ds) if ds else None

    nodes_by_worker = defaultdict(list)
    for n in nodes.values():
        if n.get("created_by") and n["type"] == "claim":
            nodes_by_worker[n["created_by"]].append(n)

    worker_metrics = {}
    for worker, worker_nodes in nodes_by_worker.items():
        claims = [(n["id"], enc_live(n)) for n in worker_nodes]
        d_good, d_bad = mean_dist(claims, good), mean_dist(claims, bad)
        if d_good is None or d_bad is None:
            continue
        reliability = d_bad / (d_good + d_bad + 1e-9)

        # Aggregate the merge log (it holds one entry per turn, not totals)
        accepted = sum(e.get("accepted", 0) for e in merge_log
                       if e.get("worker") == worker)
        rejected = sum(e.get("rejected", 0) for e in merge_log
                       if e.get("worker") == worker)
        acceptance_rate = accepted / max(1, accepted + rejected)

        worker_metrics[worker] = {
            "reliability_score": round(reliability, 4),
            "acceptance_rate": round(acceptance_rate, 4),
            "claims_contributed": len(worker_nodes),
            "dist_to_good": round(d_good, 4),
            "dist_to_bad": round(d_bad, 4),
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
    - Routing agreement = order preservation: over every question pair the
      edge metric strictly orders, the fraction where scatter orders the
      same way (memoric ties count half). Top-3 lists reported for
      reference. (The earlier top-3 set overlap was degenerate under the
      ties that edge-counts produce constantly.)
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
        related_claims = _question_claims(qid, nodes, edges)
        related_ids = {qid} | {n["id"] for n in related_claims}

        contradicts_count = sum(
            1 for e in edges
            if e["type"] == "contradicts"
            and e["src"] in related_ids and e["dst"] in related_ids)
        contention_baseline[qid] = contradicts_count / max(1, len(related_claims))

        claim_mbs = [_encode_node(n, graph_start) for n in related_claims]
        contention_memoric[qid] = (embedding_scatter(claim_mbs, graph_start)
                                   if claim_mbs else 0.0)

    qids = list(contention_baseline)
    score = total = 0.0
    for i in range(len(qids)):
        for j in range(i + 1, len(qids)):
            b = contention_baseline[qids[i]] - contention_baseline[qids[j]]
            if b == 0:
                continue  # edge metric expresses no preference — skip
            m = contention_memoric[qids[i]] - contention_memoric[qids[j]]
            total += 1
            if m == 0:
                score += 0.5
            elif (m > 0) == (b > 0):
                score += 1
    if total == 0:
        return {"status": "insufficient_data",
                "message": "Edge metric ties on every question pair"}
    agreement = score / total

    top3 = lambda d: [q for q, _ in sorted(d.items(), key=lambda x: -x[1])[:3]]
    return {
        "status": "success",
        "total_open_questions": len(open_questions),
        "strictly_ordered_pairs": int(total),
        "top3_baseline": top3(contention_baseline),
        "top3_memoric": top3(contention_memoric),
        "routing_agreement": round(agreement, 4),
        "success_criteria_met": agreement > 0.9,  # 90% order preservation
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Memoric binary validation experiments")
    p.add_argument("--graph", default="perdura_graph.json",
                   help="Path to perdura_graph.json")
    p.add_argument("--semantic", choices=["blake2b", "simhash"], default="blake2b",
                   help="48-bit semantic hash (RFC open question 6)")
    args = p.parse_args()

    # Module scope: this rebinds the global SEMANTIC_FN used by _encode_node
    SEMANTIC_FN = {"blake2b": pm.blake2b_48bits,
                   "simhash": pm.simhash_48bits}[args.semantic]
    print(f"semantic hash: {args.semantic}")

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
