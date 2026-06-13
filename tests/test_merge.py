"""Conductor merge invariants — the deterministic validate/attribute/merge
path. No LLM judgment here, so every rule is exactly assertable."""

from perdura import Graph, merge_delta, NODE_TYPES, EDGE_TYPES


def test_valid_delta_merges_and_attributes(graph):
    q = graph.add_node("question", "Q?", created_by="user", confidence=1.0)
    acc, rej = merge_delta(graph, {
        "new_nodes": [{"ref": "a", "type": "claim", "text": "A claim.",
                       "confidence": 0.6, "domain_tags": ["x"]}],
        "new_edges": [{"type": "answers", "src": "a", "dst": q}],
    }, worker="claude")
    assert (acc, rej) == (2, 0)
    claims = [n for n in graph.nodes.values() if n.type == "claim"]
    assert len(claims) == 1
    # attribution is stamped by the conductor, from the worker arg
    assert claims[0].created_by == "claude"


def test_bad_node_type_rejected(graph):
    acc, rej = merge_delta(graph, {
        "new_nodes": [{"ref": "a", "type": "banana", "text": "nope"}],
    }, worker="w")
    assert (acc, rej) == (0, 1)
    assert not graph.nodes


def test_empty_text_rejected(graph):
    acc, rej = merge_delta(graph, {
        "new_nodes": [{"ref": "a", "type": "claim", "text": "   "}],
    }, worker="w")
    assert (acc, rej) == (0, 1)


def test_unknown_edge_type_rejected(graph):
    a = graph.add_node("claim", "a", created_by="w")
    b = graph.add_node("claim", "b", created_by="w")
    acc, rej = merge_delta(graph, {
        "new_edges": [{"type": "frobnicates", "src": a, "dst": b}],
    }, worker="w")
    assert (acc, rej) == (0, 1)


def test_self_loop_edge_rejected(graph):
    a = graph.add_node("claim", "a", created_by="w")
    acc, rej = merge_delta(graph, {
        "new_edges": [{"type": "supports", "src": a, "dst": a}],
    }, worker="w")
    assert (acc, rej) == (0, 1)


def test_edge_to_unknown_node_rejected(graph):
    a = graph.add_node("claim", "a", created_by="w")
    acc, rej = merge_delta(graph, {
        "new_edges": [{"type": "supports", "src": a, "dst": "n_ghost"}],
    }, worker="w")
    assert (acc, rej) == (0, 1)


def test_ref_resolution_within_delta(graph):
    # an edge can reference two nodes created in the same delta by ref
    acc, rej = merge_delta(graph, {
        "new_nodes": [{"ref": "a", "type": "claim", "text": "a"},
                      {"ref": "b", "type": "evidence", "text": "b"}],
        "new_edges": [{"type": "supports", "src": "b", "dst": "a"}],
    }, worker="w")
    assert (acc, rej) == (3, 0)
    assert len(graph.edges) == 1


def test_supersede_marks_never_deletes(graph):
    old = graph.add_node("claim", "old", created_by="w")
    acc, rej = merge_delta(graph, {
        "new_nodes": [{"ref": "n", "type": "claim", "text": "new"}],
        "supersedes": [{"old": old, "new": "n"}],
    }, worker="w")
    # the old node is still present, just marked
    assert old in graph.nodes
    assert graph.nodes[old].superseded_by is not None
    assert graph.nodes[old] not in graph.live_nodes()


def test_resolve_questions_only_targets_questions(graph):
    q = graph.add_node("question", "Q?", created_by="user")
    c = graph.add_node("claim", "C", created_by="w")
    merge_delta(graph, {"resolve_questions": [q, c]}, worker="w")
    assert graph.nodes[q].status == "resolved"
    # a claim id passed to resolve_questions must be ignored, not mutated
    assert graph.nodes[c].status == "open"


def test_null_and_missing_fields_tolerated(graph):
    # models emit null/missing sections; merge must not crash
    acc, rej = merge_delta(graph, {"new_nodes": None, "new_edges": None},
                           worker="w")
    assert (acc, rej) == (0, 0)
    acc, rej = merge_delta(graph, {}, worker="w")
    assert (acc, rej) == (0, 0)


def test_log_appended_every_merge(graph):
    before = len(graph.log)
    merge_delta(graph, {"new_nodes": [{"type": "claim", "text": "x"}]},
                worker="w")
    assert len(graph.log) == before + 1
    assert graph.log[-1]["worker"] == "w"


def test_near_dup_gets_conductor_refines_edge(graph):
    # two near-identical claims should be linked by a conductor-authored
    # refines edge (near-dup consolidation), nothing suppressed
    graph.add_node("claim", "Bounded briefings keep cost flat as graphs grow.",
                   created_by="alice")
    merge_delta(graph, {"new_nodes": [
        {"ref": "a", "type": "claim",
         "text": "Bounded briefings keep cost flat as graphs grow."}]},
        worker="bob")
    refines = [e for e in graph.edges.values()
               if e.type == "refines" and e.created_by == "conductor"]
    assert len(refines) == 1


def test_schema_sets_are_the_contract():
    assert NODE_TYPES == {"question", "claim", "evidence", "decision",
                          "rejected"}
    assert EDGE_TYPES == {"supports", "contradicts", "refines", "answers",
                          "depends_on"}
