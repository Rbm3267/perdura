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
supersessions authored by a frontier-tier worker), and **mean contention
at the moment of escalation** — the targeting-precision metric: it asks
whether frontier spend landed where the graph was actually disagreeing
with itself, and it works identically for scripted and real workers.

Synthetic mode (default, no API keys) is a POSITIVE CONTROL for the
harness, not evidence for the thesis: the scripted frontier worker always
challenges, so flip counts converge at equal cost by construction. What
synthetic mode validates is the machinery — cost parity holds, the
contention arm escalates only above threshold, the cheap arm spends
nothing, and every delta merges through the normal conductor path.

    python experiments/escalation_ab.py                  # synthetic control

The thesis itself needs real workers, which `--real` runs through this
same harness — same seeded graph, same four arms, same metrics, real
outcomes (needs ANTHROPIC_API_KEY/GEMINI_API_KEY, or a local Qwen server
for --local qwen):

    python experiments/escalation_ab.py --real \\
        --local qwen --frontier claude,gemini
"""

import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from perdura import Graph, run_turns
from perdura_router import DEFAULT_COSTS, DEFAULT_TIERS, ModelSpec, Router

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


def _synthetic_registry():
    """Fresh Pad/Challenger pair with their shared "#i" counters reset, so
    every arm's transcript starts from "#1" regardless of run order."""
    PadWorker._i = ChallengerWorker._i = 0
    pad, challenger = PadWorker(), ChallengerWorker()
    return ([ModelSpec("pad", 0.0, "local", pad),
             ModelSpec("challenger", 1.0, "frontier", challenger)],
            [pad, challenger])


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


def run_arm(policy: str, workdir: str, registry: list, workers: list,
           every: int = 3, p_escalate: float = 0.0, turns: int = TURNS,
           budget: float = BUDGET, escalate_at: float = ESCALATE_AT) -> dict:
    """Run one arm's protocol on a fresh seeded graph against the given
    registry/workers (scripted Pad/Challenger for the synthetic control,
    real ClaudeWorker/GeminiWorker/QwenWorker for `--real`)."""
    g = seed(os.path.join(workdir, f"{policy}.json"))
    router = Router(registry=registry, policy=policy, budget=budget,
                    escalate_at=escalate_at, every=every,
                    p_escalate=p_escalate, seed=42)
    run_turns(g, workers, turns, router=router)
    esc = [e for e in router.ledger if e["reason"] == "escalate"]
    frontier_names = {s.name for s in registry if s.tier == "frontier"}
    flips = sum(1 for e in g.edges.values()
                if e.type == "contradicts" and e.created_by in frontier_names)
    flips += sum(1 for n in g.nodes.values()
                 if n.superseded_by and n.created_by not in frontier_names
                 and g.nodes.get(n.superseded_by) is not None
                 and g.nodes[n.superseded_by].created_by in frontier_names)
    return {"policy": policy, "spend": router.spent,
            "escalations": len(esc),
            "mean_contention_at_escalation":
                round(sum(e["contention"] for e in esc) / len(esc), 4)
                if esc else 0.0,
            "flips": flips,
            "final_contention": round(g.contention(), 4),
            "ledger": router.ledger}


def _run_four_arms(build_registry, turns, budget, escalate_at) -> dict:
    """Run all four arms with a fresh (registry, workers) pair per arm
    (`build_registry()` -> (registry, workers)), periodic/random tuned to
    the contention arm's escalation count for cost parity."""
    with tempfile.TemporaryDirectory() as workdir:
        arms = {}
        registry, workers = build_registry()
        arms["contention"] = run_arm("contention", workdir, registry, workers,
                                     turns=turns, budget=budget,
                                     escalate_at=escalate_at)
        e = max(arms["contention"]["escalations"], 1)
        registry, workers = build_registry()
        arms["periodic"] = run_arm("periodic", workdir, registry, workers,
                                   every=max(round(turns / e), 1), turns=turns,
                                   budget=budget, escalate_at=escalate_at)
        registry, workers = build_registry()
        arms["random"] = run_arm("random", workdir, registry, workers,
                                 p_escalate=e / turns, turns=turns,
                                 budget=budget, escalate_at=escalate_at)
        registry, workers = build_registry()
        arms["cheap"] = run_arm("cheap", workdir, registry, workers,
                                turns=turns, budget=budget,
                                escalate_at=escalate_at)
    return arms


def run_synthetic_arms(turns=TURNS, budget=BUDGET, escalate_at=ESCALATE_AT):
    return _run_four_arms(_synthetic_registry, turns, budget, escalate_at)


