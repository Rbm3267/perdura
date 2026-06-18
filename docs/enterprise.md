# Perdura in the enterprise — deployment plan

*Status: E0, E1, E2, and E3 shipped. Everything here is the application layer
on top of the Phase 3 research bet: if contention-driven routing does not
beat periodic/random escalation at equal cost, this document describes a
nice decision-record graph, not a product. The research result is the moat.*

*Gate note: the documented gate for E2+ below (§7) is the Phase 3 escalation
A/B passing on real workers. That A/B has not been run for real — only the
synthetic positive control in `escalation_ab.py` has. The operator made an
explicit, informed decision to build E2 anyway, ahead of the gate, rather
than have it skipped silently — and made the same explicit decision again
for E3, which §7 had called out as still gated even after the E2 override.
This paragraph is that record. The gate still applies to anything claimed
about contention-routing's real-world cost-effectiveness; it does not apply
to "can this codebase serve a multi-tenant, SSO-authenticated, RLS-isolated
control plane" (E2) or "can PR/ADR/incident/ticket streams propose deltas
through the normal merge path, with cross-stream collision audits falling
out of existing machinery" (E3) — both of which now answer for real.*

## 1. Why the concept transfers

Perdura's thesis — the graph is the system of record; models are ephemeral
workers — generalizes past models. In an organization, **people, vendors,
and teams are also ephemeral workers**: engineers leave, contractors rotate,
vendors get swapped, and the knowledge either walks out the door or
fossilizes in stale wikis. The same three claims, at org altitude:

1. **Inverted persistence** → institutional memory that survives churn.
   `question → claims → evidence → decision`, with `rejected` nodes
   preserving *why the road not taken was not taken* — the thing every org
   loses first. Supersede-never-delete is a free audit trail.
2. **Epistemic track records** → reliability per *agent and team, per
   domain* — never per individual (a deliberate line: "the platform team's
   capacity claims hold 80% of the time; their timeline claims hold 40%"
   is actionable without being a performance review). Attribution hiding
   doubles as blind review; track records stay operator-only.
3. **Contention-driven economics** → contention-driven **attention**. The
   scarcest enterprise resource is not frontier-model tokens, it is senior
   engineering time. A contention metric over a cross-department graph
   routes design reviews to where the org measurably disagrees with itself,
   instead of by org chart or volume.

The cross-department failure mode Perdura is uniquely shaped for: two teams
building on contradicting assumptions that nobody notices until integration.
In a shared graph that is a `contradicts` edge waiting to be detected — the
collision detector + stance-audit boarding (docs/memoric-binary.md §7.5)
runs continuously across team boundaries, auditing near-miss claim pairs no
single meeting would ever put in the same room.

## 2. The integration insight: the worker protocol is already a service boundary

Workers board, receive a bounded briefing, return a strict-JSON delta, and
a deterministic conductor merges it. Nothing about that assumes the worker
is an LLM we spawned. Everything integrates as a worker or an operator,
which collapses the API surface to three planes:

| Plane | Surface | Who uses it |
|---|---|---|
| **Delta** (write) | `POST /graphs/{id}/deltas` — the existing strict-JSON delta schema, verbatim | platform AI agents, ingestion adapters, humans in a UI |
| **Briefing** (read) | `GET /briefing?question=…` — bounded, attribution-stripped context packets (also `/questions`, `/contention`) | anything that prompts a model |
| **Operator** (control) | track records, the attributed graph, contention dashboards, router policy, budgets — the Station plus the operator routes | platform admins |

*(E1 ships these as `perdura_service.py` — `POST /deltas`, `GET /briefing`,
`/questions`, `/contention` for worker tokens; `GET /track`, `/graph`,
`/viz` (live collision-aware mind map) for operator tokens. The
`/graphs/{id}/…` multi-tenant prefix arrives with the Postgres store in
E2.)*

The merge path stays pure code with no LLM in the loop — schema validation
rejects garbage at the door, every write is cheap, auditable, and
deterministic. That is exactly what an enterprise platform team wants from
a dependency.

**Where it sits in a stack:** not a database, not an orchestrator — a
memory-and-routing tier between the LLM orchestration layer and the data
layer. It does not compete with the vector DB (the Phase 1.5 retriever
treats one as a pluggable backend underneath); it is the epistemic layer
above it that knows where its own knowledge disagrees with itself and uses
that to decide when to spend. Every other AI feature is a cost center that
scales with usage; an epistemic router is the component that **caps** model
spend.

## 3. Deployment shapes

In ascending order of commitment:

