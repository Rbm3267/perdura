# Perdura — context for Claude Code sessions

## Thesis
A persistent knowledge graph that hires and fires LLMs based on what it has
learned about them — spending money only where it disagrees with itself.

## Three claims (do not change without discussion)
1. **Inverted persistence** — the graph is the system of record; models are
   stateless, ephemeral workers. Nothing is lost when a model is swapped.
2. **Epistemic track records** — every node is attributed. Over time the graph
   learns which models are reliable in which domains.
3. **Contention-driven economics** — cheap local models are the default;
   frontier and specialist models are summoned only where contradiction
   density rises in the graph.

## Node types
question · claim · evidence · decision · rejected

## Edge types
supports · contradicts · refines · answers · depends_on

## Conductor invariants (never violate)
- Merge path is deterministic code only — no LLM judgment in validate/merge
- Supersede-never-delete — stale nodes are marked, not removed
- Briefings are bounded — 2-hop neighborhood of most-contended question,
  capped at BRIEFING_CHAR_BUDGET
- Attribution is hidden from workers — they see confidence, never authorship

## Repo layout
perdura.py             Phase 1 implementation
perdura_store.py       Pluggable persistence (JSON / SQLite / Postgres multi-tenant, E0+E2)
perdura_sso.py         SSO bearer tokens — JWT verified against an IdP's JWKS (E2)
perdura_service.py     Authenticated HTTP service — three planes, single + multi-tenant (E1+E2)
perdura_ingest.py       PR/ADR/incident/ticket ingestion adapters -> merge_delta (E3)
perdura_connectors.py   Live GitHub PR connector -> pr_review_delta (E3)
docs/design.md         Full design doc and rationale
docs/overview.html     Transit-map architecture visual
docs/enterprise.md     Enterprise deployment plan (track E0–E3)
experiments/debate.py  Precursor multi-model debate loop

## Phase roadmap
Full detail in ROADMAP.md; docs/memoric-binary.md is the Phase 0 RFC.
- Phase 0   Memoric binary — synthetic arm passes exp 1; consensus collapse
  countered by --adversarial-every; INVERSION FINDING: contradicting claims
  are lexically CLOSER than random pairs, so distance locates disagreement
  but can't measure it — collision_candidates() + stance-audit boarding
  (--audit-every) is the repair; spec NOT locked (docs/phase0-validation.md)
- Phase 1   ✅ Graph + delta loop, Claude/Gemini/Qwen, round-robin, CLI + MCP station
- Phase 1.5 STARTED — pluggable retrieval (perdura_retrieval.py;
  --retriever graph|hybrid|chroma, graph = required baseline arm) and
  mind-map viz (perdura.py viz), now drawing collision_candidates() as
  dotted lines and servable live (perdura_service.py GET /viz,
  operator-only)
- Phase 2   STARTED — track-record engine (perdura_track.py, perdura.py
  track, operator-only MCP tool); reliability = Laplace-smoothed claim
  outcomes (promoted/corroborated vs challenged/superseded), derived
  on demand like memoric binary
- Phase 3   KERNEL SHIPPED — perdura_router.py (--route, hard budgets,
  escalation by track-record reliability/cost) + escalation_ab.py harness
  (synthetic positive control passes; now has a --real mode that runs the
  same four-arm protocol against real ClaudeWorker/GeminiWorker/QwenWorker
  instead of the scripted pair; thesis verdict still needs that run to
  actually happen with real API keys/a local model server — neither
  exists in any sandboxed dev environment this has been built in)
- Enterprise E2 SHIPPED (gate overridden, 2026-06-17) — Postgres
  multi-tenant store (RLS-isolated, advisory-lock writers), perdura_sso.py
  (JWT/JWKS), perdura_service.py /graphs/{tenant_id}/... + admin role +
  per-domain-budget config route. See "Key decisions" below and
  docs/enterprise.md §1/§7 for the full record of the override.
- Enterprise E3 SHIPPED (gate override extended, 2026-06-17) —
  perdura_ingest.py: adr/incident/ticket/pr adapters map a structured item
  to the strict-JSON delta schema; ingest() merges through the same
  conductor path (write lock, validation, attribution) as an LLM worker
  turn. adapter:<source> attribution makes cross-stream collision audits
  and per-stream track records fall out of existing machinery for free.
  See docs/enterprise.md §1/§6/§7.

## Session conventions
- Every major change updates README.md AND index.html in the same commit —
  the operator should never have to ask. If a doc under docs/ changed,
  re-run tools/build_doc_pages.py and commit the regenerated pages too.
