"""boarding_mode provenance on Node/Edge — lets analysis (memoric_eval.py,
collision_probe.py) separate organic/audit disagreement from exogenous
--adversarial-every contradictions (docs/phase0-validation.md)."""

import json

from perdura import Graph, merge_delta


def test_add_node_and_add_edge_default_to_organic(graph):
    a = graph.add_node("claim", "a", created_by="w")
    b = graph.add_node("claim", "b", created_by="w")
    e = graph.add_edge("contradicts", a, b, "w")
    assert graph.nodes[a].boarding_mode == "organic"
    assert graph.edges[e].boarding_mode == "organic"


def test_merge_delta_propagates_explicit_boarding_mode(graph):
    q = graph.add_node("question", "Q?", created_by="user")
    acc, rej = merge_delta(graph, {
        "new_nodes": [{"ref": "a", "type": "claim", "text": "A claim."}],
        "new_edges": [{"type": "answers", "src": "a", "dst": q}],
    }, worker="claude", boarding_mode="adversarial")
    assert (acc, rej) == (2, 0)
    claims = [n for n in graph.nodes.values() if n.type == "claim"]
    assert claims[0].boarding_mode == "adversarial"
    answers_edges = [e for e in graph.edges.values() if e.type == "answers"]
    assert answers_edges[0].boarding_mode == "adversarial"


def test_merge_delta_defaults_to_organic_when_unspecified(graph):
    acc, rej = merge_delta(graph, {
        "new_nodes": [{"ref": "a", "type": "claim", "text": "A claim."}],
    }, worker="w")
    assert (acc, rej) == (1, 0)
    claim = next(n for n in graph.nodes.values() if n.type == "claim")
    assert claim.boarding_mode == "organic"


def test_near_dup_refines_edge_inherits_boarding_mode(graph):
    graph.add_node("claim", "Bounded briefings keep cost flat as graphs grow.",
                   created_by="alice")
    merge_delta(graph, {"new_nodes": [
        {"ref": "a", "type": "claim",
         "text": "Bounded briefings keep cost flat as graphs grow."}]},
        worker="bob", boarding_mode="audit")
    refines = [e for e in graph.edges.values()
               if e.type == "refines" and e.created_by == "conductor"]
    assert len(refines) == 1
    assert refines[0].boarding_mode == "audit"


def test_old_graph_dicts_missing_boarding_mode_load_as_organic(tmp_path):
    # A graph file written before boarding_mode existed has no such key
    # on nodes or edges — Node/Edge's dataclass default must backfill it.
    path = tmp_path / "old.json"
    path.write_text(json.dumps({
        "nodes": [{"id": "n_1", "type": "claim", "text": "a",
                   "domain_tags": [], "created_by": "w", "confidence": 0.5,
                   "created_at": 0.0, "status": "open",
                   "superseded_by": None}],
        "edges": [{"id": "e_1", "type": "contradicts", "src": "n_1",
                   "dst": "n_1", "created_by": "w", "created_at": 0.0}],
        "log": [],
    }))
    graph = Graph(str(path))
    assert graph.nodes["n_1"].boarding_mode == "organic"
    assert graph.edges["e_1"].boarding_mode == "organic"
