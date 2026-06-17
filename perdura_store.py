"""
perdura_store.py — pluggable graph persistence (enterprise track, E0 + E2).

The graph is the system of record, so where it lives is a deployment
decision, not an architecture decision. The store is selected by path
prefix/extension and everything above it (conductor, Station, MCP, viz,
track) is unchanged:

    perdura_graph.json          JSON file (default — byte-identical to
                                Phase 1, human-diffable, gitignored)
    perdura_graph.db            SQLite, WAL mode (transactional saves,
    perdura_graph.sqlite[3]     concurrent readers while a conductor
                                writes — the single-box multi-process tier)
    postgres://... (or          Postgres, one database shared by every
    postgresql://...)           tenant; rows scoped by tenant_id and
                                enforced by row-level security — the
                                multi-tenant tier (E2). Tenant is the
                                `tenant` query param on the DSN, e.g.
                                postgresql://host/perdura?tenant=acme
                                (omitted = tenant "default", i.e. the
                                same single-tenant shape as E0/E1).

Every store keeps the same correctness story: writers serialize via
`.lock()` (reload → merge → save), and a save is atomic (write-rename for
JSON, one transaction for SQLite/Postgres), so readers never observe a
torn graph.

Nodes and edges are never deleted (supersede-never-delete), which is why
the SQL stores can do pure upserts; the merge log is append-only, so only
entries past the stored count are inserted.
"""

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

try:
    import fcntl
except ImportError:          # Windows: no advisory locks; single writer only
    fcntl = None

SQLITE_SUFFIXES = (".db", ".sqlite", ".sqlite3")
POSTGRES_PREFIXES = ("postgres://", "postgresql://")

# Two-key advisory lock namespace so a Postgres-tier lock can never collide
# with an unrelated advisory lock some other tool on the same database
# happens to take.
_ADVISORY_LOCK_NS = 0x70657264  # "perd" — arbitrary, just a namespace tag


def store_for(path: str):
    if path.startswith(POSTGRES_PREFIXES):
        return PostgresStore(path)
    if path.endswith(SQLITE_SUFFIXES):
        return SQLiteStore(path)
    return JSONFileStore(path)


@contextmanager
def _file_lock(lock_path: str):
    if fcntl is None:
        yield
        return
    with open(lock_path, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


class JSONFileStore:
    """Phase 1 behavior, verbatim: one pretty-printed JSON document,
    written to a temp file and renamed into place."""

    def __init__(self, path: str):
        self.path = path

    def exists(self) -> bool:
        return os.path.exists(self.path)

    def load(self) -> dict:
        with open(self.path, encoding="utf-8") as f:
            return json.load(f)

    def save(self, nodes: list, edges: list, log: list):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"nodes": nodes, "edges": edges, "log": log},
                      f, indent=2)
        os.replace(tmp, self.path)

    def lock(self):
        return _file_lock(self.path + ".lock")