- Invariant changes need a test in tests/ (offline pytest, no keys/server);
  CI (.github/workflows/ci.yml) runs `pytest` on every push/PR. Run it
  locally before pushing: `.venv/bin/python -m pytest -q`.
- Auto-merge: Claude has standing approval to merge PRs once all checks
  pass — don't wait for the operator's "merge" reply. Open the PR, let CI
  go green, then mark it ready and merge. Still pause for anything
  genuinely destructive or a scope change the operator must decide.

## Key decisions already made — do not re-litigate
- perdura_graph.json is gitignored; the mind's state stays local by default
- Workers never see authorship of prior nodes (counters anchoring)
- Schema validation is strict; fix malformed deltas in parse_delta, not schema
- Memoric binary is derived state — computed on demand (encode_node), never
  persisted into the graph file (no migrations, no staleness, spec stays free)
- contention() defaults to edge-only (w=0) until Phase 0 experiments 1+3
  pass (issue #11); the 0.5 blend is opt-in via --memoric-weight and the
  edge-only run stays the required baseline arm
- Storage is pluggable by file extension/prefix (perdura_store.py): JSON
  file is the default and stays byte-identical; .db/.sqlite[3] selects
  SQLite WAL; postgres(ql):// selects the multi-tenant Postgres tier (E2).
  Same advisory lock + reload-merge-save discipline for every store
- `redact` is the one sanctioned exception to supersede-never-delete:
  operator-only, destroys node TEXT only (structure/attribution/lineage
  survive), logged — the GDPR escape hatch (docs/enterprise.md §5)
- **The E2 gate was knowingly overridden, not skipped.** docs/enterprise.md
  §7 states the gate for E2+: the Phase 3 escalation A/B (contention-routed
  vs periodic vs random at equal cost) must show contention-routing wins,
  or stop at E1. That A/B has never run for real — only the synthetic
  positive control in escalation_ab.py has. The operator explicitly chose
  (2026-06-17, via direct instruction) to build the full E2 multi-tenant
  control plane ahead of that gate anyway. Do not silently re-impose the
  gate on E2 work already shipped; any claim about contention-routing's
  real-world cost-effectiveness remains open until the real A/B runs
- **The E3 gate override extends the E2 one.** docs/enterprise.md §7 said
  the gate "still applies to E3" when E2 shipped. When E3 (ingestion
  adapters, perdura_ingest.py) was built next, the operator explicitly
  extended the same override rather than let the contradiction stand
  silently. Any claim about contention-routing's real-world
  cost-effectiveness still waits on the real escalation A/B — building E3
  doesn't change that, it only means the claim-supply layer no longer
  waits on it either.
- Postgres RLS is invisible to a superuser regardless of `FORCE ROW LEVEL
  SECURITY` — the application's Postgres credential must be a
  non-superuser, `NOBYPASSRLS` role or tenant isolation is fiction. The
  tables must also be owned by that role (FORCE doesn't bind to a role that
  doesn't own the table). tests/test_postgres_store.py proves isolation
  under exactly this credential shape, including fail-closed behavior
- `/viz` (perdura_service.py) is operator-only, not worker-tier, even
  though it strips attribution like `/briefing` does — unlike `/briefing`
  it renders the *entire* graph's text with no 2-hop bound, so the
  unbounded-egress concern (docs/enterprise.md §5) puts it on the
  operator side of the line
- perdura_connectors.py's GitHub fetch is dependency-injected (`fetch=`
  param, default a real urllib call) so the connector follows the same
  offline-test convention as every adapter — tests/test_connectors.py
  never touches the network or needs a token
- escalation_ab.py's `run_arm` flip-counting was generalized from a
  hardcoded `"challenger"` name check to `{s.name for s in registry if
  s.tier == "frontier"}`, so it works for any named worker — required for
  `--real` mode to produce meaningful metrics for claude/gemini/qwen/mock
  alike; tests/test_escalation_ab.py proves this with MockWorker standing
  in for both the local and frontier slots
- `PostgresStore` pools connections (`psycopg_pool.ConnectionPool`, one
  pool per DSN, class-level so it's shared across every tenant on that
  database) instead of opening one per load/save/config call — needed
  because `perdura_service.py` builds a new store per HTTP request, so
  pooling has to live above the instance. `lock()`'s advisory-lock
  connection deliberately stays outside the pool: it's held for an entire
  reload-merge-save cycle, not one query
