"""
perdura_track.py — Phase 2: per-model, per-domain track records.

Deterministic and derived: scores are recomputed from the graph's outcome
lineage on demand, never persisted (same philosophy as memoric binary —
no migrations, no staleness, the scoring rubric stays free to evolve).

Outcome events per claim:
  promoted     — linked to a decision node by any edge            +1.0
  corroborated — incoming supports edge from a DIFFERENT worker   +0.5
  challenged   — earlier endpoint of a contradicts edge           -0.5
  superseded   — superseded_by set                                -1.0

reliability = (1 + good) / (2 + good + bad)   # Laplace-smoothed Beta mean
0.5 means "no evidence yet"; the prior keeps one lucky claim from minting
a 1.0 reputation. Per-domain scores restrict to claims carrying the tag.

Conductor-authored edges (near-dup refines) never count as outcomes.

    python perdura.py track            # printable scorecard
"""


def claim_events(graph) -> dict:
    """Map claim id -> (good, bad) outcome weights."""
    decision_ids = {n.id for n in graph.nodes.values() if n.type == "decision"}
    events: dict = {}

    def bump(nid, good=0.0, bad=0.0):
        g, b = events.get(nid, (0.0, 0.0))
        events[nid] = (g + good, b + bad)

    for e in graph.edges.values():
        if e.created_by == "conductor":
            continue
        src, dst = graph.nodes.get(e.src), graph.nodes.get(e.dst)
        if src is None or dst is None:
            continue
        # promoted: any non-conductor edge touching a decision node
        if e.src in decision_ids and dst.type == "claim":
            bump(e.dst, good=1.0)
        if e.dst in decision_ids and src.type == "claim":
            bump(e.src, good=1.0)
        # corroborated: supports from a different worker
        if (e.type == "supports" and dst.type == "claim"
                and src.created_by and dst.created_by
                and src.created_by != dst.created_by):
            bump(e.dst, good=0.5)
        # challenged: the EARLIER endpoint of a contradicts edge (the later
        # claim is the challenger — same convention as the anchoring probe)
        if e.type == "contradicts" and src.type == dst.type == "claim":
            if src.created_at < dst.created_at:
                bump(e.src, bad=0.5)
            elif dst.created_at < src.created_at:
                bump(e.dst, bad=0.5)
            else:
                # Tie (mock/test data with default timestamps): fall back to
                # edge direction — workers draw contradicts FROM their new
                # claim TO the challenged one, so dst is the challenged.
                bump(e.dst, bad=0.5)

    for n in graph.nodes.values():
        if n.type == "claim" and n.superseded_by is not None:
            bump(n.id, bad=1.0)
    return events


def _reliability(good: float, bad: float) -> float:
    return (1.0 + good) / (2.0 + good + bad)


def track_records(graph) -> dict:
    """Per-worker (and per-domain) reliability from claim outcomes."""
    events = claim_events(graph)
    records: dict = {}
    for n in graph.nodes.values():
        if (n.type != "claim" or not n.created_by
                or n.created_by in ("user", "conductor")):
            continue
        good, bad = events.get(n.id, (0.0, 0.0))
        rec = records.setdefault(n.created_by, {
            "claims": 0, "good": 0.0, "bad": 0.0, "domains": {}})
        rec["claims"] += 1
        rec["good"] += good
        rec["bad"] += bad
        for tag in n.domain_tags or []:
            d = rec["domains"].setdefault(tag, {"claims": 0, "good": 0.0,
                                                "bad": 0.0})
            d["claims"] += 1
            d["good"] += good
            d["bad"] += bad

    for rec in records.values():
        rec["reliability"] = round(_reliability(rec["good"], rec["bad"]), 4)
        for d in rec["domains"].values():
            d["reliability"] = round(_reliability(d["good"], d["bad"]), 4)
    return records


def scorecard(graph) -> str:
    """Printable per-model, per-domain scorecard."""
    records = track_records(graph)
    if not records:
        return "No attributed claims yet — run some worker turns first."
    lines = ["Per-model track records (Phase 2; derived, 0.5 = no evidence):"]
    for w, rec in sorted(records.items(),
                         key=lambda kv: -kv[1]["reliability"]):
        lines.append(f"  {w}: reliability {rec['reliability']:.3f} "
                     f"({rec['claims']} claims, +{rec['good']:.1f} good, "
                     f"-{rec['bad']:.1f} bad)")
        for tag, d in sorted(rec["domains"].items(),
                             key=lambda kv: -kv[1]["reliability"]):
            lines.append(f"      {tag}: {d['reliability']:.3f} "
                         f"({d['claims']} claims)")
    return "\n".join(lines)
