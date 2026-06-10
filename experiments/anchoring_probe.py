"""
experiments/anchoring_probe.py — measurement for issue #6.

Hidden authorship fights anchoring, but workers still see confidence.
Question: do workers contradict high-confidence claims less than
low-confidence ones? Observational measure over a session graph:

  contradicts-rate(bucket) = claims in bucket receiving a contradicts edge
                             from a LATER claim / claims in bucket

A large gap (high-confidence claims drawing fewer challenges) is anchoring
signal — then re-run the session with --mask-confidence and compare.

    python experiments/anchoring_probe.py --graph perdura_graph.json
"""

import argparse
import json

HIGH = 0.7


def probe(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    nodes = {n["id"]: n for n in data["nodes"]}

    challenged = set()  # claims that received a contradicts edge later
    for e in data["edges"]:
        if e["type"] != "contradicts":
            continue
        for end in (e["src"], e["dst"]):
            n = nodes.get(end)
            if n and n["type"] == "claim" and \
                    e.get("created_at", 0) > n.get("created_at", 0):
                challenged.add(end)

    buckets = {"high": [0, 0], "low": [0, 0]}  # [challenged, total]
    for n in nodes.values():
        if n["type"] != "claim" or n.get("created_by") in ("", "user"):
            continue
        b = buckets["high" if n.get("confidence", 0.5) >= HIGH else "low"]
        b[1] += 1
        b[0] += n["id"] in challenged

    rate = lambda b: b[0] / b[1] if b[1] else float("nan")
    hi, lo = rate(buckets["high"]), rate(buckets["low"])
    return {
        "high_conf_claims": buckets["high"][1],
        "high_conf_challenged": buckets["high"][0],
        "high_conf_challenge_rate": round(hi, 4),
        "low_conf_claims": buckets["low"][1],
        "low_conf_challenged": buckets["low"][0],
        "low_conf_challenge_rate": round(lo, 4),
        "effect_size_rate_diff": round(lo - hi, 4) if hi == hi and lo == lo
        else None,
        "note": "positive diff = high-confidence claims are challenged "
                "less (anchoring signal); compare against a "
                "--mask-confidence run",
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Confidence-anchoring probe")
    p.add_argument("--graph", default="perdura_graph.json")
    args = p.parse_args()
    print(json.dumps(probe(args.graph), indent=2))
