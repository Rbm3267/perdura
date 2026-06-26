"""Storage tier — JSON and SQLite stores must be behaviorally identical:
same round-trip, append-only log, supersession surviving reload."""

import os

import pytest

from perdura import Graph
from perdura_store import store_for, JSONFileStore, SQLiteStore


def test_store_selected_by_extension(tmp_path):
    assert isinstance(store_for(str(tmp_path / "g.json")), JSONFileStore)
    for ext in (".db", ".sqlite", ".sqlite3"):
        assert isinstance(store_for(str(tmp_path / ("g" + ext))), SQLiteStore)


@pytest.mark.parametrize("ext", [".json", ".db", ".sqlite3"])
def test_round_trip(tmp_path, ext):
    path = str(tmp_path / ("g" + ext))
    g = Graph(path)
    q = g.add_node("question", "Q?", created_by="user", confidence=1.0)
    c = g.add_node("claim", "C", created_by="w", confidence=0.6,
                   domain_tags=["d"])
    g.add_edge("answers", c, q, "w")
    g.save()

    back = Graph(path)
    assert set(back.nodes) == set(g.nodes)
    assert set(back.edges) == set(g.edges)
    assert back.nodes[c].domain_tags == ["d"]
    assert back.nodes[c].confidence == 0.6


@pytest.mark.parametrize("ext", [".json", ".db"])
def test_supersession_survives_reload(tmp_path, ext):
    path = str(tmp_path / ("g" + ext))
    g = Graph(path)
    old = g.add_node("claim", "old", created_by="w")
    new = g.add_node("claim", "new", created_by="w")
    g.supersede(old, new)
    g.save()
    back = Graph(path)
    assert back.nodes[old].superseded_by == new
    assert back.nodes[old] not in back.live_nodes()


@pytest.mark.parametrize("ext", [".json", ".db"])
def test_log_is_append_only_across_saves(tmp_path, ext):
    path = str(tmp_path / ("g" + ext))
    g = Graph(path)
    g.log.append({"ts": 1, "worker": "a", "accepted": 1, "rejected": 0})
    g.save()
    g.save()  # saving twice must not duplicate existing log entries
    g.log.append({"ts": 2, "worker": "b", "accepted": 1, "rejected": 0})
    g.save()
    back = Graph(path)
    assert len(back.log) == 2
    assert [e["worker"] for e in back.log] == ["a", "b"]


def test_missing_file_is_empty_graph(tmp_path):
    g = Graph(str(tmp_path / "does_not_exist.json"))
    assert g.nodes == {} and g.edges == {} and g.log == []


@pytest.mark.skipif(os.geteuid() == 0,
                    reason="root bypasses file permission bits")
def test_ping_checks_existing_file_writability_not_just_directory(tmp_path):
    # A writable directory isn't sufficient once the file itself exists and
    # is read-only -- ping() must catch that case, not just probe the dir.
    path = tmp_path / "g.json"
    JSONFileStore(str(path)).save([], [], [])
    assert JSONFileStore(str(path)).ping() is True
    os.chmod(path, 0o444)
    try:
        assert JSONFileStore(str(path)).ping() is False
    finally:
        os.chmod(path, 0o644)  # restore so tmp_path cleanup can remove it


def test_ping_checks_directory_when_file_does_not_exist_yet(tmp_path):
    assert JSONFileStore(str(tmp_path / "fresh.json")).ping() is True
    assert JSONFileStore(str(tmp_path / "missing-dir" / "g.json")).ping() is False