1. **Library / SDK** — the conductor is stateless deterministic code over a
   graph store; embed it in-process.
2. **Sidecar service** — one conductor per tenant graph; the platform talks
   HTTP/MCP to it. The MCP station already works this way today.
3. **Managed multi-tenant service** — graph-per-tenant, conductors as
   stateless workers. Because the conductor is single-writer-per-graph
   deterministic code, each graph is a partition with a leader — a
   well-understood scaling shape.

## 4. Storage tier (E0, E2 — shipped)

The graph is the only state, so storage is the first thing that must stop
being an experiment. `perdura_store.py` makes persistence pluggable, chosen
by file extension, with everything above it unchanged:

| Tier | Path | Properties |
|---|---|---|
| JSON file | `perdura_graph.json` (default) | byte-identical Phase 1 behavior, human-diffable, single box |
| SQLite (WAL) | `perdura_graph.db` | transactional saves, concurrent readers during writes, multi-process on one box — validated 60/60 merges under 4 concurrent conductors |
| Postgres | `postgresql://host/db?tenant=acme` | same interface, graph-per-tenant, row-level security, the managed-service tier |

All three tiers keep the same correctness story: writers serialize via the
advisory lock (reload → merge → save), saves are atomic (write-rename / one
transaction), readers never observe a torn graph. Supersede-never-delete
means SQLite and Postgres saves are pure upserts; the append-only merge log
inserts only new entries.

Postgres adds tenant isolation as a hard backstop, not just an app-level
WHERE clause: every tenant table has row-level security `FORCE`d, scoped to
`current_setting('perdura.tenant_id')`. RLS never applies to a Postgres
superuser regardless of `FORCE` — the application's database credential
must be a non-superuser, `NOBYPASSRLS` role, or the isolation guarantee is
fiction. `tests/test_postgres_store.py` proves isolation against that exact
role shape, including fail-closed behavior (no tenant set → no rows, not all
rows). The write lock is a Postgres advisory lock keyed by tenant, so writers
serialize across hosts, not just within one box.

`PostgresStore` pools connections (`psycopg_pool.ConnectionPool`, one pool
per database DSN, shared across every tenant on it) rather than opening a
fresh connection per load/save/config call — `perdura_service.py`
constructs a new store per HTTP request, so pooling lives at the class
level, above any one store instance, or it would just move the same
per-request connection cost one layer up. The advisory lock in `lock()`
keeps its own dedicated connection outside the pool, since it's held for
an entire reload-merge-save cycle, not a single query.

## 5. Security and compliance — the accidental enterprise features

Existing conductor invariants, re-read through a procurement lens:

- **Bounded briefings are a data-egress control.** Only a 2-hop
  neighborhood, capped at `BRIEFING_CHAR_BUDGET`, ever leaves the graph
  toward a third-party model API. The maximum exposure per model call is
  provably bounded, and the bound is a named constant. Most RAG pipelines
  can make no such statement.
- **Attribution hiding is context isolation.** Workers never see who
  authored prior nodes; generalized: the model never sees more provenance
  than policy allows.
- **Supersede-never-delete is the audit trail** regulators want — with one
  honest conflict: GDPR right-to-erasure. The resolution is **redaction,
  not deletion** (`perdura.py redact <node-id>`, shipped): the text payload
  is destroyed; type, confidence, attribution, edges, and supersession
  lineage survive, so the epistemic record stays intact while the content
  does not. Redactions are operator-only and logged. Memoric encodings are
  derived on demand (never persisted), so no stale copy of redacted text
  outlives the command. True crypto-shredding arrives with
  encrypted-at-rest storage in the Postgres tier.
- **The Phase 3 model registry is a per-tenant policy engine.** Enterprises
  have model allowlists, regional routing, and budget ceilings;
  contention-driven routing and compliance-driven routing are the same
  lookup with different constraints. `Router.domain_budgets` (E2: persisted
  per tenant via `GET`/`PUT /graphs/{tenant_id}/config`, admin-only) caps
  spend per domain tag, attributed to the hottest open question at decision
  time — a per-tenant, per-domain ceiling, not just a global one.

## 6. Claim supply: ingestion

The engine is only as good as its claim supply, and an enterprise platform
is a claim factory: tickets, PR reviews, incident retros, support
escalations. Adapters watch those streams and **propose deltas through the
normal merge path** — no privileged side door, so every conductor invariant
holds for machine-ingested claims too. Humans board the same way (the MCP
station already accepts any MCP client — an engineer in an IDE is just
another worker).

