"""Enterprise E2 service — multi-tenant HTTP routes, SSO, and the
admin-only per-tenant config endpoint, all over a real loopback server
backed by real Postgres. Needs a reachable Postgres; skips cleanly
otherwise (same gate as tests/test_postgres_store.py)."""

import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import urlsplit, urlunsplit

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

psycopg = pytest.importorskip("psycopg")

from perdura_service import make_handler
from perdura_sso import SSOConfig

ADMIN_DSN = __import__("os").environ.get(
    "PERDURA_TEST_PG_DSN", "postgresql://postgres:postgres@127.0.0.1:5432/postgres")
APP_ROLE = "perdura_test_app_svc"
APP_PASSWORD = "perdura-test-app-svc-pw"
ISSUER, AUDIENCE, KID = "https://idp.example.com/", "perdura", "svc-test-key"


def _reachable():
    try:
        with psycopg.connect(ADMIN_DSN, connect_timeout=2):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _reachable(),
    reason="no Postgres reachable at PERDURA_TEST_PG_DSN for the E2 service tests")


@pytest.fixture(scope="module")
def app_dsn():
    """Same non-superuser, NOBYPASSRLS role as test_postgres_store.py, under
    a distinct name so the two modules' fixtures never collide regardless of
    run order."""
    with psycopg.connect(ADMIN_DSN, autocommit=True) as con:
        exists = con.execute("SELECT 1 FROM pg_roles WHERE rolname = %s",
                              (APP_ROLE,)).fetchone()
        if exists:
            con.execute(f"DROP OWNED BY {APP_ROLE} CASCADE")
            con.execute(f"DROP ROLE {APP_ROLE}")
        con.execute(f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD "
                    f"'{APP_PASSWORD}' NOSUPERUSER NOBYPASSRLS")
        con.execute(f"GRANT ALL ON SCHEMA public TO {APP_ROLE}")
        con.execute("DROP TABLE IF EXISTS nodes, edges, log, tenants CASCADE")
    parts = urlsplit(ADMIN_DSN)
    netloc = f"{APP_ROLE}:{APP_PASSWORD}@{parts.hostname}:{parts.port or 5432}"
    base = urlunsplit((parts.scheme, netloc, parts.path, "", ""))
    yield base
    with psycopg.connect(ADMIN_DSN, autocommit=True) as con:
        con.execute(f"DROP OWNED BY {APP_ROLE} CASCADE")
        con.execute(f"DROP ROLE {APP_ROLE}")


@pytest.fixture
def tenants():
    suffix = str(time.time_ns())
    return f"acme-{suffix}", f"globex-{suffix}"


def _tok(role, tenant):
    return f"{role}-{tenant}-tok"


def _tokens_for(tenant):
    return {_tok(role, tenant): {"role": role, "tenant": tenant}
             for role in ("worker", "operator", "admin")}


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


@pytest.fixture
def service(app_dsn, tenants):
    tenant_a, tenant_b = tenants
    tokens = {**_tokens_for(tenant_a), **_tokens_for(tenant_b)}
    httpd = ThreadingHTTPServer(("127.0.0.1", 0),
                                make_handler(pg_dsn=app_dsn, tokens=tokens))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield SimpleNamespace(base=f"http://127.0.0.1:{httpd.server_address[1]}",
                              tenant_a=tenant_a, tenant_b=tenant_b)
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_health_needs_no_auth(service):
    assert call(service.base, "GET", "/health")[0] == 200


def test_unprefixed_route_is_404_in_multitenant_mode(service):
    code, _ = call(service.base, "GET", "/questions",
                   token=_tok("worker", service.tenant_a))
    assert code == 404


def test_worker_can_read_own_tenant(service):
    code, body = call(service.base, "GET",
                      f"/graphs/{service.tenant_a}/questions",
                      token=_tok("worker", service.tenant_a))
    assert code == 200 and body["open_questions"] == []


def test_token_tenant_mismatch_is_403(service):
    code, _ = call(service.base, "GET",
                   f"/graphs/{service.tenant_b}/questions",
                   token=_tok("worker", service.tenant_a))
    assert code == 403


def test_unknown_tenant_in_url_is_403(service):
    code, _ = call(service.base, "GET", "/graphs/no-such-tenant/questions",
                   token=_tok("worker", service.tenant_a))
    assert code == 403


def test_admin_required_for_config(service):
    code, _ = call(service.base, "GET", f"/graphs/{service.tenant_a}/config",
                   token=_tok("operator", service.tenant_a))
    assert code == 403


