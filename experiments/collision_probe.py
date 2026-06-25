"""
experiments/collision_probe.py — the inversion finding, reproducible.

Post-hoc separability: can memoric distances tell contradicting claim
pairs from random pairs at all? Measured 2026-06-12 on a real session:
NO for the spec'd direction — contradicting pairs are lexically CLOSER
(simhash AUC 0.296) and confidence-closer (0.371) than random pairs.
Same topic, opposite stance. Inverted, lexical proximity is a
disagreement LOCATOR (collision band), feeding the stance-audit
boarding mode (perdura.py --audit-every).

That 2026-06-12 session ran entirely under --adversarial-every, so the
original collision-band calibration (COLLISION_LOW/HIGH) was tuned on
exogenous contradictions only — a critic attacking the strongest claim
on every boarding, not organic disagreement (docs/phase0-validation.md's
exogeneity finding). Since perdura.py now tags every node/edge with
boarding_mode, this probe splits contradicting pairs into organic+audit
(genuine disagreement, audit-classified or not) vs adversarial
(manufactured) and reports the inversion + collision-band numbers for
each separately, plus the pooled "all" figure for comparison. Graphs
written before boarding_mode existed load every edge as "organic" (the
dataclass default) — there's no way to retroactively recover which of
those were adversarially manufactured, so the split is only meaningful
for sessions run after this fix.

    python experiments/collision_probe.py --graph perdura_graph.json
"""

import argparse
import itertools
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import perdura_memoric as pm


def auc(pos, neg):
    if not pos or not neg:
        return float("nan")
    w = sum(1.0 if p > n else 0.5 if p == n else 0.0 for p in pos for n in neg)
    return w / (len(pos) * len(neg))


def probe(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    nodes = {n["id"]: n for n in data.get("nodes", []) if "id" in n}
    gs = min((n.get("created_at") or 0 for n in nodes.values()), default=0)
    claims = [n for n in nodes.values() if n["type"] == "claim"]
    contra_edges = [e for e in data.get("edges", [])
                    if e.get("type") == "contradicts"
                    and "src" in e and "dst" in e
                    and nodes.get(e["src"], {}).get("type") == "claim"
                    and nodes.get(e["dst"], {}).get("type") == "claim"]
    contra = {frozenset((e["src"], e["dst"])) for e in contra_edges}
    adversarial_contra = {frozenset((e["src"], e["dst"])) for e in contra_edges
                          if e.get("boarding_mode", "organic") == "adversarial"}
    organic_contra = contra - adversarial_contra
    if len(contra) < 3 or len(claims) < 6:
        print("insufficient data: need >=3 contradicting claim pairs")
        return

    pair_sets = [("all", contra), ("organic+audit", organic_contra),
                 ("adversarial", adversarial_contra)]

    enc = {fn: {c["id"]: pm.encode_node(c, gs, semantic_fn=f)
                for c in claims}
           for fn, f in (("blake2b", pm.blake2b_48bits),
                         ("simhash", pm.simhash_48bits))}
    metrics = {
        "semantic_blake2b": lambda a, b: pm.semantic_distance(
            enc["blake2b"][a], enc["blake2b"][b]) / 48,
        "semantic_simhash": lambda a, b: pm.semantic_distance(
            enc["simhash"][a], enc["simhash"][b]) / 48,
        "conf_delta": lambda a, b: abs(nodes[a].get("confidence", 0.5)
                                       - nodes[b].get("confidence", 0.5)),
        "epistemic_simhash": lambda a, b: pm.epistemic_distance(
            enc["simhash"][a], enc["simhash"][b]),
    }
    ids = [c["id"] for c in claims]
    # Negatives are pairs with no contradicts edge at all (organic or
    # adversarial) — fixed across all three positive sets below so the
    # comparison is always "this kind of contradiction" vs "no contradiction".
    neg_pairs = [p for p in itertools.combinations(ids, 2)
                 if frozenset(p) not in contra]
    print(f"{len(contra)} contradicting pairs total "
          f"({len(organic_contra)} organic+audit, "
          f"{len(adversarial_contra)} adversarial) vs "
          f"{len(neg_pairs)} non-contradicting pairs")
    print(f"(>0.5: distance grows with disagreement; <0.5: INVERTED)\n")
    header = f"{'metric':<20}" + "".join(f"{label:>16}" for label, _ in pair_sets)
    print(header)
    for name, fn in metrics.items():
        row = f"{name:<20}"
        for _, pos_set in pair_sets:
            pos_pairs = [tuple(p) for p in pos_set]
            a = auc([fn(*p) for p in pos_pairs], [fn(*p) for p in neg_pairs])
            row += f"{a:>16.3f}"
        print(row)

    # Collision-band calibration
    h = {c["id"]: pm.simhash_48bits(c.get("text") or "") for c in claims}

    def band(p):
        d = (h[p[0]] ^ h[p[1]]).bit_count()
        return pm.COLLISION_LOW < d <= pm.COLLISION_HIGH

    tot = sum(1 for p in itertools.combinations(ids, 2) if band(p))
    print(f"\ncollision band ({pm.COLLISION_LOW},{pm.COLLISION_HIGH}]: "
          f"{tot} candidate pairs total")
    for label, pos_set in pair_sets:
        pos_pairs = [tuple(p) for p in pos_set]
        hit = sum(1 for p in pos_pairs if band(p))
        print(f"  {label:<14} recall {hit}/{len(pos_pairs)}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Inversion-finding probe")
    p.add_argument("--graph", default="perdura_graph.json")
    probe(p.parse_args().graph)
