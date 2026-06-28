"""
perdura_router.py — Phase 3 kernel: the epistemic router.

The thesis made executable: cheap local models are the default labor force,
and frontier models are summoned to bound spend. The router owns WORKER
selection (question selection stays with the conductor loop); it is
deterministic operator-side code — nothing here touches the validate/merge
path.

Default policy is `periodic`, not `contention` (pivoted 2026-06-25 — see
docs/phase3-ab-results.md). The decisive A/B (experiments/escalation_ab.py)
ran six clean times against real Claude/Gemini calls: contention-triggered
escalation confirmed its targeting precision (it reliably finds the
hottest live disagreement, every run) but never once beat periodic
escalation on the metric the bet actually rides on — outcome flips at
equal cost (5 losses, 1 tie across 6 runs). `policy="contention"` is still
fully supported as a research arm; it is no longer the recommended default.

Routing decision (ROADMAP Phase 3):

    escalate?           policy decides (periodic / contention / random)
    which frontier?     argmax reliability/cost among affordable ones,
                        reliability = live track records (perdura_track),
                        0.5 (no evidence) when a worker has no history
    otherwise           the cheapest affordable worker (local first)

Budgets are hard: when the session budget is spent, every turn falls back
to the cheapest worker. The ledger records every decision with its reason,
so an A/B harness (experiments/escalation_ab.py) can hold cost constant
across policies — the decisive Phase 3 experiment.
"""

import random
from dataclasses import dataclass, field

from perdura_track import track_records

# Relative cost units per boarding. Local labor is free by definition —
# the thesis prices frontier calls, not electricity.
DEFAULT_COSTS = {"qwen": 0.0, "mock": 0.0, "gemini": 1.0, "claude": 3.0}
DEFAULT_TIERS = {"qwen": "local", "mock": "local",
                 "gemini": "frontier", "claude": "frontier"}

# Contention at/above which the contention policy summons a frontier worker.
# Shared so the Station's routing preview matches what the router would do.
DEFAULT_ESCALATE_AT = 0.15


@dataclass
class ModelSpec:
    name: str
    cost: float
    tier: str          # "local" | "frontier"
    worker: object     # instantiated worker (has .name / .generate)


def registry_from_workers(workers, costs=None, tiers=None) -> list:
    """Build a registry from already-instantiated workers. `costs`/`tiers`
    (e.g. from perdura_providers.cost_tier_overrides) layer on top of
    DEFAULT_COSTS/DEFAULT_TIERS for config-defined names; any name in
    neither mapping defaults to free local."""
    costs = {**DEFAULT_COSTS, **(costs or {})}
    tiers = {**DEFAULT_TIERS, **(tiers or {})}
    return [ModelSpec(name=w.name,
                      cost=costs.get(w.name, 0.0),
                      tier=tiers.get(w.name, "local"),
                      worker=w)
            for w in workers]


def hottest_question(graph):
    """The most-contended open question, or None — the routing signal and
    (E2) the domain whose budget a turn's spend counts against."""
    qs = graph.open_questions()
    if not qs:
        return None
    return max(qs, key=lambda q: graph.contention(graph.neighborhood(q.id)))


def hottest_contention(graph) -> float:
    """Contention of the most-contended open question (the routing signal)."""
    q = hottest_question(graph)
    if q is None:
        return 0.0
    return graph.contention(graph.neighborhood(q.id))


