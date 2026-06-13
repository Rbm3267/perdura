"""
experiments/escalation_ab.py — the decisive Phase 3 experiment, as a harness.

Question (README "the decisive experiment still ahead"): does
contention-triggered escalation put frontier spend where it changes
outcomes more than periodic or random escalation AT EQUAL COST?

Protocol
--------
Four arms over the same seeded graph (one contested question, one calm
one), same workers, same turn count:

    contention   escalate when hottest-question contention >= threshold
    periodic     escalate every Nth turn, N chosen to match the contention
                 arm's escalation count (cost parity by construction)
    random       escalate with p = E/turns, fixed seed (cost parity in
                 expectation)
    cheap        never escalate (the floor)

Metrics per arm: spend, escalations, outcome flips (contradicts +
supersessions authored by the frontier worker), and **mean contention at
the moment of escalation** — the targeting-precision metric: it asks
whether frontier spend landed where the graph was actually disagreeing
with itself, and it works identically for scripted and real workers.

Synthetic mode (default, no API keys) is a POSITIVE CONTROL for the
harness, not evidence for the thesis: the scripted frontier worker always
challenges, so flip counts converge at equal cost by construction. What
synthetic mode validates is the machinery — cost parity holds, the
contention arm escalates only above threshold, the cheap arm spends
nothing, and every delta merges through the normal conductor path. The
thesis itself needs real workers:

    python experiments/escalation_ab.py            # synthetic control
    (real arms: run perdura.py run --route ... with real workers and
     compare ledgers/scorecards — same metrics, real outcomes)
"""

import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from perdura import Graph, run_turns
from perdura_router import ModelSpec, Router

TURNS = 12
BUDGET = 4.0
ESCALATE_AT = 0.15


# -- scripted workers (briefing-only, like real ones) ------------------------

def _qid(prompt: str) -> str:
    return prompt.split("Open question under consideration:\n")[1].split(":")[0]


class PadWorker:
    """Cheap local labor at its worst: agreeable padding, never a challenge."""
    name = "pad"
    _i = 0

    def generate(self, prompt):
        PadWorker._i += 1
        i = PadWorker._i
        return json.dumps({
            "new_nodes": [{"ref": "a", "type": "claim",
                           "text": f"Padding claim #{i}: the current approach "
                                   f"seems broadly reasonable and workable.",
                           "confidence": 0.55, "domain_tags": ["synthetic"]}],
            "new_edges": [{"type": "answers", "src": "a", "dst": _qid(prompt)}],
            "supersedes": [], "resolve_questions": [],
        })


class ChallengerWorker:
    """Scripted frontier: challenges the highest-confidence claim in the
    briefing that nothing contradicts yet — guaranteed outcome flips, which
    is exactly why synthetic results validate the harness, not the thesis."""
    name = "challenger"
    _i = 0

    def generate(self, prompt):
        ChallengerWorker._i += 1
        i = ChallengerWorker._i
        claims = re.findall(r"^(n_\w+) \| claim \| ([\d.]+|--) \|",
                            prompt, re.M)
        challenged = {m.group(2) for m in
                      re.finditer(r"(n_\w+) -\[contradicts\]-> (n_\w+)", prompt)}
        fresh = [(cid, conf) for cid, conf in claims if cid not in challenged]
        pool = fresh or claims
        if not pool:                       # nothing to attack: pad instead
            return PadWorker().generate(prompt)
        target = max(pool, key=lambda c: 0.0 if c[1] == "--" else float(c[1]))[0]
        return json.dumps({
            "new_nodes": [{"ref": "c", "type": "claim",
                           "text": f"Challenge #{i}: the favored claim "
                                   f"overstates its evidence and ignores the "
                                   f"failure mode under load.",
                           "confidence": 0.65, "domain_tags": ["synthetic"]}],
            "new_edges": [{"type": "contradicts", "src": "c", "dst": target}],
            "supersedes": [], "resolve_questions": [],
        })


# -- protocol -----------------------------------------------------------------

