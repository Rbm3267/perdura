"""
ledger.py — the memoric-conductor skill's "central memory train."

A standalone, dependency-free reimplementation of Perdura's memoric-binary
idea (perdura_memoric.py) and router ledger (perdura_router.py) at
single-session granularity, deliberately leaving out the contention/edge
machinery: routing here is driven by per-(domain, task_type) track record
and a token budget, not by disagreement between parallel takes.

Append-only, supersede-never-delete, same as the Perdura graph: a task
record is never edited in place. A later "supersede" event demotes an
earlier record's reliability contribution without removing it, so the
ledger stays an honest history of what was tried, not just what worked.

CLI (the conductor calls this as deterministic code -- it makes the
routing decision, not an LLM):

    python ledger.py encode --domain <tag> --type <type> --confidence 0.0-1.0 --text "..."
    python ledger.py pick --domain <tag> --type <type> [--budget N] [--spent-file F]
    python ledger.py append --domain <tag> --type <type> --confidence C --tier T --model M --cost N [--text "..."]
    python ledger.py supersede --target <record_id>
    python ledger.py report
"""

import argparse
import json
import os
import sys
import time
import uuid
from hashlib import blake2b

LEDGER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "train.jsonl")

# Reliability when a (domain, type, tier) combination has no history yet --
# same Laplace-smoothing prior perdura_track.py uses for an unseen worker.
PRIOR_RELIABILITY = 0.5

DEFAULT_TIER_COST = {"local": 0.0, "frontier": 3.0}


def semantic_hash(text: str) -> str:
    """16-hex-char (64-bit) content fingerprint -- enough to recognize a
    repeated/near-identical task without storing the task text itself."""
    return blake2b((text or "").encode(), digest_size=8).hexdigest()


def load_records(path=LEDGER_PATH):
    if not os.path.exists(path):
        return []
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def append_record(record: dict, path=LEDGER_PATH):
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")
    return record


def superseded_ids(records: list) -> set:
    return {r["target"] for r in records if r.get("event") == "supersede"}


def task_records(records: list) -> list:
    return [r for r in records if r.get("event") != "supersede"]


def track_record(records: list, domain: str, task_type: str) -> dict:
    """Per-tier reliability for this (domain, type): Laplace-smoothed
    fraction of past records NOT later superseded, like perdura_track.py's
    claim-outcome scoring -- but the signal here is supersession, not
    contradiction."""
    superseded = superseded_ids(records)
    by_tier = {}
    for r in task_records(records):
        if r.get("domain") != domain or r.get("type") != task_type:
            continue
        tier = r["tier"]
        s = by_tier.setdefault(tier, {"n": 0, "ok": 0, "cost": 0.0})
        s["n"] += 1
        s["cost"] += r.get("cost", 0.0)
        if r["id"] not in superseded:
            s["ok"] += 1
    out = {}
    for tier, s in by_tier.items():
        # Laplace smoothing (+1/+2) toward the 0.5 prior, exactly like
        # perdura_track.py's per-model reliability.
        reliability = (s["ok"] + 1) / (s["n"] + 2)
        avg_cost = s["cost"] / s["n"] if s["n"] else DEFAULT_TIER_COST.get(tier, 0.0)
        out[tier] = {"reliability": round(reliability, 4), "n": s["n"],
                     "avg_cost": round(avg_cost, 4)}
    return out


def domain_spent(records: list, domain: str) -> float:
    return sum(r.get("cost", 0.0) for r in task_records(records)
               if r.get("domain") == domain)


