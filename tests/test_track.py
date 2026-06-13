"""Phase 2 track records — outcome-based reliability, derived on demand.
Laplace-smoothed, conductor edges excluded, never scores user/conductor."""

from perdura import Graph
from perdura_track import track_records, _reliability


def test_no_evidence_is_one_half():
    # the Laplace prior: a worker with one untouched claim sits at 0.5
    assert _reliability(0.0, 0.0) == 0.5


def test_promoted_claim_raises_reliability(graph):
    c = graph.add_node("claim", "good idea", created_by="alice")
    d = graph.add_node("decision", "we will do this", created_by="bob")
    graph.add_edge("answers", c, d, "bob")   # claim linked to a decision
    rec = track_records(graph)["alice"]
    assert rec["good"] == 1.0
    assert rec["reliability"] > 0.5


def test_superseded_claim_lowers_reliability(graph):
    old = graph.add_node("claim", "wrong", created_by="alice")
    new = graph.add_node("claim", "right", created_by="bob")
    graph.supersede(old, new)
    rec = track_records(graph)["alice"]
    assert rec["bad"] == 1.0
    assert rec["reliability"] < 0.5


def test_challenged_claim_penalizes_earlier_endpoint(graph):
    a = graph.add_node("claim", "first", created_by="alice")
    b = graph.add_node("claim", "rebuttal", created_by="bob")
    # bob contradicts alice's earlier claim
    graph.add_edge("contradicts", b, a, "bob")
    recs = track_records(graph)
    assert recs["alice"]["bad"] == 0.5   # challenged
    assert recs["bob"]["bad"] == 0.0     # the challenger isn't penalized


def test_corroboration_requires_different_worker(graph):
    a = graph.add_node("claim", "claim", created_by="alice")
    s1 = graph.add_node("claim", "support from bob", created_by="bob")
    s2 = graph.add_node("claim", "self support", created_by="alice")
    graph.add_edge("supports", s1, a, "bob")     # counts
    graph.add_edge("supports", s2, a, "alice")   # same author: ignored
    assert track_records(graph)["alice"]["good"] == 0.5


def test_conductor_edges_never_count(graph):
    a = graph.add_node("claim", "a", created_by="alice")
    b = graph.add_node("claim", "b", created_by="alice")
    graph.add_edge("supports", b, a, "conductor")   # near-dup refines style
    rec = track_records(graph)["alice"]
    assert rec["good"] == 0.0


def test_user_and_conductor_not_scored(graph):
    graph.add_node("claim", "from user", created_by="user")
    graph.add_node("claim", "from conductor", created_by="conductor")
    assert track_records(graph) == {}


def test_per_domain_breakdown(graph):
    c = graph.add_node("claim", "x", created_by="alice", domain_tags=["arch"])
    d = graph.add_node("decision", "do it", created_by="bob")
    graph.add_edge("answers", c, d, "bob")
    rec = track_records(graph)["alice"]
    assert "arch" in rec["domains"]
    assert rec["domains"]["arch"]["good"] == 1.0
