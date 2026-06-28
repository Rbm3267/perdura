"""Service observability/productization features: readiness, structured
logging, rate limiting, usage metering. All off-by-default except /ready
and /health, which need no opt-in since they carry no tenant data."""

import json
import logging
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from perdura import Graph
import perdura_service as svc
from perdura_service import make_handler

WTOK, OTOK = "worker-token", "operator-token"


def _start(gp, tokens, **kw):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(gp, tokens, **kw))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


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
            return r.status, dict(r.headers), json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), json.loads(e.read() or b"null")


@pytest.fixture
def service(tmp_path):
    gp = str(tmp_path / "g.json")
    Graph(gp).save()
    tokens = {WTOK: "worker", OTOK: "operator"}
    httpd = _start(gp, tokens)
    try:
        yield SimpleNamespace(base=f"http://127.0.0.1:{httpd.server_address[1]}", gp=gp)
    finally:
        httpd.shutdown()
        httpd.server_close()


# -- /ready -------------------------------------------------------------

def test_ready_is_200_when_store_reachable(service):
    code, _, body = call(service.base, "GET", "/ready")
    assert code == 200 and body["status"] == "ready"


def test_ready_needs_no_auth(service):
    code, _, _ = call(service.base, "GET", "/ready")
    assert code == 200


def test_ready_is_503_when_store_unreachable(tmp_path):
    # the directory containing the graph file doesn't exist -> JSONFileStore
    # can never write there, so readiness must report unavailable
    gp = str(tmp_path / "missing-dir" / "g.json")
    httpd = _start(gp, {WTOK: "worker"})
    try:
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        code, _, body = call(base, "GET", "/ready")
        assert code == 503 and body["status"] == "unavailable"
    finally:
        httpd.shutdown()
        httpd.server_close()


# -- structured logging --------------------------------------------------

def test_requests_are_logged_structurally(service, caplog):
    with caplog.at_level(logging.INFO, logger="perdura.service"):
        call(service.base, "GET", "/questions", token=WTOK)
    records = [r for r in caplog.records if r.name == "perdura.service"]
    assert any("GET" in r.message and "/questions" in r.message
               and "status=200" in r.message for r in records)


def test_log_message_does_not_print_to_stderr(service, capsys):
    # the raw BaseHTTPRequestHandler access line must not leak to the
    # terminal the way the old silent override avoided -- it should be
    # routed through logging instead, at DEBUG (so silent at default INFO)
    call(service.base, "GET", "/health")
    captured = capsys.readouterr()
    assert captured.err == ""


# -- rate limiting --------------------------------------------------------

def test_rate_limit_disabled_by_default(service):
    for _ in range(10):
        code, _, _ = call(service.base, "GET", "/questions", token=WTOK)
        assert code == 200


def test_rate_limit_returns_429_with_retry_after(tmp_path):
    gp = str(tmp_path / "g.json")
    Graph(gp).save()
    httpd = _start(gp, {WTOK: "worker"}, rate_limit_per_minute=2)
    try:
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        call(base, "GET", "/questions", token=WTOK)
        call(base, "GET", "/questions", token=WTOK)
        code, headers, body = call(base, "GET", "/questions", token=WTOK)
        assert code == 429
        assert headers["Retry-After"] == "60"
        assert "error" in body
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_rate_limit_is_keyed_per_token(tmp_path):
    # a second credential must get its own budget, not share the first's
    gp = str(tmp_path / "g.json")
    Graph(gp).save()
    httpd = _start(gp, {WTOK: "worker", OTOK: "operator"}, rate_limit_per_minute=1)
    try:
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        assert call(base, "GET", "/questions", token=WTOK)[0] == 200
        assert call(base, "GET", "/questions", token=WTOK)[0] == 429
        assert call(base, "GET", "/track", token=OTOK)[0] == 200
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_rate_limiter_prunes_expired_windows_past_cap(monkeypatch):
    # An attacker rotating through bogus keys (forged tokens, spoofed IPs)
    # must not grow _windows forever -- once over the cap, the next call
    # sweeps anything whose 60s window has already lapsed.
    rl = svc._RateLimiter(limit_per_minute=5)
    now = [1000.0]
    monkeypatch.setattr(svc.time, "monotonic", lambda: now[0])
    for i in range(10005):
        rl.allow(f"key-{i}")
    assert len(rl._windows) == 10005   # still-active windows aren't dropped early
    now[0] += 61.0
    rl.allow("fresh-key")
    assert len(rl._windows) == 1   # the 61s-old windows got swept on this call


