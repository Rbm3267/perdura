"""Briefing invariants — bounded regardless of graph size, attribution
hidden from workers, settled decisions always in scope."""

from perdura import build_briefing, BRIEFING_CHAR_BUDGET


def test_briefing_is_bounded(graph):
    q = graph.add_node("question", "Big question?", created_by="user")
    # flood the neighborhood with far more claims than the budget can hold
    for i in range(400):
        c = graph.add_node("claim", f"Claim number {i} " + "padding " * 20,
                           created_by="w", confidence=0.5 + (i % 5) / 10)
        graph.add_edge("answers", c, q, "w")
    briefing = build_briefing(graph, graph.nodes[q])
    # node section + edge section are each separately bounded; the whole
    # prompt stays within a small multiple of the budget no matter the graph
    assert len(briefing) < BRIEFING_CHAR_BUDGET * 2


def test_attribution_hidden_from_workers(seeded):
    briefing = build_briefing(seeded, seeded.nodes[seeded.q])
    # workers must never see authorship of prior nodes (anti-anchoring)
    assert "alice" not in briefing
    assert "bob" not in briefing


def test_confidence_shown_then_maskable(seeded):
    shown = build_briefing(seeded, seeded.nodes[seeded.q])
    assert "0.80" in shown or "0.8" in shown
    masked = build_briefing(seeded, seeded.nodes[seeded.q],
                            mask_confidence=True)
    # masked variant replaces the score with a placeholder
    assert "0.80" not in masked
    assert " -- " in masked


def test_live_decisions_always_pinned(graph):
    # a structurally isolated question still inherits settled policy
    q = graph.add_node("question", "Fresh unrelated question?",
                       created_by="user")
    d = graph.add_node("decision", "Settled: briefings are bounded.",
                       created_by="w", confidence=0.9)
    briefing = build_briefing(graph, graph.nodes[q])
    assert d in briefing


def test_superseded_nodes_excluded(graph):
    q = graph.add_node("question", "Q?", created_by="user")
    old = graph.add_node("claim", "stale idea", created_by="w")
    new = graph.add_node("claim", "current idea", created_by="w")
    graph.add_edge("answers", old, q, "w")
    graph.add_edge("answers", new, q, "w")
    graph.supersede(old, new)
    briefing = build_briefing(graph, graph.nodes[q])
    assert old not in briefing
