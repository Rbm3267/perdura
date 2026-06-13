"""End-to-end CLI smoke + the redact compliance escape hatch.

These drive perdura.py as a subprocess (mock worker, no keys) so the
argparse wiring, run loop, and operator commands are exercised for real.
"""

import subprocess
import sys
from pathlib import Path

from perdura import Graph

ROOT = Path(__file__).resolve().parent.parent


def _run(*args, **kw):
    return subprocess.run([sys.executable, "perdura.py", *args],
                          cwd=ROOT, capture_output=True, text=True,
                          check=True, **kw)


def test_new_run_show_offline(tmp_path):
    gp = str(tmp_path / "g.json")
    _run("new", "How should briefings be bounded?", "--graph", gp)
    _run("run", "--graph", gp, "--workers", "mock", "--turns", "3")
    g = Graph(gp)
    assert g.open_questions() or g.nodes          # the loop produced state
    assert any(n.created_by == "mock" for n in g.nodes.values())


def test_router_cli_runs(tmp_path):
    gp = str(tmp_path / "g.json")
    _run("new", "Contested question?", "--graph", gp)
    out = _run("run", "--graph", gp, "--workers", "mock",
               "--turns", "3", "--route", "contention")
    assert "Router (contention)" in out.stdout


def test_redact_destroys_text_preserves_structure(tmp_path):
    gp = str(tmp_path / "g.json")
    _run("new", "Q with PII?", "--graph", gp)
    _run("run", "--graph", gp, "--workers", "mock", "--turns", "2")

    g = Graph(gp)
    target = next(n.id for n in g.nodes.values() if n.type == "claim")
    edges_before = {e.id for e in g.edges.values()
                    if target in (e.src, e.dst)}
    author_before = g.nodes[target].created_by
    assert edges_before                            # the claim has structure

    _run("redact", target, "--graph", gp)

    g2 = Graph(gp)
    assert target in g2.nodes                      # node survives
    assert g2.nodes[target].text == "[redacted]"   # text destroyed
    assert g2.nodes[target].created_by == author_before  # attribution kept
    # every edge touching the node survives (lineage preserved)
    assert edges_before <= set(g2.edges)
    # the redaction is logged
    assert any(e.get("redacted") == target for e in g2.log)
