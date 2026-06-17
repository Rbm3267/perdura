"""
perdura_service.py — enterprise track E1 + E2: the three planes over HTTP,
now multi-tenant.

E1 (single tenant): one graph, bearer tokens minted at startup, the
worker/operator split enforced at the boundary.

    Delta    (write)  POST /deltas          worker | operator
    Briefing (read)   GET  /briefing        worker | operator
                      GET  /questions       worker | operator
                      GET  /contention      worker | operator
    Operator (control) GET /track           operator only
                       GET /graph           operator only

E2 (multi-tenant control plane) adds a third role, **admin**, and a tenant
prefix: start with `--pg-dsn` instead of `--graph` and every route above
gains a `/graphs/{tenant_id}` prefix, backed by one Postgres database with
row-level-security tenant isolation (perdura_store.PostgresStore). A new
admin-only route makes per-domain router budgets real, mutable config
instead of a CLI flag:

    GET/PUT /graphs/{tenant_id}/config      admin only   (domain_budgets)

Auth has two layers, and either can carry role + tenant:
  - **SSO** (perdura_sso.SSOConfig.from_env()): bearer tokens are JWTs
    issued by the org's IdP, verified against its JWKS. This is how E2
    deployments should authenticate — role and tenant are claims the IdP
    vouched for, not config this service trusts blindly.
  - **Static tokens** (PERDURA_WORKER_TOKEN/PERDURA_OPERATOR_TOKEN for E1,
    or PERDURA_STATIC_TOKENS as a JSON map of token -> {"role","tenant"}
    for E2 without standing up an IdP — local dev, tests, break-glass).
A request is authorized if either layer recognizes the token; SSO is
tried first. Attribution-hiding is a conductor invariant, so it is a
security boundary here: worker tokens can board, contribute, and read
contention, but never see authorship (/track and the attributed /graph
are operator-only). In multi-tenant mode a token's tenant claim must
match the `{tenant_id}` in the URL, or the request gets 403 — RLS in
Postgres is the hard backstop, this is the HTTP-layer check in front of it.

Stdlib only for E1 (same as the Station); E2 needs the `enterprise` extra
(`pip install -e ".[enterprise]"`) for psycopg + PyJWT. Writes use the same
advisory lock + reload-merge-save discipline as every other writer.

    python perdura_service.py --graph /abs/perdura_graph.json --port 8900
    python perdura_service.py --pg-dsn postgresql://host/perdura --port 8900
"""

import argparse
import json
import os
import secrets
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlparse, urlsplit, urlunsplit

from perdura import Graph, build_briefing, merge_delta, parse_delta, graph_write_lock
from perdura_store import store_for

# role rank: each role inherits everything ranked below it
_RANK = {"worker": 1, "operator": 2, "admin": 3}

# caps a worker-controlled Content-Length before it's used to size a read,
# so a forged header can't be used to force an oversized in-memory buffer
_MAX_BODY_SIZE = 10 * 1024 * 1024  # 10MB


def _dsn_for_tenant(base_dsn: str, tenant_id: str) -> str:
    parts = urlsplit(base_dsn)
    qs = parse_qs(parts.query)
    qs["tenant"] = [tenant_id]
    return urlunsplit((parts.scheme, parts.netloc, parts.path,
                       urlencode(qs, doseq=True), parts.fragment))


def _parse_tenant_route(path: str):
    """'/graphs/acme/questions' -> ('acme', '/questions'); ('', '') for
    anything that isn't a /graphs/{tenant}/... path."""
    parts = path.split("/", 3)
    if len(parts) < 3 or parts[0] != "" or parts[1] != "graphs" or not parts[2]:
        return None, None
    return parts[2], ("/" + parts[3] if len(parts) > 3 else "")


def _questions(g):
    rows = []
    for q in g.open_questions():
        hood = g.neighborhood(q.id)
        rows.append({"id": q.id, "text": q.text, "domain_tags": q.domain_tags,
                     "contention": g.contention(hood),
                     "claims": sum(1 for n in g.live_nodes()
                                   if n.id in hood and n.type == "claim")})
    rows.sort(key=lambda r: -r["contention"])
    return rows


def _contention(g):
    return {"global": g.contention(),
            "per_question": [{"id": r["id"], "text": r["text"][:80],
                              "contention": r["contention"]}
                             for r in _questions(g)]}


