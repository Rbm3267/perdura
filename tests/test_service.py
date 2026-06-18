"""Enterprise E1 service API — bearer auth, the worker/operator boundary,
and the three planes. Runs a real server on a loopback port."""

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from perdura import Graph
from perdura_service import make_handler

WTOK, OTOK = "worker-token", "operator-token"


@pytest.fixture
def service(tmp_path):
    gp = str(tmp_path / "g.json")
    g = Graph(gp)
    q = g.add_node("question", "Fixed or scaling budget?", created_by="user",
                   confidence=1.0)
    a = g.add_node("claim", "Scale the budget with the graph.",
                   created_by="alice", confidence=0.8)
    b = g.add_node("claim", "Keep the budget fixed and bounded.",
                   created_by="bob", confidence=0.7)
    g.add_edge("answers", a, q, "alice")
    g.add_edge("answers", b, q, "bob")
    g.add_edge("contradicts", b, a, "bob")
    g.save()

    tokens = {WTOK: "worker", OTOK: "operator"}
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(gp, tokens))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield SimpleNamespace(base=f"http://127.0.0.1:{httpd.server_address[1]}",
                              gp=gp, q=q)
    finally:
        httpd.shutdown()
        httpd.server_close()


def call(base, method, path, token=None, body=None):
    req = urllib.request.Request(base + path, method=method)
    if token:
        req.add_header("Authorization", "Bearer " + token)
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data=data) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def test_health_needs_no_auth(service):
    assert call(service.base, "GET", "/health")[0] == 200


def test_missing_token_is_401(service):
    assert call(service.base, "GET", "/questions")[0] == 401


def test_unknown_token_is_401(service):
    assert call(service.base, "GET", "/questions", token="nope")[0] == 401


def test_worker_can_read_briefing_and_questions(service):
    assert call(service.base, "GET", "/questions", token=WTOK)[0] == 200
    assert call(service.base, "GET", "/contention", token=WTOK)[0] == 200
    code, body = call(service.base, "GET", "/briefing", token=WTOK)
    assert code == 200 and body["question_id"] == service.q


def test_briefing_hides_attribution(service):
    _, body = call(service.base, "GET", "/briefing", token=WTOK)
    # the worker plane must never leak authorship of prior nodes
    assert "alice" not in body["briefing_prompt"]
    assert "bob" not in body["briefing_prompt"]


def test_worker_forbidden_from_operator_routes(service):
    assert call(service.base, "GET", "/track", token=WTOK)[0] == 403
    assert call(service.base, "GET", "/graph", token=WTOK)[0] == 403
    assert call(service.base, "GET", "/viz", token=WTOK)[0] == 403


def test_operator_sees_track_and_attribution(service):
    assert call(service.base, "GET", "/track", token=OTOK)[0] == 200
    code, body = call(service.base, "GET", "/graph", token=OTOK)
    assert code == 200
    authors = {n["by"] for n in body["nodes"]}
    assert "alice" in authors and "bob" in authors   # attributed view


def test_operator_viz_renders_live_html_without_attribution(service):
    req = urllib.request.Request(service.base + "/viz", method="GET")
    req.add_header("Authorization", "Bearer " + OTOK)
    with urllib.request.urlopen(req) as r:
        assert r.status == 200
        assert r.headers["Content-Type"].startswith("text/html")
        html = r.read().decode()
    assert "perdura" in html and "const DATA" in html
    assert "alice" not in html and "bob" not in html   # unattributed, like /briefing


def test_delta_merges_through_conductor(service):
    delta = {"new_nodes": [{"ref": "c", "type": "claim",
                            "text": "A third position via the API.",
                            "confidence": 0.6}],
             "new_edges": [{"type": "answers", "src": "c", "dst": service.q}]}
    code, body = call(service.base, "POST", "/deltas", token=WTOK,
                      body={"worker": "api-client", "delta": delta})
    assert code == 200 and body["accepted"] == 2
    # the merge actually landed in the shared graph file
    g = Graph(service.gp)
    assert any(n.created_by == "api-client" for n in g.nodes.values())


def test_delta_requires_token(service):
    assert call(service.base, "POST", "/deltas",
                body={"delta": {}})[0] == 401


def test_malformed_delta_is_400(service):
    code, _ = call(service.base, "POST", "/deltas", token=WTOK,
                   body={"delta": "this is not json"})
    assert code == 400


def test_unknown_route_is_404(service):
    assert call(service.base, "GET", "/nope", token=OTOK)[0] == 404
