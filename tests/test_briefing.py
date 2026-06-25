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


def test_memoric_briefing_off_by_default(seeded):
    # default output must stay byte-identical to Phase 1 (issue: opt-in
    # only, never changes the baseline arm)
    default = build_briefing(seeded, seeded.nodes[seeded.q])
    explicit_off = build_briefing(seeded, seeded.nodes[seeded.q],
                                  memoric_briefing=False)
    assert default == explicit_off
    assert "collision" not in default


def test_memoric_briefing_surfaces_unlinked_collision(graph):
    # a claim lexically close to one already in the briefing, but written
    # by a different worker on an unrelated question with no edge between
    # them, should still surface once --memoric-briefings is on.
    q1 = graph.add_node("question", "Should briefings stay fixed-size?",
                        created_by="user", confidence=1.0)
    anchor = graph.add_node(
        "claim", "Worker briefing budgets must remain fixed and bounded "
        "regardless of how large the graph grows over time.",
        created_by="alice", confidence=0.8)
    graph.add_edge("answers", anchor, q1, "alice")

    q2 = graph.add_node("question", "Should retrieval scale with size?",
                        created_by="user", confidence=1.0)
    echo = graph.add_node(
        "claim", "Worker briefing budgets must scale up and grow as large "
        "as the graph grows over time, never staying fixed.",
        created_by="bob", confidence=0.7)
    graph.add_edge("answers", echo, q2, "bob")

    without = build_briefing(graph, graph.nodes[q1])
    assert echo not in without

    with_echo = build_briefing(graph, graph.nodes[q1], memoric_briefing=True)
    assert anchor in with_echo
    assert echo in with_echo
    assert "Epistemically close" in with_echo


def test_memoric_briefing_stays_bounded(graph):
    q = graph.add_node("question", "Big question?", created_by="user")
    anchor = graph.add_node("claim", "the anchor claim text here",
                            created_by="alice", confidence=0.9)
    graph.add_edge("answers", anchor, q, "alice")
    # flood the graph with collision-band claims attributed to a different
    # worker, all unlinked, all outside the briefing's selected set
    for i in range(40):
        graph.add_node(
            "claim", f"the anchor claim text here variant {i} " + "x" * 40,
            created_by="bob", confidence=0.5)
    briefing = build_briefing(graph, graph.nodes[q], memoric_briefing=True)
    assert len(briefing) < BRIEFING_CHAR_BUDGET * 2