def make_handler(graph_path: str = None, tokens: dict = None,
                 pg_dsn: str = None, sso=None):
    """Exactly one of graph_path (E1, single tenant) or pg_dsn (E2,
    multi-tenant, `/graphs/{tenant_id}/...`) must be set. `tokens` maps
    bearer-token string -> role string (E1, tenant-less) or
    -> {"role", "tenant"} (E2 static tokens). `sso` is an
    perdura_sso.SSOConfig, tried before the static map when given."""
    if (graph_path is None) == (pg_dsn is None):
        raise ValueError("make_handler needs exactly one of graph_path/pg_dsn")
    tokens = tokens or {}

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):           # keep the terminal quiet
            pass

        # -- helpers --------------------------------------------------------
        def _graph_path_for(self, tenant_id):
            return _dsn_for_tenant(PG_DSN, tenant_id) if PG_DSN else GRAPH_PATH

        def _resolve_token(self):
            """Returns (role, tenant) or None. tenant is None for E1
            tenant-less tokens; SSO/E2 static tokens always carry one."""
            auth = self.headers.get("Authorization", "")
            if not auth.startswith("Bearer "):
                return None
            token = auth[len("Bearer "):].strip()
            if SSO is not None:
                try:
                    claims = SSO.verify(token)
                    return claims["role"], claims["tenant"]
                except Exception:
                    # Fall through to the static map (break-glass) on *any*
                    # SSO failure — not just an invalid token, but also a
                    # JWKS-fetch outage or network/timeout error. Break-glass
                    # that only covers bad tokens isn't break-glass.
                    pass
            entry = tokens.get(token)
            if entry is None:
                return None
            if isinstance(entry, str):
                return entry, None
            return entry["role"], entry["tenant"]

        def _send(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _read_body(self):
            """Returns the request body bytes, or None after sending 413 if
            the (caller-controlled) Content-Length exceeds the cap."""
            length = int(self.headers.get("Content-Length") or 0)
            if length > _MAX_BODY_SIZE:
                self._send(413, {"error": "request entity too large"})
                return None
            return self.rfile.read(length)

        def _authorize(self, need, tenant_id):
            """Return the caller's role, or None after sending 401/403."""
            resolved = self._resolve_token()
            if resolved is None:
                self._send(401, {"error": "missing or unknown bearer token"})
                return None
            role, token_tenant = resolved
            if _RANK[role] < _RANK[need]:
                self._send(403, {"error": f"{need} role required"})
                return None
            if PG_DSN is not None and token_tenant != tenant_id:
                self._send(403, {"error": "token is not authorized for this tenant"})
                return None
            return role

        # -- routing ----------------------------------------------------------
        def _route(self, path):
            """('' for E1, tenant_id for E2, sub-path) or (None, None, None)
            if the path doesn't belong to this server's mode."""
            if PG_DSN is not None:
                tenant_id, sub = _parse_tenant_route(path)
                return (None, None) if tenant_id is None else (tenant_id, sub)
            return ("", path)

        def do_GET(self):
            u = urlparse(self.path)
            path, qs = u.path, parse_qs(u.query)
            if path == "/health":
                return self._send(200, {"status": "ok"})
            tenant_id, sub = self._route(path)
            if tenant_id is None:
                return self._send(404, {"error": "no such route"})

            if sub in ("/questions", "/contention", "/briefing"):
                if not self._authorize("worker", tenant_id):
                    return
                g = Graph(self._graph_path_for(tenant_id))
                if sub == "/questions":
                    return self._send(200, {"open_questions": _questions(g)})
                if sub == "/contention":
                    return self._send(200, _contention(g))
                return self._briefing(g, qs)

            if sub in ("/track", "/graph"):
                if not self._authorize("operator", tenant_id):
                    return
                g = Graph(self._graph_path_for(tenant_id))
                if sub == "/track":
                    from perdura_track import track_records
                    return self._send(200, {"track_records": track_records(g)})
                return self._send(200, self._full_graph(g))   # attributed

            if sub == "/config":
                if not self._authorize("admin", tenant_id):
                    return
                if PG_DSN is None:
                    return self._send(400, {"error": "tenant config needs "
                                            "multi-tenant (--pg-dsn) mode"})
                store = store_for(self._graph_path_for(tenant_id))
                return self._send(200, store.get_tenant_config())

            self._send(404, {"error": "no such route"})

        def do_PUT(self):
            tenant_id, sub = self._route(urlparse(self.path).path)
            if tenant_id is None or sub != "/config":
                return self._send(404, {"error": "no such route"})
            if not self._authorize("admin", tenant_id):
                return
            if PG_DSN is None:
                return self._send(400, {"error": "tenant config needs "
                                        "multi-tenant (--pg-dsn) mode"})
            body = self._read_body()
            if body is None:
                return
            try:
                payload = json.loads(body or b"{}")
                domain_budgets = {str(k): float(v) for k, v in
                                  dict(payload["domain_budgets"]).items()}
            except Exception as e:
                return self._send(400, {"error": f"bad request: {e}"})
            store = store_for(self._graph_path_for(tenant_id))
            store.set_tenant_config(domain_budgets)
            self._send(200, store.get_tenant_config())

        def _briefing(self, g, qs):
            questions = g.open_questions()
            if not questions:
                return self._send(409, {"error": "no open questions"})
            qid = (qs.get("question") or [None])[0]
            if qid:
                q = g.nodes.get(qid)
                if not q or q.type != "question" or q.status != "open" \
                        or q.superseded_by:
                    return self._send(404,
                                      {"error": f"{qid} is not an open question"})
            else:
                questions.sort(key=lambda x: -g.contention(g.neighborhood(x.id)))
                q = questions[0]
            return self._send(200, {
                "question_id": q.id, "question": q.text,
                "contention": g.contention(g.neighborhood(q.id)),
                "global_contention": g.contention(),
                "briefing_prompt": build_briefing(g, q)})  # attribution stripped

        def _full_graph(self, g):
            return {
                "nodes": [{"id": n.id, "type": n.type, "text": n.text,
                           "confidence": n.confidence, "by": n.created_by,
                           "tags": n.domain_tags, "status": n.status,
                           "superseded_by": n.superseded_by}
                          for n in g.nodes.values()],
                "edges": [{"id": e.id, "type": e.type, "src": e.src,
                           "dst": e.dst, "by": e.created_by}
                          for e in g.edges.values()],
                "global_contention": g.contention()}

        def do_POST(self):
            tenant_id, sub = self._route(urlparse(self.path).path)
            if tenant_id is None or sub != "/deltas":
                return self._send(404, {"error": "no such route"})
            if not self._authorize("worker", tenant_id):
                return
            body = self._read_body()
            if body is None:
                return
            try:
                payload = json.loads(body or b"{}")
                worker = str(payload.get("worker") or "service-client")
                raw = payload["delta"]
                delta = parse_delta(raw if isinstance(raw, str)
                                    else json.dumps(raw))
            except Exception as e:
                return self._send(400, {"error": f"bad request: {e}"})
            graph_path = self._graph_path_for(tenant_id)
            # same lock + reload-merge-save as every writer
            with graph_write_lock(graph_path):
                g = Graph(graph_path)
                accepted, rejected = merge_delta(g, delta, worker)
                g.save()
                result = {"status": "merged", "accepted": accepted,
                          "rejected": rejected,
                          "global_contention": g.contention()}
            self._send(200, result)

    GRAPH_PATH = graph_path
    PG_DSN = pg_dsn
    SSO = sso
    return _Handler


def serve(graph_path=None, port=8900, host="127.0.0.1", tokens=None,
         pg_dsn=None, sso=None):
    if (graph_path is None) == (pg_dsn is None):
        raise ValueError("serve() needs exactly one of graph_path/pg_dsn")
    if sso is None:
        from perdura_sso import SSOConfig
        sso = SSOConfig.from_env()
    if tokens is None:
        if pg_dsn is not None:
            raw = os.environ.get("PERDURA_STATIC_TOKENS")
            tokens = json.loads(raw) if raw else {}
            if sso is None and not tokens:
                sys.exit("multi-tenant mode (--pg-dsn) needs SSO "
                         "(PERDURA_OIDC_*) or static tokens "
                         "(PERDURA_STATIC_TOKENS) -- otherwise no request "
                         "could ever authenticate")
        else:
            tokens = {os.environ.get("PERDURA_WORKER_TOKEN")
                      or secrets.token_urlsafe(24): "worker",
                      os.environ.get("PERDURA_OPERATOR_TOKEN")
                      or secrets.token_urlsafe(24): "operator"}
    if graph_path is not None and not os.path.exists(graph_path):
        Graph(graph_path).save()
    httpd = ThreadingHTTPServer(
        (host, port), make_handler(graph_path, tokens, pg_dsn=pg_dsn, sso=sso))
    if pg_dsn is not None:
        print(f"Perdura service (multi-tenant): http://{host}:{port}  "
              f"(routes: /graphs/{{tenant_id}}/...)")
        print(f"  Postgres: {pg_dsn}")
        print(f"  SSO: {'configured (' + sso.issuer + ')' if sso else 'not configured'}")
        for tok, entry in tokens.items():
            print(f"  static token tenant={entry['tenant']} "
                  f"role={entry['role']}: {tok}")
    else:
        print(f"Perdura service: http://{host}:{port}  "
              f"(graph: {os.path.abspath(graph_path)})")
        for tok, role in tokens.items():
            print(f"  {role:>8} token: {tok}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nService stopped.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Perdura service API (E1/E2)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--graph", default=None,
                   help="single-tenant graph file/SQLite/Postgres DSN (E1)")
    g.add_argument("--pg-dsn",
                   help="Postgres base DSN for multi-tenant mode (E2); "
                        "routes become /graphs/{tenant_id}/...")
    p.add_argument("--port", type=int, default=8900)
    p.add_argument("--host", default="127.0.0.1",
                   help="default 127.0.0.1; tokens are the only auth, so "
                        "expose beyond localhost only behind TLS/a trusted net")
    args = p.parse_args()
    graph_path = args.graph or (None if args.pg_dsn else "perdura_graph.json")
    sys.exit(serve(graph_path=graph_path, pg_dsn=args.pg_dsn,
                   port=args.port, host=args.host))
