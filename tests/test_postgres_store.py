"""Postgres store (E2) — the multi-tenant tier. Isolation must hold even
when application code forgets a WHERE clause, so these tests exercise raw
SQL through the actual application role (non-superuser, NOBYPASSRLS) rather
than trusting PostgresStore's own filtering. Needs a reachable Postgres;
skips cleanly otherwise so `pytest` stays offline-by-default everywhere
that doesn't run the CI Postgres service container."""

import os
import threading
import time
from urllib.parse import urlsplit, urlunsplit

import pytest

psycopg = pytest.importorskip("psycopg")

from perdura import Graph
from perdura_store import PostgresStore, store_for

ADMIN_DSN = os.environ.get(
    "PERDURA_TEST_PG_DSN", "postgresql://postgres:postgres@127.0.0.1:5432/postgres")
APP_ROLE = "perdura_test_app"
APP_PASSWORD = "perdura-test-app-pw"


def _reachable():
    try:
        with psycopg.connect(ADMIN_DSN, connect_timeout=2):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _reachable(),
    reason="no Postgres reachable at PERDURA_TEST_PG_DSN for the E2 store tests")


@pytest.fixture(scope="module")
def app_dsn():
    """A non-superuser, NOBYPASSRLS role: RLS is invisible to superusers
    regardless of FORCE, so isolation must be proven under a realistic
    application credential, not the admin connection."""
    with psycopg.connect(ADMIN_DSN, autocommit=True) as con:
        exists = con.execute("SELECT 1 FROM pg_roles WHERE rolname = %s",
                              (APP_ROLE,)).fetchone()
        if exists:
            con.execute(f"DROP OWNED BY {APP_ROLE} CASCADE")
            con.execute(f"DROP ROLE {APP_ROLE}")
        con.execute(f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD "
                    f"'{APP_PASSWORD}' NOSUPERUSER NOBYPASSRLS")
        con.execute(f"GRANT ALL ON SCHEMA public TO {APP_ROLE}")
        # Tables are shared across tenants/runs; drop any leftovers (e.g.
        # owned by a prior superuser session) so _ensure_schema recreates
        # them owned by the app role -- ownership is what makes FORCE ROW
        # LEVEL SECURITY actually bind for non-superuser writers.
        con.execute("DROP TABLE IF EXISTS nodes, edges, log, tenants CASCADE")
    parts = urlsplit(ADMIN_DSN)
    netloc = f"{APP_ROLE}:{APP_PASSWORD}@{parts.hostname}:{parts.port or 5432}"
    base = urlunsplit((parts.scheme, netloc, parts.path, "", ""))
    yield base
    with psycopg.connect(ADMIN_DSN, autocommit=True) as con:
        con.execute(f"DROP OWNED BY {APP_ROLE} CASCADE")
        con.execute(f"DROP ROLE {APP_ROLE}")


@pytest.fixture
def two_tenants(app_dsn):
    suffix = str(time.time_ns())
    return (f"{app_dsn}?tenant=acme-{suffix}", f"{app_dsn}?tenant=globex-{suffix}")


def test_store_selected_for_postgres_dsn(app_dsn):
    for prefix in ("postgres://", "postgresql://"):
        s = store_for(prefix + "host/db")
        assert isinstance(s, PostgresStore)
        assert s.tenant_id == "default"
    s = store_for(app_dsn + "?tenant=acme")
    assert s.tenant_id == "acme"
    assert "tenant=" not in s.dsn   # stripped before reaching libpq


def test_round_trip_and_isolation(two_tenants):
    dsn_a, dsn_b = two_tenants
    sa, sb = store_for(dsn_a), store_for(dsn_b)
    assert not sa.exists() and not sb.exists()
    sa.save([{"id": "n1", "type": "claim"}], [], [{"ts": 1}])
    sb.save([{"id": "n2", "type": "claim"}], [], [])
    assert sa.exists() and sb.exists()
    assert [n["id"] for n in sa.load()["nodes"]] == ["n1"]
    assert [n["id"] for n in sb.load()["nodes"]] == ["n2"]
    assert sa.load()["log"] == [{"ts": 1}]


def test_rls_enforces_isolation_with_no_where_clause(app_dsn, two_tenants):
    """The hard backstop: even a raw query with NO tenant filter at all,
    run through the real application role, must only ever see the row(s)
    for whichever tenant the session is currently scoped to."""
    dsn_a, dsn_b = two_tenants
    tenant_a = dsn_a.split("tenant=")[1]
    tenant_b = dsn_b.split("tenant=")[1]
    store_for(dsn_a).save([{"id": "n1"}], [], [])
    store_for(dsn_b).save([{"id": "n2"}], [], [])

    with psycopg.connect(app_dsn) as con:
        con.execute("SELECT set_config('perdura.tenant_id', %s, false)", (tenant_a,))
        rows = con.execute("SELECT id FROM nodes").fetchall()
        assert [r[0] for r in rows] == ["n1"]

        con.execute("SELECT set_config('perdura.tenant_id', %s, false)", (tenant_b,))
        rows = con.execute("SELECT id FROM nodes").fetchall()
        assert [r[0] for r in rows] == ["n2"]

        con.execute("SELECT set_config('perdura.tenant_id', '', false)")
        rows = con.execute("SELECT id FROM nodes").fetchall()
        assert rows == []   # unset tenant -> fail closed, not fail open


def test_lock_serializes_within_a_tenant_but_not_across(two_tenants):
    dsn_a, dsn_b = two_tenants
    sa, sa2, sb = store_for(dsn_a), store_for(dsn_a), store_for(dsn_b)
    started_b_at = {}

    def hold_a_lock():
        with sa.lock():
            time.sleep(0.4)

    t = threading.Thread(target=hold_a_lock)
    t.start()
    time.sleep(0.1)   # let the thread actually acquire tenant A's lock first

    start = time.monotonic()
    with sb.lock():    # different tenant: must not block on A's lock
        started_b_at["elapsed"] = time.monotonic() - start
    assert started_b_at["elapsed"] < 0.3

    start = time.monotonic()
    with sa2.lock():    # same tenant: must wait for the held lock to release
        elapsed = time.monotonic() - start
    assert elapsed > 0.2
    t.join()


def test_tenant_config_round_trip_and_isolation(two_tenants):
    dsn_a, dsn_b = two_tenants
    sa, sb = store_for(dsn_a), store_for(dsn_b)
    assert sa.get_tenant_config()["domain_budgets"] == {}
    sa.set_tenant_config({"architecture": 5.0})
    assert sa.get_tenant_config()["domain_budgets"] == {"architecture": 5.0}
    assert sb.get_tenant_config()["domain_budgets"] == {}


def test_ping_does_not_leak_pool_connections(two_tenants):
    # ping() borrows a connection through the same pooled _connect() every
    # other method uses (pool.connection(), not a raw psycopg.connect());
    # if it ever failed to return the connection, the pool (max_size=10)
    # would exhaust well before this loop finishes.
    dsn_a, _ = two_tenants
    s = store_for(dsn_a)
    for _ in range(25):
        assert s.ping() is True


def test_graph_class_round_trips_through_postgres(two_tenants):
    dsn_a, _ = two_tenants
    g = Graph(dsn_a)
    q = g.add_node("question", "Q?", created_by="user", confidence=1.0)
    c = g.add_node("claim", "C", created_by="w", confidence=0.6, domain_tags=["d"])
    g.add_edge("answers", c, q, "w")
    g.save()

    back = Graph(dsn_a)
    assert set(back.nodes) == set(g.nodes)
    assert set(back.edges) == set(g.edges)
    assert back.nodes[c].domain_tags == ["d"]
