"""
perdura_service.py — enterprise track E1: the three planes over HTTP.

The MCP station (perdura_server.py) is the worker interface for MCP clients
but has no auth. This is the authenticated REST service from the deployment
plan (docs/enterprise.md §2): the same three planes, behind bearer tokens,
with the worker/operator split enforced at the boundary.

    Delta    (write)  POST /deltas          worker | operator
    Briefing (read)   GET  /briefing        worker | operator
                      GET  /questions       worker | operator
                      GET  /contention      worker | operator
    Operator (control) GET /track           operator only
                       GET /graph           operator only

Attribution-hiding is a conductor invariant, so it is a security boundary
here: worker tokens can board, contribute, and read contention, but never
see authorship (/track and the attributed /graph are operator-only). A
worker token presented to an operator route gets 403, a missing/unknown
token gets 401.

Stdlib only (same as the Station). Writes use the same advisory lock +
reload-merge-save discipline as every other writer, so the service, the
MCP station, and CLI conductors can share one graph safely.

    python perdura_service.py --graph /abs/perdura_graph.json --port 8900
    # tokens come from PERDURA_WORKER_TOKEN / PERDURA_OPERATOR_TOKEN, or
    # are generated and printed at startup for local use.
"""

import argparse
import json
import os
import secrets
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from perdura import (Graph, build_briefing, merge_delta, parse_delta,
                     graph_write_lock)

# role rank: operator inherits everything a worker can do
_RANK = {"worker": 1, "operator": 2}


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


def make_handler(graph_path: str, tokens: dict):
    """tokens maps bearer-token string -> role ("worker" | "operator")."""

    class _Handler(BaseHTTPRequestHandler):
        graph_path = None  # set below

        def log_message(self, *a):           # keep the terminal quiet
            pass

        # -- helpers --------------------------------------------------------
        def _role(self):
            auth = self.headers.get("Authorization", "")
            if not auth.startswith("Bearer "):
                return None
            return tokens.get(auth[len("Bearer "):].strip())

        def _send(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _authorize(self, need):
            """Return the caller's role, or None after sending 401/403."""
            role = self._role()
            if role is None:
                self._send(401, {"error": "missing or unknown bearer token"})
                return None
            if _RANK[role] < _RANK[need]:
                self._send(403, {"error": f"{need} role required"})
                return None
            return role

        # -- routes ---------------------------------------------------------
        def do_GET(self):
            u = urlparse(self.path)
            path, qs = u.path, parse_qs(u.query)
            if path == "/health":
                return self._send(200, {"status": "ok"})

            if path in ("/questions", "/contention", "/briefing"):
                if not self._authorize("worker"):
                    return
                g = Graph(GRAPH)
                if path == "/questions":
                    return self._send(200, {"open_questions": _questions(g)})
                if path == "/contention":
                    return self._send(200, _contention(g))
                return self._briefing(g, qs)

            if path in ("/track", "/graph"):
                if not self._authorize("operator"):
                    return
                g = Graph(GRAPH)
                if path == "/track":
                    from perdura_track import track_records
                    return self._send(200, {"track_records": track_records(g)})
                return self._send(200, self._full_graph(g))   # attributed

            self._send(404, {"error": "no such route"})

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
            if urlparse(self.path).path != "/deltas":
                return self._send(404, {"error": "no such route"})
            if not self._authorize("worker"):
                return
            length = int(self.headers.get("Content-Length") or 0)
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
                worker = str(payload.get("worker") or "service-client")
                raw = payload["delta"]
                delta = parse_delta(raw if isinstance(raw, str)
                                    else json.dumps(raw))
            except Exception as e:
                return self._send(400, {"error": f"bad request: {e}"})
            # same lock + reload-merge-save as every writer
            with graph_write_lock(GRAPH):
                g = Graph(GRAPH)
                accepted, rejected = merge_delta(g, delta, worker)
                g.save()
                result = {"status": "merged", "accepted": accepted,
                          "rejected": rejected,
                          "global_contention": g.contention()}
            self._send(200, result)

    GRAPH = graph_path
    _Handler.graph_path = graph_path
    return _Handler


def serve(graph_path, port=8900, host="127.0.0.1", tokens=None):
    if tokens is None:
        tokens = {os.environ.get("PERDURA_WORKER_TOKEN")
                  or secrets.token_urlsafe(24): "worker",
                  os.environ.get("PERDURA_OPERATOR_TOKEN")
                  or secrets.token_urlsafe(24): "operator"}
    if not os.path.exists(graph_path):
        Graph(graph_path).save()
    httpd = ThreadingHTTPServer((host, port),
                                make_handler(graph_path, tokens))
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
    p = argparse.ArgumentParser(description="Perdura service API (E1)")
    p.add_argument("--graph", default="perdura_graph.json")
    p.add_argument("--port", type=int, default=8900)
    p.add_argument("--host", default="127.0.0.1",
                   help="default 127.0.0.1; tokens are the only auth, so "
                        "expose beyond localhost only behind TLS/a trusted net")
    args = p.parse_args()
    sys.exit(serve(args.graph, port=args.port, host=args.host))
