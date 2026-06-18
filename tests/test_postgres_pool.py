"""PostgresStore pools connections (psycopg_pool.ConnectionPool) instead of
opening a fresh one per load/save/config call -- load-bearing because
perdura_service.py builds a new PostgresStore per HTTP request, so the pool
has to be shared above any one store instance or it would just move the
same per-request connection cost up a layer. This proves the sharing logic
with a fake pool class (no real psycopg_pool networking, no live Postgres
needed) -- the live round trip through the real pool is exercised by
tests/test_postgres_store.py against the CI Postgres service."""

import pytest

pytest.importorskip("psycopg_pool")
import psycopg_pool

from perdura_store import PostgresStore


class _FakePool:
    instances = []

    def __init__(self, dsn, **kwargs):
        self.dsn = dsn
        self.kwargs = kwargs
        _FakePool.instances.append(self)

    def close(self):
        pass


@pytest.fixture(autouse=True)
def _fake_connection_pool(monkeypatch):
    monkeypatch.setattr(psycopg_pool, "ConnectionPool", _FakePool)
    monkeypatch.setattr(PostgresStore, "_pools", {})
    _FakePool.instances.clear()
    yield


def test_same_dsn_shares_one_pool_across_tenants():
    sa = PostgresStore("postgresql://host/db?tenant=acme")
    sb = PostgresStore("postgresql://host/db?tenant=globex")
    assert sa.dsn == sb.dsn   # tenant stripped before reaching the pool key
    assert PostgresStore._pool_for(sa.dsn) is PostgresStore._pool_for(sb.dsn)
    assert len(_FakePool.instances) == 1


def test_different_dsn_gets_its_own_pool():
    sa = PostgresStore("postgresql://host/db-a")
    sb = PostgresStore("postgresql://host/db-b")
    assert PostgresStore._pool_for(sa.dsn) is not PostgresStore._pool_for(sb.dsn)
    assert len(_FakePool.instances) == 2
