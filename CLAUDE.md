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
docs/phase3-ab-results.md  Real escalation A/B results (2026-06-25) — gate
                        tested, contention-routing trailed periodic on
                        outcome flips in all 3 clean runs
experiments/debate.py  Precursor multi-model debate loop

## Phase roadmap
Full detail in ROADMAP.md; docs/memoric-binary.md is the Phase 0 RFC.
- Phase 0   Memoric binary — synthetic arm passes exp 1; consensus collapse
  countered by --adversarial-every; INVERSION FINDING: contradicting claims
  are lexically CLOSER than random pairs, so distance locates disagreement
  but can't measure it — collision_candidates() + stance-audit boarding
  (--audit-every) is the repair; EXOGENEITY FIX (2026-06-25): Node/Edge now
  carry boarding_mode (organic/adversarial/audit, default organic so old
  graphs load unchanged), stamped by the conductor at merge time; exp 1
  (memoric_eval.py) and the inversion probe (collision_probe.py) now score
  organic+audit contradictions separately from manufactured
  --adversarial-every ones instead of pooling them; spec NOT locked
  (docs/phase0-validation.md)
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
- Phase 3   KERNEL SHIPPED, REAL A/B RUN (2026-06-25) — perdura_router.py
  (--route, hard budgets, escalation by track-record reliability/cost) +
  escalation_ab.py harness, synthetic positive control passes. The --real
  mode has now run three clean times against real Claude/Gemini calls
  (--local mock standing in for an unavailable local model): targeting
  precision confirmed (contention-routing finds the hottest live
  disagreement every run), but on outcome flips at equal cost —
  contention-routing's actual bet — it trailed periodic escalation all
  three times (0v1, 1v4, 1v3). Thin n, one real confound found (contention
  dilutes with graph size regardless of budget, capping the contention
  arm's sample), one open alternate reading (flip-counting may not credit
  dispute resolution) not yet rescuing the number. Local-tier half of the
  thesis still untested (no real cheap model available). Full record:
  docs/phase3-ab-results.md.
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
  or stop at E1. At the time E2 was built that A/B had never run for real
  — only the synthetic positive control in escalation_ab.py had. The
  operator explicitly chose (2026-06-17, via direct instruction) to build
  the full E2 multi-tenant control plane ahead of that gate anyway. Do not
  silently re-impose the gate on E2 work already shipped.
- **The E3 gate override extends the E2 one.** docs/enterprise.md §7 said
  the gate "still applies to E3" when E2 shipped. When E3 (ingestion
  adapters, perdura_ingest.py) was built next, the operator explicitly
  extended the same override rather than let the contradiction stand
  silently.
- **The real escalation A/B has since run (2026-06-25) — and the result
  is unfavorable, not absent.** `docs/phase3-ab-results.md` is the full
  record: three clean real-worker runs (Claude and/or Gemini, `--local
  mock`), all in the same direction. Contention-routing's targeting
  precision is confirmed (2–7× periodic's mean contention at the moment of
  escalation, every run). But on the gate's actual metric — outcome flips
  at equal cost — contention-routing trailed periodic in all three (0 vs
  1, 1 vs 4, 1 vs 3 flips). This is thin evidence (1–4 escalations/arm/run)
  and confounded by a real finding (this seed graph's contention dilutes
  with size regardless of budget, capping the contention arm's own sample
  no matter how long the run goes) and one open alternate reading (raw
  flip-counting may undercount dispute *resolution*, not just creation —
  supported once, ambiguous once). None of that rescues the headline
  direction. Do not describe contention-routing's real-world
  cost-effectiveness as "untested" or "still open" going forward — it has
  been tested, and the project record (ROADMAP.md, docs/enterprise.md
  §1/§7, docs/phase3-ab-results.md) says plainly that the test currently
  cuts against the thesis. The local-tier half of claim 3 (cheap-by-default
  is good enough most of the time) remains genuinely untested — `--local
  mock` stood in for a real cheap model in every run, since none has been
  reachable in any environment this has been built in.
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
