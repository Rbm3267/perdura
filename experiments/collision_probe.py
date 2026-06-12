"""
experiments/collision_probe.py — the inversion finding, reproducible.

Post-hoc separability: can memoric distances tell contradicting claim
pairs from random pairs at all? Measured 2026-06-12 on a real session:
NO for the spec'd direction — contradicting pairs are lexically CLOSER
(simhash AUC 0.296) and confidence-closer (0.371) than random pairs.
Same topic, opposite stance. Inverted, lexical proximity is a
disagreement LOCATOR (collision band), feeding the stance-audit
boarding mode (perdura.py --audit-every).

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
    nodes = {n["id"]: n for n in data["nodes"]}
    gs = min((n.get("created_at", 0) for n in nodes.values()), default=0)
    claims = [n for n in nodes.values() if n["type"] == "claim"]
    contra = {frozenset((e["src"], e["dst"])) for e in data["edges"]
              if e["type"] == "contradicts"
              and nodes.get(e["src"], {}).get("type") == "claim"
              and nodes.get(e["dst"], {}).get("type") == "claim"}
    if len(contra) < 3 or len(claims) < 6:
        print("insufficient data: need >=3 contradicting claim pairs")
        return

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
    pos_pairs = [tuple(p) for p in contra]
    neg_pairs = [p for p in itertools.combinations(ids, 2)
                 if frozenset(p) not in contra]
    print(f"{len(pos_pairs)} contradicting vs {len(neg_pairs)} other pairs")
    print(f"{'metric':<20} {'AUC':>6}  (>0.5: distance grows with "
          f"disagreement; <0.5: INVERTED)")
    for name, fn in metrics.items():
        a = auc([fn(*p) for p in pos_pairs], [fn(*p) for p in neg_pairs])
        print(f"{name:<20} {a:>6.3f}")

    # Collision-band calibration
    h = {c["id"]: pm.simhash_48bits(c.get("text") or "") for c in claims}

    def band(p):
        d = (h[p[0]] ^ h[p[1]]).bit_count()
        return pm.COLLISION_LOW < d <= pm.COLLISION_HIGH
    hit = sum(1 for p in pos_pairs if band(p))
    tot = sum(1 for p in itertools.combinations(ids, 2) if band(p))
    print(f"\ncollision band ({pm.COLLISION_LOW},{pm.COLLISION_HIGH}]: "
          f"recall {hit}/{len(pos_pairs)} contradicting pairs; "
          f"{tot} candidate pairs total")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Inversion-finding probe")
    p.add_argument("--graph", default="perdura_graph.json")
    probe(p.parse_args().graph)