def run_real_arms(local_name: str, frontier_names: list, turns=TURNS,
                  budget=BUDGET, escalate_at=ESCALATE_AT) -> dict:
    """Same protocol, real workers: `local_name` (zero-cost default labor —
    qwen needs a local OpenAI-compatible server; mock needs nothing but
    isn't a real worker) plus `frontier_names` (claude/gemini, real API
    calls, real cost). Router requires >=1 strictly-zero-cost worker, which
    is why a local/mock anchor is mandatory even in --real mode."""
    from types import SimpleNamespace

    from perdura import (DEFAULT_CLAUDE_MODEL, DEFAULT_GEMINI_MODEL,
                         DEFAULT_QWEN_MODEL, DEFAULT_QWEN_URL,
                         WORKER_FACTORIES)

    fake_args = SimpleNamespace(claude_model=DEFAULT_CLAUDE_MODEL,
                                gemini_model=DEFAULT_GEMINI_MODEL,
                                qwen_model=DEFAULT_QWEN_MODEL,
                                qwen_url=DEFAULT_QWEN_URL)

    def build_registry():
        names = [local_name] + [n for n in frontier_names if n]
        instances = [(name, WORKER_FACTORIES[name](fake_args)) for name in names]
        registry = [ModelSpec(name, DEFAULT_COSTS.get(name, 0.0),
                              DEFAULT_TIERS.get(name, "local"), w)
                   for name, w in instances]
        return registry, [w for _, w in instances]

    return _run_four_arms(build_registry, turns, budget, escalate_at)


# -- reporting ------------------------------------------------------------

def print_report(arms: dict, turns: int, budget: float, escalate_at: float):
    print("\n" + "=" * 72)
    print(f"Escalation A/B — {turns} turns, budget {budget:g}, "
          f"threshold {escalate_at}")
    print(f"{'arm':<12}{'spend':>6}{'escal.':>8}{'flips':>7}"
          f"{'cont@esc':>10}{'final cont.':>13}")
    for a in arms.values():
        print(f"{a['policy']:<12}{a['spend']:>6g}{a['escalations']:>8}"
              f"{a['flips']:>7}{a['mean_contention_at_escalation']:>10}"
              f"{a['final_contention']:>13}")


def main():
    import argparse
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--real", action="store_true",
                  help="run real workers instead of the synthetic positive "
                       "control — this is the actual decisive experiment")
    p.add_argument("--local", default="qwen", choices=["qwen", "mock"],
                  help="(--real only) the zero-cost default-labor worker; "
                       "qwen needs a local OpenAI-compatible server "
                       "(--qwen-url in perdura.py's default, LM Studio)")
    p.add_argument("--frontier", default="claude,gemini",
                  help="(--real only) comma list of frontier workers to "
                       "escalate to; needs the matching API key(s) "
                       "(ANTHROPIC_API_KEY / GEMINI_API_KEY)")
    p.add_argument("--turns", type=int, default=TURNS)
    p.add_argument("--budget", type=float, default=BUDGET)
    p.add_argument("--escalate-at", type=float, default=ESCALATE_AT)
    args = p.parse_args()

    if args.real:
        arms = run_real_arms(args.local, args.frontier.split(","),
                             turns=args.turns, budget=args.budget,
                             escalate_at=args.escalate_at)
        print_report(arms, args.turns, args.budget, args.escalate_at)
        print("\nREAL WORKER RUN — this is the decisive Phase 3 experiment "
              "(docs/enterprise.md's gate), not a positive control: no "
              "pass/fail assertion here, read the table. Contention-routing's "
              "real-world cost-effectiveness was an open claim before this "
              "run (CLAUDE.md \"Key decisions\") — this is what answers it.")
        return

    arms = run_synthetic_arms(args.turns, args.budget, args.escalate_at)
    print_report(arms, args.turns, args.budget, args.escalate_at)

    # harness validity checks (the synthetic arm is a positive control)
    c = arms["contention"]
    assert all(e["contention"] >= args.escalate_at for e in c["ledger"]
               if e["reason"] == "escalate"), \
        "contention arm escalated below threshold"
    assert arms["cheap"]["spend"] == 0, "cheap arm spent budget"
    assert abs(arms["periodic"]["spend"] - c["spend"]) <= 1.0, \
        "cost parity broken between contention and periodic arms"
    assert c["mean_contention_at_escalation"] >= \
        arms["random"]["mean_contention_at_escalation"], \
        "contention arm did not out-target random at escalation time"
    print("\nharness checks passed — synthetic positive control only; the "
          "thesis verdict needs real workers: rerun with --real (needs "
          "ANTHROPIC_API_KEY/GEMINI_API_KEY, or a local Qwen server for "
          "--local qwen)")


if __name__ == "__main__":
    main()
