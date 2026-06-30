# Perdura — context for Claude Code sessions

## Thesis
A persistent knowledge graph that outlives every model that works it,
building an attributed, per-model track record of which LLMs to trust — in
which domains — as it goes. (Spend-by-contention was the original headline
framing; claim 3 below records why the pitch now leads with persistence and
track records instead.)

## Three claims (do not change without discussion)
1. **Inverted persistence** — the graph is the system of record; models are
   stateless, ephemeral workers. Nothing is lost when a model is swapped.
2. **Epistemic track records** — every node is attributed. Over time the graph
   learns which models are reliable in which domains.
3. **Contention-driven economics** — cheap local models are the default;
   frontier and specialist models are summoned to bound spend. The
   *default* escalation trigger is now a validated periodic cadence, not
   contradiction density: the real 6-run A/B (docs/phase3-ab-results.md)
   found contention-triggered escalation never beats periodic on outcome
   flips at equal cost (5 losses, 1 tie). Contention-triggered escalation
   (`--route contention`) stays fully supported as a research arm — its
   targeting precision is confirmed (it reliably finds the hottest live
   disagreement) — but it is no longer the recommended default. Pivoted
   2026-06-25; do not revert without a new real-worker result that
   reverses the 6/6 direction.

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
perdura_providers.py   Pluggable LLM provider config -- quick connect (config, not code)
perdura_store.py       Pluggable persistence (JSON / SQLite / Postgres multi-tenant, E0+E2)
perdura_sso.py         SSO bearer tokens — JWT verified against an IdP's JWKS (E2)
perdura_service.py     Authenticated HTTP service — three planes, single + multi-tenant (E1+E2)
perdura_ingest.py       PR/ADR/incident/ticket ingestion adapters -> merge_delta (E3)
perdura_connectors.py   Live GitHub PR connector -> pr_review_delta (E3)
docs/design.md         Full design doc and rationale
docs/overview.html     Transit-map architecture visual
docs/enterprise.md     Enterprise deployment plan (track E0–E3)
docs/api.md            perdura_service.py HTTP API reference (E4)
docs/phase3-ab-results.md  Real escalation A/B results (2026-06-25) — gate
                        tested, contention-routing trailed periodic on
                        outcome flips in all 3 clean runs
Dockerfile, docker-compose.yml  Container image + E2 deployment demo (E4)
CHANGELOG.md           Version history
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
  --adversarial-every ones instead of pooling them. FIRST REAL PASS
  (2026-06-25): a 30-turn Claude+Gemini run (first to combine
  --adversarial-every and --audit-every) clears exp 1 on real data for the
  first time — AUC 0.944 simhash, organic+audit-filtered (blake2b 0.370,
  worse than chance — settles open question 6 against blake2b). Exp 3
  narrows to a near-miss (0.868, up from 0.56); exp 2 stays blocked for the
  third real session running (0 decisions/supersessions — a structural
  protocol gap, not thin data). The inversion finding now holds on organic
  contention too (simhash AUC 0.395 vs 0.187 adversarial). Spec still NOT
  locked (needs exp 1 *and* 3); heterogeneous workers (local model vs
  frontier) remain completely untested in every environment this has run
  in (docs/phase0-validation.md)
- Phase 1   ✅ Graph + delta loop, Claude/Gemini/Qwen, round-robin, CLI + MCP station
- Phase 1.5 STARTED — pluggable retrieval (perdura_retrieval.py;
  --retriever graph|hybrid|chroma, graph = required baseline arm) and
  mind-map viz (perdura.py viz), now drawing collision_candidates() as
  dotted lines and servable live (perdura_service.py GET /viz,
  operator-only). SHIPPED 2026-06-25: --memoric-briefings appends a
  bounded collision-locator section (lexically-close, unlinked claims) to
  every worker's briefing, not just --audit-every turns — the concrete
  mechanism for claim 1's "onload/offload models rapidly without losing
  context"; reuses the already-validated collision_candidates(), does not
  touch the still-gated --memoric-weight contention blend, off by default,
  byte-identical output when unset (tests/test_briefing.py)
- Phase 2   STARTED — track-record engine (perdura_track.py, perdura.py
  track, operator-only MCP tool); reliability = Laplace-smoothed claim
  outcomes (promoted/corroborated vs challenged/superseded), derived
  on demand like memoric binary
