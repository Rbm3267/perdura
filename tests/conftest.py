"""Shared fixtures for the Perdura test suite.

Tests build graphs at tmp paths and drive the conductor directly
(merge_delta) rather than through live workers, so the whole suite runs
offline and deterministically — no API keys, no local model server.
"""

import pytest

from perdura import Graph


@pytest.fixture
def graph(tmp_path):
    """An empty graph backed by a throwaway JSON file."""
    return Graph(str(tmp_path / "g.json"))


@pytest.fixture
def seeded(graph):
    """A graph with one open question and two contradicting claims."""
    q = graph.add_node("question", "Fixed or scaling briefing budget?",
                       created_by="user", confidence=1.0)
    a = graph.add_node("claim", "Budgets must scale with graph size.",
                       created_by="alice", confidence=0.8)
    b = graph.add_node("claim", "Budgets must stay fixed and bounded.",
                       created_by="bob", confidence=0.7)
    graph.add_edge("answers", a, q, "alice")
    graph.add_edge("answers", b, q, "bob")
    graph.add_edge("contradicts", b, a, "bob")
    graph.q, graph.a, graph.b = q, a, b
    return graph
