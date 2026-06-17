"""Phase 3 router — contention-driven escalation under hard budgets.
Worker selection only; the merge path is untouched."""

import pytest

from perdura import Graph
from perdura_router import Router, ModelSpec, registry_from_workers


class _W:
    def __init__(self, name):
        self.name = name


def _reg():
    return [ModelSpec("qwen", 0.0, "local", _W("qwen")),
            ModelSpec("claude", 3.0, "frontier", _W("claude"))]


def _contested(tmp_path):
    g = Graph(str(tmp_path / "g.json"))
    q = g.add_node("question", "Q?", created_by="user", confidence=1.0)
    a = g.add_node("claim", "a", created_by="x", confidence=0.9)
    b = g.add_node("claim", "b", created_by="y", confidence=0.9)
    g.add_edge("answers", a, q, "x")
    g.add_edge("answers", b, q, "y")
    g.add_edge("contradicts", b, a, "y")
    return g


def test_requires_zero_cost_worker():
    with pytest.raises(ValueError):
        Router(registry=[ModelSpec("paid", 1.0, "local", _W("paid"))])


def test_calm_graph_stays_local(tmp_path):
    g = Graph(str(tmp_path / "g.json"))
    q = g.add_node("question", "Q?", created_by="user")
    g.add_node("claim", "lonely claim", created_by="x")
    r = Router(registry=_reg(), policy="contention", escalate_at=0.15)
    assert r.pick(g, 0).name == "qwen"


def test_contended_graph_escalates(tmp_path):
    g = _contested(tmp_path)
    r = Router(registry=_reg(), policy="contention", escalate_at=0.15)
    assert r.pick(g, 0).name == "claude"
    assert r.ledger[-1]["reason"] == "escalate"


def test_budget_forces_local_fallback(tmp_path):
    g = _contested(tmp_path)
    r = Router(registry=_reg(), policy="contention", escalate_at=0.15,
               budget=3.0)
    assert r.pick(g, 0).name == "claude"   # spends the whole budget
    assert r.pick(g, 1).name == "qwen"     # can't afford claude now
    assert any(e["reason"] == "escalate-unaffordable" for e in r.ledger)
    assert r.spent <= r.budget             # hard budget never exceeded


def test_cheap_policy_never_escalates(tmp_path):
    g = _contested(tmp_path)
    r = Router(registry=_reg(), policy="cheap")
    assert all(r.pick(g, t).name == "qwen" for t in range(3))
    assert r.spent == 0.0


def test_periodic_policy_escalates_on_schedule(tmp_path):
    g = _contested(tmp_path)
    r = Router(registry=_reg(), policy="periodic", every=3)
    picks = [r.pick(g, t).name for t in range(3)]
    assert picks == ["qwen", "qwen", "claude"]


def test_registry_from_workers_assigns_tiers():
    reg = registry_from_workers([_W("qwen"), _W("claude"), _W("gemini")])
    tiers = {s.name: s.tier for s in reg}
    assert tiers["qwen"] == "local"
    assert tiers["claude"] == "frontier" and tiers["gemini"] == "frontier"


def test_ledger_records_every_decision(tmp_path):
    g = _contested(tmp_path)
    r = Router(registry=_reg(), policy="contention")
    for t in range(3):
        r.pick(g, t)
    assert len(r.ledger) == 3
    assert all("contention" in e and "reason" in e for e in r.ledger)


def _contested_with_domain(tmp_path, domain):
    g = Graph(str(tmp_path / "g.json"))
    q = g.add_node("question", "Q?", created_by="user", confidence=1.0,
                   domain_tags=[domain])
    a = g.add_node("claim", "a", created_by="x", confidence=0.9)
    b = g.add_node("claim", "b", created_by="y", confidence=0.9)
    g.add_edge("answers", a, q, "x")
    g.add_edge("answers", b, q, "y")
    g.add_edge("contradicts", b, a, "y")
    return g


def test_domain_budget_blocks_escalation_even_under_global_budget(tmp_path):
    g = _contested_with_domain(tmp_path, "legal")
    r = Router(registry=_reg(), policy="contention", escalate_at=0.15,
              domain_budgets={"legal": 0.0})   # plenty of global budget
    assert r.pick(g, 0).name == "qwen"
    assert r.ledger[-1]["reason"] == "escalate-unaffordable"


def test_domain_budget_does_not_cap_other_domains(tmp_path):
    g = _contested_with_domain(tmp_path, "legal")
    r = Router(registry=_reg(), policy="contention", escalate_at=0.15,
              domain_budgets={"unrelated-domain": 0.0})
    assert r.pick(g, 0).name == "claude"   # the cap doesn't apply to "legal"


def test_domain_spend_accumulates_per_tag(tmp_path):
    g = _contested_with_domain(tmp_path, "legal")
    r = Router(registry=_reg(), policy="contention", escalate_at=0.15,
              domain_budgets={"legal": 3.0})
    assert r.pick(g, 0).name == "claude"            # spends 3.0 on "legal"
    assert r.domain_spent["legal"] == 3.0
    assert r.pick(g, 1).name == "qwen"              # "legal" budget exhausted
    assert r.ledger[-1]["reason"] == "escalate-unaffordable"


def test_uncapped_domain_is_unaffected_by_domain_budgets_dict(tmp_path):
    g = _contested(tmp_path)   # no domain tags on the question at all
    r = Router(registry=_reg(), policy="contention", escalate_at=0.15,
              domain_budgets={"legal": 0.0})
    assert r.pick(g, 0).name == "claude"
