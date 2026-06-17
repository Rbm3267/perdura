"""Mind-map viz (Phase 1.5) — render() must stay a pure function of the
graph (same data three ways: HUD counts, JSON blob, this test), and must
surface collision_candidates() so the cross-stream contradiction E3
ingestion exposes (tests/test_ingest.py::test_cross_stream_collision_detected)
is visible without running --audit-every separately."""

import json
import re

from perdura import Graph
from perdura_viz import render, write


def _data(html: str) -> dict:
    m = re.search(r"const DATA = (\{.*\});", html)
    assert m, "DATA blob not found in rendered HTML"
    return json.loads(m.group(1))


def test_render_embeds_nodes_and_edges(seeded):
    data = _data(render(seeded))
    ids = {n["id"] for n in data["nodes"]}
    assert {seeded.q, seeded.a, seeded.b} <= ids
    assert any(e["type"] == "contradicts" for e in data["edges"])
    assert data["collisions"] == []   # no cross-author lexical collision here


def test_render_surfaces_cross_stream_collision(graph):
    a = graph.add_node(
        "claim",
        "Connection pool exhaustion caused the outage in the billing service.",
        created_by="adapter:incident", confidence=0.8)
    b = graph.add_node(
        "claim",
        "The billing service outage was not caused by connection pool exhaustion.",
        created_by="adapter:adr", confidence=0.6)

    data = _data(render(graph))
    pairs = [{c["src"], c["dst"]} for c in data["collisions"]]
    assert {a, b} in pairs
    assert f"{len(data['collisions'])} collisions" in render(graph)


def test_write_creates_file(seeded, tmp_path):
    out = str(tmp_path / "mind.html")
    path = write(seeded, out)
    assert path == out
    with open(out, encoding="utf-8") as f:
        content = f.read()
    assert "perdura" in content and "DATA" in content