def seed(path: str) -> Graph:
    """One contested question (live disagreement) and one calm one."""
    g = Graph(path)
    q1 = g.add_node("question", "Should briefing budgets scale with graph "
                    "size or stay fixed?", created_by="user", confidence=1.0)
    a = g.add_node("claim", "Budgets must scale: fixed budgets starve large "
                    "graphs of context.", created_by="seed_a", confidence=0.8)
    b = g.add_node("claim", "Budgets must stay fixed: scaling budgets "
                    "reintroduces unbounded context cost.", created_by="seed_b",
                    confidence=0.8)
    g.add_edge("answers", a, q1, "seed_a")
    g.add_edge("answers", b, q1, "seed_b")
    g.add_edge("contradicts", b, a, "seed_b")
    q2 = g.add_node("question", "What timestamp format should the merge log "
                    "use?", created_by="user", confidence=1.0)
    c = g.add_node("claim", "Unix epoch floats are sufficient for the log.",
                   created_by="seed_a", confidence=0.6)
    g.add_edge("answers", c, q2, "seed_a")
    g.save()
    return g


def run_arm(policy: str, workdir: str, every: int = 3,
            p_escalate: float = 0.0) -> dict:
    PadWorker._i = ChallengerWorker._i = 0
    g = seed(os.path.join(workdir, f"{policy}.json"))
    workers = [PadWorker(), ChallengerWorker()]
    router = Router(
        registry=[ModelSpec("pad", 0.0, "local", workers[0]),
                  ModelSpec("challenger", 1.0, "frontier", workers[1])],
        policy=policy, budget=BUDGET, escalate_at=ESCALATE_AT,
        every=every, p_escalate=p_escalate, seed=42)
    run_turns(g, workers, TURNS, router=router)
    esc = [e for e in router.ledger if e["reason"] == "escalate"]
    flips = sum(1 for e in g.edges.values()
                if e.type == "contradicts" and e.created_by == "challenger")
    flips += sum(1 for n in g.nodes.values()
                 if n.superseded_by and n.created_by != "challenger"
                 and g.nodes.get(n.superseded_by) is not None
                 and g.nodes[n.superseded_by].created_by == "challenger")
    return {"policy": policy, "spend": router.spent,
            "escalations": len(esc),
            "mean_contention_at_escalation":
                round(sum(e["contention"] for e in esc) / len(esc), 4)
                if esc else 0.0,
            "flips": flips,
            "final_contention": round(g.contention(), 4),
            "ledger": router.ledger}


def main():
    with tempfile.TemporaryDirectory() as workdir:
        arms = {}
        arms["contention"] = run_arm("contention", workdir)
        e = max(arms["contention"]["escalations"], 1)
        arms["periodic"] = run_arm("periodic", workdir,
                                   every=max(round(TURNS / e), 1))
        arms["random"] = run_arm("random", workdir, p_escalate=e / TURNS)
        arms["cheap"] = run_arm("cheap", workdir)

    print("\n" + "=" * 72)
    print(f"Escalation A/B — {TURNS} turns, budget {BUDGET:g}, "
          f"threshold {ESCALATE_AT}")
    print(f"{'arm':<12}{'spend':>6}{'escal.':>8}{'flips':>7}"
          f"{'cont@esc':>10}{'final cont.':>13}")
    for a in arms.values():
        print(f"{a['policy']:<12}{a['spend']:>6g}{a['escalations']:>8}"
              f"{a['flips']:>7}{a['mean_contention_at_escalation']:>10}"
              f"{a['final_contention']:>13}")

    # harness validity checks (the synthetic arm is a positive control)
    c = arms["contention"]
    assert all(e["contention"] >= ESCALATE_AT for e in c["ledger"]
               if e["reason"] == "escalate"), \
        "contention arm escalated below threshold"
    assert arms["cheap"]["spend"] == 0, "cheap arm spent budget"
    assert abs(arms["periodic"]["spend"] - c["spend"]) <= 1.0, \
        "cost parity broken between contention and periodic arms"
    assert c["mean_contention_at_escalation"] >= \
        arms["random"]["mean_contention_at_escalation"], \
        "contention arm did not out-target random at escalation time"
    print("\nharness checks passed — synthetic positive control only; "
          "the thesis verdict needs real workers (see module docstring)")


if __name__ == "__main__":
    main()