def pick_tier(records: list, domain: str, task_type: str,
              budget: float = None, escalate_margin: float = 0.15) -> dict:
    """Deterministic routing rule -- no LLM judgment, same invariant as
    Perdura's conductor. Stays local unless frontier's reliability-per-cost
    in this exact (domain, type) beats local's by more than
    `escalate_margin`, AND the domain still has budget headroom."""
    tr = track_record(records, domain, task_type)
    local = tr.get("local", {"reliability": PRIOR_RELIABILITY, "n": 0,
                              "avg_cost": DEFAULT_TIER_COST["local"]})
    frontier = tr.get("frontier", {"reliability": PRIOR_RELIABILITY, "n": 0,
                                    "avg_cost": DEFAULT_TIER_COST["frontier"]})

    spent = domain_spent(records, domain)
    frontier_cost = max(frontier["avg_cost"], 0.01)
    over_budget = budget is not None and spent + frontier_cost > budget

    local_score = local["reliability"] / max(local["avg_cost"], 0.01) \
        if local["avg_cost"] else local["reliability"] * 100
    frontier_score = frontier["reliability"] / frontier_cost

    escalate = (not over_budget) and (frontier_score - local_score > escalate_margin
                                       or local["n"] == 0 and frontier["n"] > 0
                                       and frontier["reliability"] > PRIOR_RELIABILITY)
    return {
        "tier": "frontier" if escalate else "local",
        "reason": "budget-exhausted" if over_budget else
                  ("track-record" if escalate else "default-local"),
        "domain_spent": round(spent, 4),
        "local": local, "frontier": frontier,
    }


def cmd_encode(args):
    rec = {
        "id": uuid.uuid4().hex[:10],
        "ts": time.time(),
        "domain": args.domain,
        "type": args.type,
        "confidence": args.confidence,
        "semantic": semantic_hash(args.text),
    }
    print(json.dumps(rec, indent=2))


def cmd_pick(args):
    records = load_records()
    decision = pick_tier(records, args.domain, args.type, budget=args.budget)
    print(json.dumps(decision, indent=2))


def cmd_append(args):
    rec = {
        "id": uuid.uuid4().hex[:10],
        "ts": time.time(),
        "domain": args.domain,
        "type": args.type,
        "confidence": args.confidence,
        "tier": args.tier,
        "model": args.model,
        "cost": args.cost,
        "semantic": semantic_hash(args.text or ""),
    }
    append_record(rec)
    print(json.dumps(rec, indent=2))


def cmd_supersede(args):
    append_record({"event": "supersede", "target": args.target, "ts": time.time()})
    print(f"superseded {args.target}")


def cmd_report(args):
    records = load_records()
    tasks = task_records(records)
    superseded = superseded_ids(records)
    domains = sorted({r["domain"] for r in tasks})
    print(f"{len(tasks)} tasks, {len(superseded)} superseded, "
          f"{len(records) - len(tasks)} supersede events\n")
    for d in domains:
        types = sorted({r["type"] for r in tasks if r["domain"] == d})
        for t in types:
            tr = track_record(records, d, t)
            print(f"  {d}/{t}: " + ", ".join(
                f"{tier} n={s['n']} reliability={s['reliability']} avg_cost={s['avg_cost']}"
                for tier, s in tr.items()))


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    pe = sub.add_parser("encode")
    pe.add_argument("--domain", required=True)
    pe.add_argument("--type", required=True)
    pe.add_argument("--confidence", type=float, default=0.5)
    pe.add_argument("--text", default="")
    pe.set_defaults(func=cmd_encode)

    pp = sub.add_parser("pick")
    pp.add_argument("--domain", required=True)
    pp.add_argument("--type", required=True)
    pp.add_argument("--budget", type=float, default=None)
    pp.set_defaults(func=cmd_pick)

    pa = sub.add_parser("append")
    pa.add_argument("--domain", required=True)
    pa.add_argument("--type", required=True)
    pa.add_argument("--confidence", type=float, default=0.5)
    pa.add_argument("--tier", required=True, choices=["local", "frontier"])
    pa.add_argument("--model", required=True)
    pa.add_argument("--cost", type=float, required=True)
    pa.add_argument("--text", default="")
    pa.set_defaults(func=cmd_append)

    ps = sub.add_parser("supersede")
    ps.add_argument("--target", required=True)
    ps.set_defaults(func=cmd_supersede)

    pr = sub.add_parser("report")
    pr.set_defaults(func=cmd_report)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
