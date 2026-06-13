"""Contention metric — the routing signal. Edge-only by default
(memoric_weight 0.0 until Phase 0 validation passes)."""

from perdura import Graph


def test_zero_without_contradictions(graph):
    q = graph.add_node("question", "Q?", created_by="user")
    a = graph.add_node("claim", "a", created_by="w", confidence=0.8)
    graph.add_edge("answers", a, q, "w")
    assert graph.contention() == 0.0


def test_zero_without_claims(graph):
    graph.add_node("question", "Q?", created_by="user")
    assert graph.contention() == 0.0


def test_contradiction_raises_contention(seeded):
    # one contradicts edge over two claims, confidence-weighted
    # (0.8 + 0.7)/2 = 0.75 contra mass / 2 claims = 0.375
    assert seeded.contention() == round(0.75 / 2, 3)


def test_default_weight_is_edge_only(graph):
    # the memoric blend stays opt-in: default must be edge-only (w=0)
    assert graph.memoric_weight == 0.0


def test_neighborhood_scopes_contention(seeded):
    # contention restricted to a node set only counts edges within it
    hood = seeded.neighborhood(seeded.q)
    assert seeded.contention(hood) > 0.0
    # an empty/foreign scope has no claims -> zero
    assert seeded.contention({"n_nonexistent"}) == 0.0


def test_neighborhood_is_two_hops(graph):
    a = graph.add_node("claim", "a", created_by="w")
    b = graph.add_node("claim", "b", created_by="w")
    c = graph.add_node("claim", "c", created_by="w")
    d = graph.add_node("claim", "d", created_by="w")
    graph.add_edge("supports", b, a, "w")   # 1 hop from a
    graph.add_edge("supports", c, b, "w")   # 2 hops from a
    graph.add_edge("supports", d, c, "w")   # 3 hops from a
    hood = graph.neighborhood(a, hops=2)
    assert a in hood and b in hood and c in hood
    assert d not in hood