Metering falls out of the same seams: deltas merged, briefings served,
audit boardings run — usage-based billing aligned with value, not raw
token burn.

**Shipped:** `perdura_ingest.py` — one pure function per stream
(`adr_delta`, `incident_delta`, `ticket_delta`, `pr_review_delta`) maps a
plain dict (already-fetched item data, not a live API call) to the
strict-JSON delta schema; `ingest()` merges it through `merge_delta()`
under the same write lock as an LLM worker turn — `python perdura.py
ingest <file.json> --adapter {adr,incident,ticket,pr}`, single item or
batch, optionally `--question <node-id>` to attach to an existing open
question instead of opening a new one. Attribution is stamped
`adapter:<source>` (e.g. `adapter:incident`), so two things fall out with
no new machinery: per-domain track records (`perdura.py track`) score each
stream's reliability over time exactly like an LLM worker, and the
collision detector (`--audit-every`) finds lexically-close, unlinked
claims **across streams** — an ADR's context claim and an incident's
root-cause claim about the same area — which is the cross-stream
collision audit this section called for.

**Also shipped:** `perdura_connectors.py` closes the "already-fetched
item" gap above for one stream — `fetch_github_prs`/`sync_github_prs`
pull merged PRs and their review comments straight from the GitHub REST
API and feed them through `pr_review_delta`, with a cursor (highest PR
number synced) for idempotent incremental re-sync. `perdura.py
sync-github --repo owner/name --token $GITHUB_TOKEN` runs it end to end.
The HTTP transport is an injected `fetch` callable — the default hits
`api.github.com` via `urllib`, tests supply a fake — so the connector's
mapping and cursor logic is covered offline (`tests/test_connectors.py`)
the same way every other adapter is, with no token or live call required
to prove it works.

## 7. Enterprise track roadmap

Runs parallel to the research phases; each step is gated so the research
focus is never displaced.

- **E0 — storage tier** ✅ `perdura_store.py`: pluggable JSON/SQLite
  persistence, `redact` compliance command.
- **E1 — service API** ✅ `perdura_service.py`: the three planes over HTTP
  with bearer auth and the worker/operator split enforced at the boundary
  (worker tokens board, contribute, and read contention; `/track` and the
  attributed `/graph` are operator-only — attribution-hiding becomes an
  access-control boundary, not just a convention). The Station gains a
  read-only **Conductor panel**: the model registry and a live routing
  preview (per open question, what the contention policy would do at the
  current threshold). Worker schedules and budgets become *mutable* config
  in E2.
- **E2 — multi-tenant control plane** ✅ (gate overridden — see below)
  `perdura_store.py` (`PostgresStore`, RLS-isolated, graph-per-tenant),
  `perdura_sso.py` (JWT bearer tokens verified against an IdP's JWKS, no
  hand-rolled crypto), `perdura_service.py` (`/graphs/{tenant_id}/...`
  routing, the `admin` role, tenant claims enforced at the HTTP layer in
  front of RLS), and a real `GET`/`PUT /graphs/{tenant_id}/config` for
  per-domain router budgets (`perdura_router.py`'s `domain_budgets`,
  persisted in Postgres instead of a CLI flag).
- **E3 — ingestion adapters** ✅ (gate overridden — see below)
  `perdura_ingest.py`: `adr_delta`/`incident_delta`/`ticket_delta`/
  `pr_review_delta` map a structured item to the strict-JSON delta schema;
  `ingest()` merges through the normal conductor path (same write lock,
  same validation, same attribution stamping as an LLM worker turn) — no
  privileged side door. `perdura.py ingest <file> --adapter ...` (CLI),
  single item or batch. Attribution `adapter:<source>` makes cross-stream
  collision audits fall out of the existing collision detector
  (`--audit-every`) and gives each stream its own track record for free.
  `perdura_connectors.py` (same date) adds a live GitHub PR connector —
  `perdura.py sync-github --repo owner/name` — so `pr_review_delta` can
  run against the real API instead of a pre-fetched dict, with a cursor
  file for idempotent incremental sync.
- **Gate for E2+:** the Phase 3 escalation A/B (contention-routed vs
  periodic vs random at equal cost) must show contention-routing wins.
  If it does not, stop at E1 and say so honestly. **This gate was not
  satisfied when E2 or E3 was built** — only the synthetic positive
  control in `escalation_ab.py` has run, never the real-worker A/B. The
  operator explicitly chose to override the gate for both (see the status
  note at the top of this document); any claim about contention-routing's
  real-world cost-effectiveness still waits on the real A/B.
