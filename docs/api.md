# perdura_service.py — HTTP API reference

This is the route-by-route reference for the enterprise E1 (single-tenant)
and E2 (multi-tenant) HTTP service. For the narrative version — why these
three planes exist, the auth model, the deployment plan — see
[enterprise.md](enterprise.md). This document exists to be looked things up
in, not read top to bottom.

## Starting the service

```bash
# E1 — single tenant, one graph file (or SQLite/Postgres DSN)
python perdura_service.py --graph /abs/perdura_graph.json --port 8900

# E2 — multi-tenant, one Postgres database, RLS-isolated per tenant
python perdura_service.py --pg-dsn postgresql://host/perdura --port 8900
```

`--host` defaults to `127.0.0.1`. Bearer tokens are the only auth, so if you
bind beyond localhost, put TLS or a trusted network in front of it — the
service itself does not terminate TLS.

## Auth

Every route except `/health` and `/ready` requires `Authorization: Bearer
<token>`. A request is authorized if either layer below recognizes the
token; SSO is tried first, static tokens are the fallback (so they work as
break-glass alongside SSO, not just instead of it):

- **SSO** — a JWT issued by your IdP, verified against its JWKS. Configure
  via `PERDURA_OIDC_*` env vars (see `perdura_sso.SSOConfig.from_env()`).
  Role and tenant come from claims the IdP vouched for.
- **Static tokens** — `PERDURA_WORKER_TOKEN` / `PERDURA_OPERATOR_TOKEN` in
  E1 (tenant-less), or `PERDURA_STATIC_TOKENS` in E2: a JSON map of
  `token -> {"role": ..., "tenant": ...}`. Generated randomly at startup
  and printed once if you don't set them — fine for a quick local run, not
  for anything you'll come back to.

Three roles, each ranked above and inheriting the one below: `worker` <
`operator` < `admin`. In E2, a token's `tenant` claim must also match the
`{tenant_id}` in the URL, or the request gets `403` — Postgres row-level
security is the backstop behind that check, not a substitute for it.

Failure modes: `401` for a missing/unrecognized token, `403` for a token
that's valid but under-ranked or wrong-tenant.

## Route prefix: E1 vs E2

E1 routes are unprefixed (`/questions`, `/deltas`, ...). E2 routes gain a
`/graphs/{tenant_id}/` prefix in front of every route below **except**
`/health` and `/ready`, which describe the service process, not a tenant's
data, in both modes. An E1-shaped request (no tenant prefix) against an E2
server returns `404`, and vice versa.

## Operations routes (no tenant prefix, either mode)

| Route | Method | Auth | Notes |
|---|---|---|---|
| `/health` | GET | none | Always `200 {"status": "ok"}`. Liveness only — does not touch the store. |
| `/ready` | GET | none | Pings the configured store (`ping()` — directory writability for a JSON file, `SELECT 1` for SQLite/Postgres). `200 {"status": "ready"}` or `503 {"status": "unavailable"}`. Exempt from rate limiting, so infra probes aren't throttled. |

Use `/health` for "is the process up" and `/ready` for "can it actually
serve a request right now" — the distinction matters most right after
startup or during a Postgres failover.

## Worker plane (role: worker+)

| Route | Method | Notes |
|---|---|---|
| `/questions` | GET | Open questions, sorted by contention descending. Each entry: `id`, `text`, `domain_tags`, `contention`, `claims` (count in its 2-hop neighborhood). |
| `/contention` | GET | `{"global": <float>, "per_question": [...]}`. |
| `/briefing` | GET | The bounded 2-hop briefing for one question — attribution stripped. `?question=<id>` picks a specific open question; omitted, it picks the most contended one. `404` if the id isn't an open question, `409` if there are no open questions at all. |
| `/deltas` | POST | Submit a worker delta. Body: `{"worker": "<name>", "delta": {...} | "<json-string>"}`. Merges through the same conductor path as an LLM worker turn (lock, validate, attribute, save). Returns `{"status": "merged", "accepted": <n>, "rejected": <n>, "global_contention": <float>}`. |

`/briefing`'s response also includes `question_id`, `question`,
`contention` (for that question), `global_contention`, and
`briefing_prompt` — the actual bounded text a worker would receive.

## Operator plane (role: operator+)

| Route | Method | Notes |
|---|---|---|
| `/track` | GET | `{"track_records": [...]}` — per-model, per-domain reliability (Laplace-smoothed claim outcomes). |
| `/graph` | GET | The full graph, attributed (`by` on every node/edge). Operator-only specifically because it un-hides authorship. |
| `/viz` | GET | Live force-directed mind-map as HTML (`Content-Type: text/html`), same renderer as `perdura.py viz`. Strips attribution like `/briefing`, but operator-only anyway because it has no 2-hop bound — it renders the entire graph's text. |
| `/usage` | GET | In-memory metering snapshot for this tenant (E1: the whole process) since the server started. See below. |

### `/usage` response shape

```json
{
  "requests": 42,
  "by_route": {"/questions": 30, "/deltas": 12},
  "by_status": {"200": 40, "403": 2},
  "bytes_in": 4096,
  "bytes_out": 51200,
  "deltas_accepted": 10,
  "deltas_rejected": 2
}
```

This is **a foundation for billing visibility, not billing-grade
infrastructure**: it's in-memory, per-process, resets on restart, and is
never persisted. Good for "is this tenant calling us at all, and through
which routes" during a pilot; not a substitute for a real metering
pipeline if you need invoices.

## Admin plane (role: admin, E2 only)

| Route | Method | Notes |
|---|---|---|
| `/config` | GET | `{"domain_budgets": {...}}` for this tenant. |
| `/config` | PUT | Body: `{"domain_budgets": {"<domain>": <float>, ...}}`. Replaces the tenant's router budgets; returns the new config. |

Both return `400` if called against an E1 (single-tenant, `--graph`)
server — tenant config only exists where there's a tenant.

## Errors

All error bodies are `{"error": "<message>"}`. Status codes used across the
API: `400` (malformed body/header), `401` (no/unknown token), `403`
(under-ranked role, or wrong tenant), `404` (no such route, or — for
`/briefing?question=`— not an open question), `409` (no open questions),
`413` (request body over the 10MB cap), `429` (rate limited), `503`
(`/ready` only — store unreachable).

## Operational env vars

| Var | Default | Effect |
|---|---|---|
| `PERDURA_LOG_LEVEL` | `INFO` | Verbosity for the `perdura.service` logger. One structured `INFO` line per request (method, route, tenant, role, status, bytes in/out, elapsed ms); raw HTTP request lines log at `DEBUG`. |
| `PERDURA_RATE_LIMIT_PER_MINUTE` | `0` (disabled) | Fixed-window per-credential rate limit (also settable via `--rate-limit-per-minute`). Keyed by bearer token, or client IP if no token was presented. `/health` and `/ready` are exempt. Violations return `429` with `Retry-After: 60`. |
| `PERDURA_OIDC_*` | unset | SSO configuration — see `perdura_sso.SSOConfig.from_env()`. |
| `PERDURA_STATIC_TOKENS` | unset | E2 static token map, JSON. |
| `PERDURA_WORKER_TOKEN`, `PERDURA_OPERATOR_TOKEN` | random at startup | E1 static tokens. |

## Deployment

A `Dockerfile` (multi-stage, non-root) and `docker-compose.yml` (service +
Postgres, healthchecked against `/ready`) are at the repo root — see the
top of `docker-compose.yml` for the one-command demo of the full E2 path.
