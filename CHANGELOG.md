# Changelog

All notable changes to Perdura are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [0.3.0] — 2026-06-27

"Quick connect": wiring up a new LLM provider or vendor is now a
config-file edit, not a `perdura.py` code change — the productization
priority recorded 2026-06-25.

### Added
- `perdura_providers.py` — loads named workers from a JSON (or YAML, if
  `pyyaml` is installed) config file, layered on top of the built-in
  `qwen`/`claude`/`gemini`/`lmstudio`/`mock` set. Each entry names a
  `protocol` perdura already speaks (`anthropic`, `google-genai`, `openai`
  for any OpenAI-compatible chat-completions endpoint, `lmstudio-native`,
  `mock`), so any OpenRouter/Together/Groq/Fireworks/self-hosted
  vLLM/Ollama vendor is a config entry; only a genuinely new wire format
  needs code.
- `--provider-config PATH` CLI flag (`perdura.py run`); auto-discovers
  `./perdura_providers.json` when omitted. A missing default path changes
  nothing; an explicitly-named missing/malformed path fails loudly.
- `registry_from_workers(workers, costs=None, tiers=None)` — optional
  cost/tier override params so a config-defined vendor escalates through
  the Phase 3 router (`--route`) like a real frontier worker instead of
  the free/local default.
- `QwenWorker` gains an `api_key` constructor param (default `"local"`),
  used by the `openai` protocol to pass a real key for hosted
  OpenAI-compatible vendors. API keys are always resolved from an
  environment variable named in a config entry's `api_key_env`, never
  written into the config file itself.
- `tests/test_providers.py` (config loading, per-protocol construction,
  the `.name` override, cost/tier extraction) and new `--provider-config`
  CLI coverage in `tests/test_cli.py`.

## [0.2.0] — 2026-06-25

Productization pass: the E1/E2 HTTP service gains the observability and
deployment primitives an operator needs to actually run it, and the
project's stance on local models is made explicit.

### Added
- `/ready` — readiness endpoint backed by a real store probe (`ping()` on
  `JSONFileStore`, `SQLiteStore`, `PostgresStore`); unauthenticated and
  exempt from rate limiting, distinct from `/health`'s always-200 liveness
  check.
- `/usage` — operator-only per-tenant metering endpoint (`perdura_service.py`):
  request counts, by-route and by-status breakdowns, bytes in/out, and
  accepted/rejected delta counts. In-memory and per-process — a foundation
  for billing, not billing-grade itself.
- Structured logging via the standard `logging` module (logger
  `perdura.service`), level set by `PERDURA_LOG_LEVEL` (default `INFO`);
  one structured line per request, raw HTTP log lines demoted to `DEBUG`.
- Fixed-window rate limiting, keyed by bearer token or client IP
  (`PERDURA_RATE_LIMIT_PER_MINUTE` / `--rate-limit-per-minute`, default
  off); violations return `429` with `Retry-After: 60`.
- `Dockerfile` (multi-stage, non-root) and `docker-compose.yml` (service +
  Postgres, wired to `/ready`) for the documented E1/E2 deployment paths.
- `tests/test_service_ops.py` (13 tests) and three new E2 tests in
  `tests/test_service_e2.py` covering readiness, logging, rate limiting,
  and per-tenant usage scoping.

### Decided
- Local-model support (LM Studio/Ollama) is shelved, not abandoned: most
  prospective deployments run frontier models (Claude/Gemini) only, so
  further local-model engineering work is deferred until the product has
  real usage to justify it. `LMStudioWorker` stays in the tree and works;
  it just isn't where near-term effort goes.

## [0.1.0] — 2026-06

Everything through Phase 3 and Enterprise E0–E3, prior to the
observability/deployment pass above. See `ROADMAP.md` for full narrative
detail; summarized by area:

### Core graph + workers (Phase 1)
- Persistent knowledge graph (questions/claims/evidence/decisions/rejected
  nodes; supports/contradicts/refines/answers/depends_on edges), JSON
  persistence, deterministic conductor (validate/merge, no LLM judgment on
  the merge path), supersede-never-delete.
- Claude, Gemini, and local Qwen workers, round-robin boarding, bounded
  2-hop briefings, attribution hidden from workers.
- CLI (`perdura.py`) and MCP station (`perdura_server.py`).

### Retrieval and visualization (Phase 1.5)
- Pluggable retrieval (`perdura_retrieval.py`; `--retriever graph|hybrid|chroma`).
- Force-directed mind-map viz (`perdura.py viz`, live at `/viz`), drawing
  `collision_candidates()` as dotted lines.
- `--memoric-briefings`: bounded collision-locator section in every
  worker's briefing, off by default, byte-identical output when unset.

### Memoric binary (Phase 0)
- 96-bit epistemic encoding (`perdura_memoric.py`), derived state only —
  never persisted into the graph.
- `boarding_mode` provenance (organic/adversarial/audit) on every
  `Node`/`Edge`, fixing an exogeneity confound in the validation
  experiments.
- First real-data pass of experiment 1 (AUC 0.944, simhash,
  organic+audit-filtered); the **inversion finding** (contradicting claims
  are lexically *closer* than random pairs, not farther) confirmed on
  organic contention.

### Track records and routing (Phases 2–3)
- Per-model, per-domain track records from claim outcomes (`perdura_track.py`,
  `perdura.py track`).
- The epistemic router (`perdura_router.py`, `--route`): registry, hard
  budgets, escalation by reliability/cost. Real 6-run A/B
  (`docs/phase3-ab-results.md`) found contention-triggered escalation never
  beat periodic escalation on outcome flips at equal cost — **default
  policy pivoted from `contention` to `periodic`**; `contention` remains a
  supported research arm.

### Enterprise (E0–E3)
- **E0/E1**: pluggable storage (JSON/SQLite/Postgres) behind one interface;
  authenticated single-tenant HTTP service (`perdura_service.py`) with
  worker/operator role separation.
- **E2**: multi-tenant Postgres store with row-level-security isolation,
  SSO (`perdura_sso.py`, JWT/JWKS) alongside static break-glass tokens, an
  admin role, and per-tenant per-domain budget config — shipped ahead of
  the Phase 3 A/B gate, by explicit operator decision (`docs/enterprise.md` §7).
- **E3**: ingestion adapters (`perdura_ingest.py`) for ADRs, incidents,
  tickets, and PR reviews, merging through the same conductor path as an
  LLM worker turn; a live GitHub PR connector (`perdura_connectors.py`).

## [Unreleased before 0.1.0]

Initial implementation, packaging metadata, and the original RFC
(`docs/memoric-binary.md`).