def test_rate_limiter_sweep_is_throttled_to_once_per_60s(monkeypatch):
    # A sustained flood that keeps _windows above the cap must not force
    # the O(N) sweep on every single request -- only at most once/60s.
    rl = svc._RateLimiter(limit_per_minute=5)
    now = [1000.0]
    monkeypatch.setattr(svc.time, "monotonic", lambda: now[0])
    for i in range(10005):
        rl.allow(f"key-{i}")
    first_sweep = rl._last_sweep
    assert first_sweep == 1000.0   # swept once already, mid-flood

    now[0] += 30.0
    rl.allow("extra-key")
    assert rl._last_sweep == first_sweep   # too soon -- no re-sweep
    assert len(rl._windows) == 10006       # so nothing got pruned yet

    now[0] += 31.0   # 61s past first_sweep
    rl.allow("another-key")
    assert rl._last_sweep != first_sweep   # 60s elapsed -- re-swept


def test_health_and_ready_are_exempt_from_rate_limiting(tmp_path):
    gp = str(tmp_path / "g.json")
    Graph(gp).save()
    httpd = _start(gp, {WTOK: "worker"}, rate_limit_per_minute=1)
    try:
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        for _ in range(5):
            assert call(base, "GET", "/health")[0] == 200
            assert call(base, "GET", "/ready")[0] == 200
    finally:
        httpd.shutdown()
        httpd.server_close()


# -- /usage ---------------------------------------------------------------

def test_usage_requires_operator(service):
    assert call(service.base, "GET", "/questions", token=WTOK)[0] == 200
    assert call(service.base, "GET", "/usage", token=WTOK)[0] == 403


def test_usage_counts_requests_and_status(service):
    call(service.base, "GET", "/questions", token=WTOK)
    call(service.base, "GET", "/questions", token=WTOK)
    call(service.base, "GET", "/nope", token=WTOK)
    code, _, body = call(service.base, "GET", "/usage", token=OTOK)
    assert code == 200
    assert body["requests"] >= 3
    assert body["by_route"]["/questions"] == 2
    assert body["by_status"]["200"] >= 2
    assert body["by_status"]["404"] == 1


def test_usage_tracks_delta_accept_reject_counts(service):
    delta = {"new_nodes": [{"ref": "q", "type": "question", "text": "Q?"}],
             "new_edges": []}
    call(service.base, "POST", "/deltas", token=WTOK,
        body={"worker": "w", "delta": delta})
    _, _, body = call(service.base, "GET", "/usage", token=OTOK)
    assert body["deltas_accepted"] == 1
    assert body["deltas_rejected"] == 0


def test_usage_collapses_unmatched_routes_instead_of_keying_on_raw_path(service):
    # An attacker probing many distinct bogus paths must not grow by_route
    # by one key per attempt -- they all collapse to a single "invalid" key.
    for i in range(5):
        call(service.base, "GET", f"/nonsense-{i}?x={i}", token=WTOK)
    code, _, body = call(service.base, "GET", "/usage", token=OTOK)
    assert code == 200
    assert body["by_route"]["invalid"] == 5
    assert all(not k.startswith("/nonsense") for k in body["by_route"])


def test_usage_attributes_by_credential_tenant_not_url_tenant(tmp_path):
    # Cross-tenant probing with a real credential must be metered against
    # that credential's own tenant, not whatever tenant string the URL
    # asked for -- otherwise one valid token lets an attacker grow a usage
    # bucket per guessed tenant id. No real Postgres is touched: every
    # request below is rejected (403) before any store access happens.
    tokens = {"worker-acme-tok": {"role": "worker", "tenant": "acme"},
             "operator-acme-tok": {"role": "operator", "tenant": "acme"}}
    httpd = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(pg_dsn="postgresql://unused/perdura", tokens=tokens))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        for i in range(5):
            code, _, _ = call(base, "GET", f"/graphs/guessed-tenant-{i}/questions",
                              token="worker-acme-tok")
            assert code == 403
        code, _, body = call(base, "GET", "/graphs/acme/usage",
                            token="operator-acme-tok")
        assert code == 200
        assert body["by_status"]["403"] == 5
        assert body["by_route"]["/questions"] == 5
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_usage_resets_per_server_instance(tmp_path):
    # a fresh make_handler() must start at zero -- no cross-test/process
    # leakage through shared module state
    gp = str(tmp_path / "g.json")
    Graph(gp).save()
    httpd = _start(gp, {WTOK: "worker", OTOK: "operator"})
    try:
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        _, _, body = call(base, "GET", "/usage", token=OTOK)
        assert body["requests"] == 0
    finally:
        httpd.shutdown()
        httpd.server_close()