@dataclass
class Router:
    registry: list
    policy: str = "periodic"        # periodic | contention | random | cheap
    budget: float = float("inf")    # session budget, in cost units
    escalate_at: float = DEFAULT_ESCALATE_AT   # contention threshold
    every: int = 3                  # escalate each Nth turn (periodic policy)
    p_escalate: float = 0.0         # escalation probability (random policy)
    seed: int = 0                   # rng seed (random policy, reproducible)
    # Per-domain caps (E2): tag -> budget, in the same cost units as
    # `budget`. A tag absent from this dict is uncapped — only the global
    # session budget applies. Spend is attributed to the domain tags of
    # whichever open question is hottest at decision time (the same
    # question the contention policy is reacting to); on explore turns the
    # conductor may then actually work a different question, which is a
    # known, documented approximation rather than exact per-question metering.
    domain_budgets: dict = field(default_factory=dict)
    domain_spent: dict = field(default_factory=dict)
    spent: float = 0.0
    ledger: list = field(default_factory=list)

    def __post_init__(self):
        self._rng = random.Random(self.seed)
        if not any(s.cost == 0.0 for s in self.registry):
            # A strictly zero-cost worker is the invariant _cheapest() leans
            # on: it stays affordable at any spend, so the session can never
            # stall. A local-but-priced worker wouldn't guarantee that.
            raise ValueError("router registry needs at least one local "
                             "(zero-cost) worker as the default labor force")

    # -- selection ----------------------------------------------------------
    def _affordable(self, spec, domains=()) -> bool:
        if self.spent + spec.cost > self.budget:
            return False
        return all(self.domain_spent.get(tag, 0.0) + spec.cost
                   <= self.domain_budgets[tag]
                   for tag in domains if tag in self.domain_budgets)

    def _cheapest(self, domains=()):
        affordable = [s for s in self.registry if self._affordable(s, domains)]
        # The zero-cost worker (guaranteed in __post_init__) is always
        # affordable, so `affordable` is non-empty in normal operation. The
        # fallback is last-resort defense: pick the absolute cheapest rather
        # than crash, even though it may nudge spend past the budget.
        return min(affordable or self.registry, key=lambda s: s.cost)

    def _best_frontier(self, graph, domains=()):
        """Highest reliability-per-cost among affordable frontier workers."""
        cands = [s for s in self.registry
                 if s.tier == "frontier" and self._affordable(s, domains)]
        if not cands:
            return None
        records = track_records(graph)
        def score(s):
            rel = records.get(s.name, {}).get("reliability", 0.5)
            return rel / max(s.cost, 0.01)
        return max(cands, key=lambda s: (score(s), -s.cost))

    def pick(self, graph, turn: int):
        """Choose the worker for this turn; returns the worker instance."""
        q = hottest_question(graph)
        domains = (q.domain_tags or []) if q else []
        contention = graph.contention(graph.neighborhood(q.id)) if q else 0.0
        if self.policy == "contention":
            escalate = contention >= self.escalate_at
        elif self.policy == "periodic":
            escalate = self.every > 0 and (turn + 1) % self.every == 0
        elif self.policy == "random":
            escalate = self._rng.random() < self.p_escalate
        elif self.policy == "cheap":
            escalate = False
        else:
            raise ValueError(f"unknown routing policy: {self.policy}")

        spec, reason = None, "default"
        if escalate:
            spec = self._best_frontier(graph, domains)
            reason = "escalate" if spec else "escalate-unaffordable"
        if spec is None:
            spec = self._cheapest(domains)
        self.spent += spec.cost
        for tag in domains:
            self.domain_spent[tag] = self.domain_spent.get(tag, 0.0) + spec.cost
        self.ledger.append({"turn": turn + 1, "worker": spec.name,
                            "cost": spec.cost, "reason": reason,
                            "contention": round(contention, 4),
                            "spent": round(self.spent, 4),
                            "domains": domains})
        return spec.worker

    # -- reporting ----------------------------------------------------------
    def summary(self) -> str:
        esc = [e for e in self.ledger if e["reason"] == "escalate"]
        denied = sum(1 for e in self.ledger
                     if e["reason"] == "escalate-unaffordable")
        lines = [f"Router ({self.policy}): {len(self.ledger)} turns, "
                 f"{len(esc)} escalations, spend {self.spent:g}"
                 + (f"/{self.budget:g}" if self.budget != float("inf") else "")
                 + (f", {denied} escalations denied by budget" if denied else "")]
        if self.domain_budgets:
            lines.append("  domain budgets: " + ", ".join(
                f"{tag} {self.domain_spent.get(tag, 0.0):g}/{cap:g}"
                for tag, cap in self.domain_budgets.items()))
        for e in self.ledger:
            mark = "↑" if e["reason"] == "escalate" else \
                   "✗" if e["reason"] == "escalate-unaffordable" else " "
            lines.append(f"  {mark} turn {e['turn']:>2}  {e['worker']:<8}"
                         f" cost {e['cost']:g}  contention {e['contention']}")
        return "\n".join(lines)