def test_admin_config_get_and_put_round_trip(service):
    base, ta = service.base, service.tenant_a
    admin = _tok("admin", ta)
    code, body = call(base, "GET", f"/graphs/{ta}/config", token=admin)
    assert code == 200 and body["domain_budgets"] == {}

    code, body = call(base, "PUT", f"/graphs/{ta}/config", token=admin,
                      body={"domain_budgets": {"legal": 5.0}})
    assert code == 200 and body["domain_budgets"] == {"legal": 5.0}

    code, body = call(base, "GET", f"/graphs/{ta}/config", token=admin)
    assert code == 200 and body["domain_budgets"] == {"legal": 5.0}


def test_config_is_isolated_per_tenant(service):
    base, ta, tb = service.base, service.tenant_a, service.tenant_b
    call(base, "PUT", f"/graphs/{ta}/config", token=_tok("admin", ta),
         body={"domain_budgets": {"legal": 5.0}})
    code, body = call(base, "GET", f"/graphs/{tb}/config", token=_tok("admin", tb))
    assert code == 200 and body["domain_budgets"] == {}


def test_delta_lands_only_in_its_own_tenant_graph(service):
    base, ta, tb = service.base, service.tenant_a, service.tenant_b
    delta = {"new_nodes": [{"ref": "q", "type": "question", "text": "Q?"}],
             "new_edges": []}
    code, body = call(base, "POST", f"/graphs/{ta}/deltas",
                      token=_tok("worker", ta),
                      body={"worker": "w", "delta": delta})
    assert code == 200 and body["accepted"] == 1

    code, body = call(base, "GET", f"/graphs/{tb}/questions",
                      token=_tok("worker", tb))
    assert body["open_questions"] == []

    code, body = call(base, "GET", f"/graphs/{ta}/questions",
                      token=_tok("worker", ta))
    assert len(body["open_questions"]) == 1


# -- SSO over real HTTP, with static tokens as break-glass alongside it -----

@pytest.fixture(scope="module")
def sso_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


@pytest.fixture
def sso_jwks_file(tmp_path, sso_keypair):
    _, public_key = sso_keypair
    jwk = RSAAlgorithm(RSAAlgorithm.SHA256).to_jwk(public_key, as_dict=True)
    jwk["kid"] = KID
    jwk["use"] = "sig"
    path = tmp_path / "jwks.json"
    path.write_text(json.dumps({"keys": [jwk]}))
    return str(path)


def _jwt_for(keypair, **claims):
    private_key, _ = keypair
    payload = {"iss": ISSUER, "aud": AUDIENCE, "iat": time.time(),
               "exp": time.time() + 300, "role": "worker", "tenant": "acme",
               **claims}
    return jwt.encode(payload, private_key, algorithm="RS256",
                      headers={"kid": KID})


@pytest.fixture
def sso_service(app_dsn, tenants, sso_jwks_file):
    tenant_a, tenant_b = tenants
    sso = SSOConfig(issuer=ISSUER, audience=AUDIENCE, jwks_file=sso_jwks_file)
    break_glass = {_tok("operator", tenant_a): {"role": "operator", "tenant": tenant_a}}
    httpd = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(pg_dsn=app_dsn, tokens=break_glass, sso=sso))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield SimpleNamespace(base=f"http://127.0.0.1:{httpd.server_address[1]}",
                              tenant_a=tenant_a, tenant_b=tenant_b)
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_sso_token_authenticates_over_http(sso_service, sso_keypair):
    tok = _jwt_for(sso_keypair, role="worker", tenant=sso_service.tenant_a)
    code, body = call(sso_service.base, "GET",
                      f"/graphs/{sso_service.tenant_a}/questions", token=tok)
    assert code == 200 and body["open_questions"] == []


def test_sso_tenant_claim_must_match_url_tenant(sso_service, sso_keypair):
    tok = _jwt_for(sso_keypair, role="worker", tenant=sso_service.tenant_a)
    code, _ = call(sso_service.base, "GET",
                   f"/graphs/{sso_service.tenant_b}/questions", token=tok)
    assert code == 403


def test_static_token_still_works_as_break_glass_alongside_sso(sso_service):
    code, _ = call(sso_service.base, "GET",
                   f"/graphs/{sso_service.tenant_a}/track",
                   token=_tok("operator", sso_service.tenant_a))
    assert code == 200


def test_invalid_sso_token_falls_through_to_401_when_no_static_match(sso_service):
    code, _ = call(sso_service.base, "GET",
                   f"/graphs/{sso_service.tenant_a}/questions",
                   token="not-a-jwt-and-not-a-static-token")
    assert code == 401
