"""
perdura_store.py — pluggable graph persistence (enterprise track, step E0).

The graph is the system of record, so where it lives is a deployment
decision, not an architecture decision. The store is selected by file
extension and everything above it (conductor, Station, MCP, viz, track)
is unchanged:

    perdura_graph.json          JSON file (default — byte-identical to
                                Phase 1, human-diffable, gitignored)
    perdura_graph.db            SQLite, WAL mode (transactional saves,
    perdura_graph.sqlite[3]     concurrent readers while a conductor
                                writes — the single-box multi-process tier)

Scaling path: JSON file → SQLite → Postgres (same interface, graph-per-
tenant — docs/enterprise.md). Both stores keep the same correctness
story: writers serialize via graph_write_lock (reload → merge → save),
and a save is atomic (write-rename for JSON, one transaction for SQLite),
so readers never observe a torn graph.

Nodes and edges are never deleted (supersede-never-delete), which is why
SQLite saves can be pure upserts; the merge log is append-only, so only
entries past the stored count are inserted.
"""

import json
import os
import sqlite3

SQLITE_SUFFIXES = (".db", ".sqlite", ".sqlite3")


def store_for(path: str):
    if path.endswith(SQLITE_SUFFIXES):
        return SQLiteStore(path)
    return JSONFileStore(path)


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