- Phase 3   KERNEL SHIPPED, REAL A/B RUN, 6-RUN POOL (2026-06-25) —
  perdura_router.py (--route, hard budgets, escalation by track-record
  reliability/cost) + escalation_ab.py harness, synthetic positive control
  passes. The --real mode has now run six clean times against real
  Claude/Gemini calls (--local mock standing in for an unavailable local
  model): targeting precision confirmed and strengthened by pooling
  (contention beats periodic in 6/6 runs, random in 5/6; pooled weighted
  mean cont@esc 0.348 vs 0.154 vs 0.226), but on outcome flips at equal
  cost — contention-routing's actual bet — it has never once beaten
  periodic (5 losses, 1 tie; pooled 5 vs 20 flips over 21 equal-cost
  escalations). The dilution confound (contention dilutes with graph size
  regardless of budget, capping the contention arm's per-run sample) is
  reconfirmed by runs 6-8 repeating run 3's exact escalation count. The
  "resolution not creation" alternate reading now holds modestly at the
  pooled level (contention arm's mean final contention is lowest of the
  three escalating arms) but isn't yet separable from dilution. Local-tier
  half of the thesis still untested (no real cheap model available). Full
  record: docs/phase3-ab-results.md.
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
- Enterprise E4 SHIPPED (2026-06-25, v0.2.0) — operational hardening, no
  gate question involved (pure ops, not a routing-thesis claim): structured
  logging (PERDURA_LOG_LEVEL), per-credential rate limiting
  (PERDURA_RATE_LIMIT_PER_MINUTE / --rate-limit-per-minute), a /ready probe
  backed by real store.ping() (vs /health's always-200), an operator-only
  /usage meter (in-memory per-tenant counters — foundation for billing
  visibility, not billing-grade), Dockerfile + docker-compose.yml for the
  E2 path. Full reference: docs/api.md. Paired decision: local-model work
  (LM Studio/Ollama) is shelved, not abandoned — see "Key decisions" below.
- Productization: pluggable provider config SHIPPED (2026-06-27, v0.3.0) —
  perdura_providers.py: a JSON/YAML file adds named workers (any protocol
  perdura already speaks: anthropic/google-genai/openai/lmstudio-native/
  mock) on top of perdura.py's built-in WORKER_FACTORIES, selected via
  --workers same as always; --provider-config PATH or an auto-discovered
  ./perdura_providers.json. cost/tier overrides feed registry_from_workers
  so a config-defined vendor escalates like a real frontier worker. API
  keys are always read from an env var named in api_key_env, never written
  into the config file. This is the "quick connect" mechanism the
  productization-direction decision below called for; see that bullet for
  the framing and tests/test_providers.py + tests/test_cli.py for coverage.

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
- **The real escalation A/B has since run (2026-06-25), six clean times —
  and the result is unfavorable, not absent.** `docs/phase3-ab-results.md`
  is the full record: six clean real-worker runs (Claude and/or Gemini,
  `--local mock`), all in the same direction. Contention-routing's
  targeting precision is confirmed and *strengthened* by the larger
  sample (pooled, escalation-weighted mean contention at escalation 0.348
  vs 0.154 periodic vs 0.226 random — contention wins in 6/6 runs against
  periodic, 5/6 against random). But on the gate's actual metric —
  outcome flips at equal cost — contention-routing has not beaten periodic
  once across all six runs (5 losses, 1 tie; pooled 5 vs 20 flips over 21
  equal-cost escalations). This is no longer the thin 1–4-escalations/run
  evidence of the first three runs — pooling six independent real-worker
  runs gives 21–24 escalations per arm — and the direction has not
  flipped once. It remains confounded by a real finding (this seed
  graph's contention dilutes with size regardless of budget, capping the
  contention arm's per-run sample — reconfirmed again by runs 6–8 landing
  on the identical escalation count as run 3) and the alternate reading
  (raw flip-counting may undercount dispute *resolution*, not just
  creation) now holds modestly at the pooled level but isn't yet
  separable from the dilution effect. None of that rescues the headline
  direction. Do not describe contention-routing's real-world
  cost-effectiveness as "untested," "still open," or "thin n" going
  forward — it has been tested repeatedly on a meaningful pooled sample,
  and the project record (ROADMAP.md, docs/enterprise.md §1/§7,
  docs/phase3-ab-results.md) says plainly that the test currently cuts
  against the thesis. The local-tier half of claim 3 (cheap-by-default
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
- **Local-model support is shelved, not abandoned (2026-06-25, direct
  operator instruction).** Most prospective deployments — consumer and
  enterprise alike — run frontier models (Claude/Gemini) only; further
  local-model engineering (LM Studio/Ollama testing, heterogeneous-worker
  validation) is deferred until the product has real usage to justify it,
  not pursued speculatively ahead of that. `LMStudioWorker`/`QwenWorker`
  stay in the tree and work (`--workers lmstudio`/`qwen`) — this is a
  prioritization call, not a removal. Do not read the recurring "no local
  model server reachable in this environment" notes elsewhere (Phase 0/3
  validation records) as an active blocking gap to go close; they're
  accurate background, not a to-do. Revisit once there's a viable product
  with users, per the operator's framing ("pivot to local once this is a
  viable product").
- **Productization direction (2026-06-25): companies adopting a multi-LLM
  setup are expected to care most about the memoric binary and a "quick
  connect" onboarding path for wiring up multiple models, not about local
  model support.** This reframes what "viable product" (above) should
  optimize for next, once the current engineering pass lands: the memoric
  binary's validated contention/track-record signal is the differentiator,
  and friction in adding/swapping LLM providers (today: editing CLI flags
  and env vars per `WORKER_FACTORIES` entry in `perdura.py`) is the
  adoption blocker to remove. **SHIPPED 2026-06-27 (v0.3.0):**
  `perdura_providers.py` is the "quick connect" mechanism this called
  for — see the Phase roadmap bullet above. The memoric-binary half of
  this priority (a more product-facing surface for the validated
  contention/track-record signal, beyond `perdura.py track`) remains
  unscoped.
