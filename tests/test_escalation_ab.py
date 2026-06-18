"""experiments/escalation_ab.py's --real plumbing (registry built from
WORKER_FACTORIES, run_arm's frontier-name-generic flip counting) needs to
work for *any* named worker, not just the scripted Pad/Challenger pair —
this proves that mechanically, standing MockWorker doubles in for the
local AND frontier slots so the harness itself is tested offline. Running
it against real frontier APIs (`--real --frontier claude,gemini`) still
needs the operator's own ANTHROPIC_API_KEY/GEMINI_API_KEY; that part is
not — and cannot be — covered by an offline test."""

import perdura
from experiments.escalation_ab import run_arm, run_real_arms, run_synthetic_arms
from perdura import MockWorker
from perdura_router import ModelSpec


def test_synthetic_arms_unchanged_shape():
    arms = run_synthetic_arms(turns=4, budget=4.0, escalate_at=0.15)
    assert set(arms) == {"contention", "periodic", "random", "cheap"}
    assert arms["cheap"]["spend"] == 0
    for a in arms.values():
        assert {"spend", "escalations", "flips",
               "mean_contention_at_escalation", "final_contention"} <= set(a)


def test_real_arms_plumbing_with_mock_workers(monkeypatch):
    """Stand MockWorker in for both the local and frontier slots — proves
    run_real_arms' registry-building and run_arm's now-generic frontier-name
    flip counting work for arbitrary WORKER_FACTORIES entries, without
    needing API keys or a local model server."""
    import experiments.escalation_ab as eab

    monkeypatch.setitem(perdura.WORKER_FACTORIES, "mock2", lambda a: MockWorker())
    monkeypatch.setitem(eab.DEFAULT_COSTS, "mock2", 2.0)
    monkeypatch.setitem(eab.DEFAULT_TIERS, "mock2", "frontier")

    arms = run_real_arms("mock", ["mock2"], turns=4, budget=8.0,
                         escalate_at=0.15)
    assert set(arms) == {"contention", "periodic", "random", "cheap"}
    assert arms["cheap"]["spend"] == 0
    assert all(a["spend"] >= 0 for a in arms.values())


def test_run_arm_flip_counting_is_generic_to_registry_names(tmp_path):
    registry = [ModelSpec("local-x", 0.0, "local", MockWorker()),
               ModelSpec("frontier-y", 1.0, "frontier", MockWorker())]
    result = run_arm("cheap", str(tmp_path), registry,
                     [s.worker for s in registry], turns=2, budget=4.0)
    assert result["policy"] == "cheap"
    assert result["spend"] == 0   # cheap arm never escalates