class SQLiteStore:
    """Rows are the serialized dataclass dicts, keyed by id — the schema
    stays defined in one place (perdura.py) and the store stays dumb.
    WAL mode lets the Station and other readers poll the file while a
    conductor commits."""

    def __init__(self, path: str):
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        # A fresh connection per operation: conductors, the MCP station,
        # and the Station dashboard are separate processes, and stale
        # handles across forks are how WAL files get corrupted.
        con = sqlite3.connect(self.path, timeout=30)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        con.executescript("""
            CREATE TABLE IF NOT EXISTS nodes (id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS edges (id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS log   (seq INTEGER PRIMARY KEY AUTOINCREMENT,
                                              entry TEXT NOT NULL);
        """)
        return con

    def exists(self) -> bool:
        return os.path.exists(self.path)

    def load(self) -> dict:
        con = self._connect()
        try:
            return {
                "nodes": [json.loads(r[0]) for r in
                          con.execute("SELECT data FROM nodes")],
                "edges": [json.loads(r[0]) for r in
                          con.execute("SELECT data FROM edges")],
                "log": [json.loads(r[0]) for r in
                        con.execute("SELECT entry FROM log ORDER BY seq")],
            }
        finally:
            con.close()

    def save(self, nodes: list, edges: list, log: list):
        con = self._connect()
        try:
            with con:  # one transaction: readers see old graph or new, never half
                con.executemany(
                    "INSERT INTO nodes (id, data) VALUES (?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                    [(n["id"], json.dumps(n)) for n in nodes])
                con.executemany(
                    "INSERT INTO edges (id, data) VALUES (?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                    [(e["id"], json.dumps(e)) for e in edges])
                (stored,) = con.execute("SELECT COUNT(*) FROM log").fetchone()
                con.executemany(
                    "INSERT INTO log (entry) VALUES (?)",
                    [(json.dumps(entry),) for entry in log[stored:]])
        finally:
            con.close()

    def lock(self):
        return _file_lock(self.path + ".lock")


class PostgresStore:
    """Multi-tenant tier (E2). One database, one set of tables, every row
    tagged `tenant_id`. Isolation is enforced twice: an explicit WHERE on
    every query (cheap, obvious) and Postgres row-level security scoped to
    `current_setting('perdura.tenant_id')` (the hard backstop — holds even
    if application code forgets the WHERE). RLS is FORCEd so it applies to
    the owning role too, not just other logins.

    The write lock is a Postgres advisory lock keyed by tenant, so it
    coordinates writers across hosts — the thing a flock on a local path
    cannot do, and the reason this tier exists.
    """

    # Schema DDL (ALTER TABLE ... ENABLE/FORCE ROW LEVEL SECURITY) takes an
    # ACCESS EXCLUSIVE lock, so it must run at most once per process per
    # target database, not on every connection — a class-level, lock-guarded
    # set of already-initialized DSNs (double-checked) keeps the common path
    # cheap and free of that lock entirely.
    _schema_lock = threading.Lock()
    _schema_ready: set = set()

    def __init__(self, dsn: str):
        parts = urlsplit(dsn)
        qs = parse_qs(parts.query)
        tenant = (qs.pop("tenant", None) or ["default"])[0]
        clean_query = urlencode(qs, doseq=True)
        self.dsn = urlunsplit((parts.scheme, parts.netloc, parts.path,
                               clean_query, parts.fragment))
        self.tenant_id = tenant

    # -- connection / schema -------------------------------------------------
    def _connect(self):
        import psycopg
        con = psycopg.connect(self.dsn)
        if self.dsn not in PostgresStore._schema_ready:
            with PostgresStore._schema_lock:
                if self.dsn not in PostgresStore._schema_ready:
                    self._ensure_schema(con)
                    PostgresStore._schema_ready.add(self.dsn)
        con.execute("SELECT set_config('perdura.tenant_id', %s, false)",
                    (self.tenant_id,))
        con.commit()
        return con

    def _ensure_schema(self, con):
        con.execute("""
            CREATE TABLE IF NOT EXISTS nodes (
                tenant_id TEXT NOT NULL, id TEXT NOT NULL, data JSONB NOT NULL,
                PRIMARY KEY (tenant_id, id));
            CREATE TABLE IF NOT EXISTS edges (
                tenant_id TEXT NOT NULL, id TEXT NOT NULL, data JSONB NOT NULL,
                PRIMARY KEY (tenant_id, id));
            CREATE TABLE IF NOT EXISTS log (
                tenant_id TEXT NOT NULL, seq BIGINT NOT NULL, entry JSONB NOT NULL,
                PRIMARY KEY (tenant_id, seq));
            CREATE TABLE IF NOT EXISTS tenants (
                tenant_id TEXT PRIMARY KEY,
                domain_budgets JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now());
        """)
        for table in ("nodes", "edges", "log"):
            con.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            con.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
            con.execute(f"""
                DO $$ BEGIN
                    CREATE POLICY tenant_isolation ON {table}
                        USING (tenant_id = current_setting('perdura.tenant_id', true));
                EXCEPTION WHEN duplicate_object THEN NULL; END $$;
            """)
        con.commit()

    def exists(self) -> bool:
        # A tenant "exists" once it has ever saved — an empty/never-seen
        # tenant behaves like a fresh graph, same as a missing JSON file.
        con = self._connect()
        try:
            row = con.execute(
                "SELECT 1 FROM tenants WHERE tenant_id = %s",
                (self.tenant_id,)).fetchone()
            return row is not None
        finally:
            con.close()

    def load(self) -> dict:
        con = self._connect()
        try:
            nodes = [r[0] for r in con.execute(
                "SELECT data FROM nodes WHERE tenant_id = %s", (self.tenant_id,))]
            edges = [r[0] for r in con.execute(
                "SELECT data FROM edges WHERE tenant_id = %s", (self.tenant_id,))]
            log = [r[0] for r in con.execute(
                "SELECT entry FROM log WHERE tenant_id = %s ORDER BY seq",
                (self.tenant_id,))]
            return {"nodes": nodes, "edges": edges, "log": log}
        finally:
            con.close()

    def save(self, nodes: list, edges: list, log: list):
        from psycopg.types.json import Jsonb
        con = self._connect()
        try:
            with con.transaction(), con.cursor() as cur:
                cur.execute(
                    "INSERT INTO tenants (tenant_id) VALUES (%s) "
                    "ON CONFLICT (tenant_id) DO NOTHING", (self.tenant_id,))
                cur.executemany(
                    "INSERT INTO nodes (tenant_id, id, data) VALUES (%s, %s, %s) "
                    "ON CONFLICT (tenant_id, id) DO UPDATE SET data = excluded.data",
                    [(self.tenant_id, n["id"], Jsonb(n)) for n in nodes])
                cur.executemany(
                    "INSERT INTO edges (tenant_id, id, data) VALUES (%s, %s, %s) "
                    "ON CONFLICT (tenant_id, id) DO UPDATE SET data = excluded.data",
                    [(self.tenant_id, e["id"], Jsonb(e)) for e in edges])
                (stored,) = cur.execute(
                    "SELECT COUNT(*) FROM log WHERE tenant_id = %s",
                    (self.tenant_id,)).fetchone()
                cur.executemany(
                    "INSERT INTO log (tenant_id, seq, entry) VALUES (%s, %s, %s)",
                    [(self.tenant_id, stored + i, Jsonb(entry))
                     for i, entry in enumerate(log[stored:])])
        finally:
            con.close()

    @contextmanager
    def lock(self):
        import psycopg
        import zlib
        con = psycopg.connect(self.dsn, autocommit=True)
        key1 = _ADVISORY_LOCK_NS
        # crc32, not the builtin hash(): str hashing is randomized per
        # process (PYTHONHASHSEED), which would make the same tenant_id
        # produce a different lock key on every host — defeating the
        # cross-host serialization this lock exists for.
        key2 = zlib.crc32(self.tenant_id.encode("utf-8")) & 0x7FFFFFFF
        try:
            con.execute("SELECT pg_advisory_lock(%s, %s)", (key1, key2))
            yield
        finally:
            con.execute("SELECT pg_advisory_unlock(%s, %s)", (key1, key2))
            con.close()

    # -- tenant config (E2 control plane) ------------------------------------
    def get_tenant_config(self) -> dict:
        con = self._connect()
        try:
            row = con.execute(
                "SELECT domain_budgets FROM tenants WHERE tenant_id = %s",
                (self.tenant_id,)).fetchone()
            return {"tenant_id": self.tenant_id,
                    "domain_budgets": (row[0] if row else {}) or {}}
        finally:
            con.close()

    def set_tenant_config(self, domain_budgets: dict):
        from psycopg.types.json import Jsonb
        con = self._connect()
        try:
            with con.transaction():
                con.execute(
                    "INSERT INTO tenants (tenant_id, domain_budgets) "
                    "VALUES (%s, %s) ON CONFLICT (tenant_id) "
                    "DO UPDATE SET domain_budgets = excluded.domain_budgets",
                    (self.tenant_id, Jsonb(domain_budgets)))
        finally:
            con.close()
