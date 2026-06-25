# Perdura Roadmap — Updated with Phase 0

## Overview

Perdura is a persistent knowledge graph where LLMs are ephemeral workers. The roadmap now includes Phase 0 — foundational research on **memoric binary**, a native epistemic encoding that enables early contention detection and per-model track records in vector space.

-----

## Phase 0: Memoric Binary (NOW)

**Validation status (2026-06-10):** synthetic arm — experiment 1 (hidden
disagreement, temporal replay) passes; exp 3 near-miss (0.87); exp 2 needs
real data. First real-session arm hit *consensus collapse* (two frontier
models agree, 1 contradicts edge in 18 turns); fixed by adversarial
boarding (`--adversarial-every N`), validated to lift contention 0→0.13 on
real models. The spec-locking run is heterogeneous workers + adversarial
boarding. Second real arm (contested seeds + adversarial): contention
supply solved (12 contradicts edges), but adversarial contradictions are
*exogenous* — unpredictable by scatter by construction — so exps 1/3 score
at chance on them. Lock requires organic contention from heterogeneous
workers (local Qwen). Spec not locked. Full results:
docs/phase0-validation.md.

**Goal:** Design, implement, and validate a 96-bit epistemic encoding that captures semantic content, node type, confidence, domain tags, temporal context, and supersession lineage.

**Deliverables:**

- `docs/memoric-binary.md` — formal RFC with spec, encoding/decoding, distance metrics
- `perdura_memoric.py` — encoder/decoder and distance metric implementations
- `experiments/memoric_eval.py` — three validation experiments

**Validation criteria:** All three experiments pass

1. Hidden disagreement detection: AUC > 0.7 (embedding scatter predicts contradicts edges)
1. Per-model track records: |correlation| > 0.6 (vector-space reliability matches merge stats)
1. Compression without loss: >90% routing agreement (memoric binary routes equivalently to edge-only metrics)

**Success path:** Lock the memoric binary spec, commit Phase 0 artifacts, then proceed to Phase 1.5.

**Risk:** If validation fails, revisit bit allocation, distance metrics, or domain encoding. Open question: the semantic-hash choice (Blake2b vs simhash — RFC open question 6) must be settled before the spec is locked.

-----

## Phase 1: Graph + Workers (COMPLETED)

**Status:** ✅ Built and verified

Graph memory + delta extraction with Claude, Gemini, and local Qwen.

- Knowledge graph with typed nodes and edges
- Round-robin worker boarding (CLI)
- JSON persistence
- Conductor: deterministic merge, attribution, contention recompute
- Per-worker merge ledger (seed of Phase 2 track records)

**Artifacts:**

- `perdura.py` — CLI implementation
- `perdura_server.py` — MCP station (any MCP client can board as a worker)
- `perdura_graph.json` — live graph state (local, gitignored)

-----

## Phase 1.5: Memoric Retrieval Layer (STARTED)

**Status (2026-06-17):** retrieval layer shipped behind a pluggable
`Retriever` interface (`perdura_retrieval.py`): `--retriever graph`
(default, byte-identical Phase 1 baseline), `hybrid` (BM25 + dense +
graph expansion), `chroma` (hybrid with a persistent ChromaDB index).
Mind-map visualization shipped (`perdura.py viz`) and upgraded to a live
local dashboard, the Station (`perdura.py ui`): real-time graph with
2s polling, questions ranked by contention, node inspector, conversation
feed, and track records. The mind map now draws `collision_candidates()`
as dotted lines (lexically-close, unlinked claim pairs from different
authors) and is servable live over HTTP (`GET /viz`, operator-only,
`perdura_service.py`), not just as a static file. Remaining: memoric
binary in briefings, learned dense embeddings, retrieval A/B on real
sessions (research question #1).

**Goal:** Integrate memoric binary into the graph storage and briefing assembly.

**Scope:**

- Memoric binary per node — **derived on demand** via `encode_node`, never
  persisted (decision: docs/memoric-binary.md §6.1; persistence only ever
  as a versioned cache if ChromaDB integration needs it)
- Assemble briefings with memoric binary included
- Contention blend `(1-w)*edge_signal + w*embedding_scatter` — implemented;
  **default w=0 (edge-only) until Phase 0 experiments 1+3 pass** (issue #11;
  the brief 2026-06-10 flip to 0.5 was reverted). Opt in with
  `--memoric-weight 0.5`; the flip to default-on is earned by validation
- Visualize graph as a force-directed mind map (connectivity + contention)
- ChromaDB hybrid retrieval (BM25 + dense embeddings + graph expansion)

**Deliverables:**

- Updated `perdura.py` with memoric binary computation
- Retrieval index integration
- Graph visualization (web or graphviz)
- Updated briefing format to include memoric binary

**Timeline:** 2–3 weeks post-Phase 0 validation

-----

## Phase 2: Track Records (STARTED)

**Status (2026-06-11):** engine shipped (`perdura_track.py`): per-model and
per-domain reliability as a Laplace-smoothed Beta mean over claim outcomes
(promoted +1, corroborated-by-another-worker +0.5, challenged -0.5,
superseded -1), derived on demand and never persisted. Surfaced via
`perdura.py track` and an operator-only MCP tool (workers never see
attribution). Validated against planted synthetic ground truth. Remaining:
accumulate real outcome data and calibrate the rubric weights.

**Goal:** Compute per-model, per-domain reliability from accumulated node outcomes.

**Scope:**

- For each model: which of its claims ended up `supported`, `contradicted`, `superseded`, or promoted to `decision`?
- Compute reliability score per model and per domain (using domain bitmap from memoric binary)
- Track embedding-space quality (nodes produced close to future decisions?)
- Expose track records in `perdura_show` output (operator mode only — workers never see attribution)

**Deliverables:**

- Track record computation engine
- Per-model/domain scorecard in graph view
- Track record queries in MCP server

**Timeline:** 3–4 weeks post-Phase 1.5

-----

## Phase 3: Epistemic Router (KERNEL SHIPPED — real A/B run, result against)

**Status (2026-06-17):** the routing kernel shipped (`perdura_router.py`,
`perdura.py run --route contention|periodic|random|cheap --budget N`):
model registry with per-boarding costs and tiers, contention-driven
escalation (frontier chosen by live track-record reliability per cost),
hard session budgets with local fallback, and a per-decision ledger. The
decisive A/B harness shipped too (`experiments/escalation_ab.py`) with a
synthetic positive control: at equal spend and equal flips, mean contention
at escalation separates the arms (0.48 contention-routed vs 0.31 random vs
0.14 periodic) — frontier spend lands on actual disagreement.

**Update (2026-06-25):** the `--real` mode has now actually run, multiple
times, against real Claude/Gemini calls (`--local mock` standing in for an
unavailable local model). Full record: `docs/phase3-ab-results.md`.
Targeting precision holds on real data (contention-routing finds the
hottest live disagreement every clean run, 2–7× periodic's mean contention
at escalation). But on the metric the thesis was supposed to win on —
outcome flips at equal cost — contention-routing trailed periodic in all
three clean real-worker runs (0 vs 1, 1 vs 4, 1 vs 3 flips). A real
confound was found (this seed graph's contention dilutes with graph size
regardless of budget, capping the contention arm's sample at ~4
escalations no matter how long the run goes), and a real open question
(raw flip-counting may not credit *resolving* a dispute, only creating new
ones — supported once, ambiguous once). Neither rescues the headline
number; both are honestly unresolved. The local-tier half of the thesis
("cheap-by-default is good enough most of the time") remains completely
untested — no real cheap model has been available in any environment this
has been built in. See `docs/phase3-ab-results.md` for the full data and
verdict table.

**Goal:** Implement cost-driven routing: escalate to frontier/specialist models only where contention is high.

**Scope:**

- Model registry with domain tags, cost, latency, accumulated track records
- Contention-driven boarding: high scatter = expensive model; low scatter = local model
- Cost budgets: per session, per question, per domain
- Specialist summoning: query requests can specify domain tags or required model types
- Fallback: if specialist unavailable, route to next-best in cost/reliability order

**Deliverables:**

- Router engine
- Model registry and registration protocol
- Cost tracking and budget enforcement
- Fallback policies

**Routing decision logic:**

```python
contention_score = 0.5 * edge_signal + 0.5 * embedding_scatter
cost_budget_remaining = ...
model_registry.sort_by(
    (contention_score * reliability[model] / cost[model]) DESC
)
selected_model = next available within budget
```

**Timeline:** 4–6 weeks post-Phase 2

-----

## Enterprise Track (parallel to the research phases)

Full plan: `docs/enterprise.md`. The worker protocol is already a service
boundary (delta plane / briefing plane / operator plane), so enterprise
deployment is an application layer over the research engine — gated so it
never displaces the research focus.

- **E0 — storage tier** ✅ (2026-06-12): pluggable persistence
  (`perdura_store.py`) selected by file extension — JSON file (default,
  byte-identical Phase 1) or SQLite WAL (transactional saves, concurrent
  readers, multi-process on one box; validated 60/60 merges under 4
  concurrent conductors). Plus `perdura.py redact <node-id>`: the
  operator-only GDPR escape hatch — text destroyed, structure/attribution/
  lineage preserved.
- **E1 — service API** ✅ (2026-06-13): `perdura_service.py` — the three
  planes over HTTP with bearer auth, worker/operator roles enforced at the
  boundary (attribution-hiding becomes access control: `/track` and the
  attributed `/graph` are operator-only). Read-only Conductor panel in the
  Station (model registry + live routing preview).
- **E2 — multi-tenant control plane** ✅ (2026-06-17, gate overridden):
  `PostgresStore` (graph-per-tenant, row-level security FORCEd and proven
  against a non-superuser role, advisory-lock writer serialization across
  hosts), `perdura_sso.py` (JWT bearer tokens verified against an IdP's
  JWKS, role + tenant as IdP-vouched claims), `perdura_service.py`
  `/graphs/{tenant_id}/...` routing with an `admin` role and a real
  `GET`/`PUT /graphs/{tenant_id}/config` for per-domain router budgets. The
  documented gate (Phase 3 escalation A/B must show contention-routing wins
  at equal cost) was **not** satisfied — only the synthetic positive
  control had run. The operator explicitly chose to override the gate and
  build E2 ahead of it; see `docs/enterprise.md` §1/§7 for the full record.
  **Update (2026-06-25):** the real A/B has since run
  (`docs/phase3-ab-results.md`) — three clean runs, all against the
  thesis on outcome flips at equal cost, though n is thin and one real
  confound (contention dilution) was found. The override is no longer
  covering an untested claim; it is covering a claim that has been tested
  and has not, so far, supported the routing thesis.
- **E3 — ingestion adapters** ✅ (2026-06-17, gate overridden):
  `perdura_ingest.py` — `adr_delta`/`incident_delta`/`ticket_delta`/
  `pr_review_delta` map a structured item (already-fetched, no live API
  calls baked in) to the strict-JSON delta schema; `ingest()` merges
  through the normal conductor path under the same write lock as an LLM
  worker turn — no privileged side door. `perdura.py ingest <file>
  --adapter {adr,incident,ticket,pr}` (CLI, single item or batch).
  Attribution `adapter:<source>` makes the collision detector
  (`--audit-every`) surface cross-stream contradictions (an ADR's context
  claim vs an incident's root-cause claim) for free, and gives each stream
  its own per-domain track record. The Phase 3 escalation A/B gate (above)
  was not satisfied when this was built either — the operator explicitly
  extended the E2 override to cover E3 too; see `docs/enterprise.md` §1/§7.
  The real A/B has since run (2026-06-25) — see the E2 entry above and
  `docs/phase3-ab-results.md`.
  Follow-up (same date): `perdura_connectors.py` adds a live GitHub PR
  connector (`perdura.py sync-github --repo owner/name`) feeding the same
  `pr_review_delta` adapter from the real API instead of a pre-fetched
  dict, with a cursor file for idempotent re-sync; the HTTP transport is
  injectable so its tests stay offline.

-----

## Phase 4: Multi-Model Collaborative Routing (Optional)

**Goal:** Enable workers to request specific collaborators for sub-questions.

**Scope:**

- Workers can call `perdura_escalate_to(domain_tags=["epistemology"], model_preference="claude")`
- Chains of delegation tracked in the graph
- Sub-question linkage: does this derive from a parent question?

**Timeline:** Beyond Phase 3 (if proven valuable)

-----

## How the Phases Build on Each Other

```
Phase 0: Memoric Binary
    ↓
  Validation experiments run
    ↓ (if passed)
    ↓
Phase 1.5: Embed memoric binary into graph + retrieval
    ↓
  Workers use briefings with memoric signals
    ↓
Phase 2: Compute track records from node outcomes
    ↓
  Now we know which models are reliable where
    ↓
Phase 3: Route based on contention + reliability + cost
    ↓
  Perdura is a functioning epistemic router
```

-----

## Key Research Questions Answered by Each Phase

|Phase|Question                                         |Answer Method                          |
|-----|-------------------------------------------------|---------------------------------------|
|0    |Does memoric binary encoding work?               |Three validation experiments           |
|1.5  |Can we detect disagreement before explicit edges?|Embedding scatter vs contradicts log   |
|2    |Which models are actually reliable?              |Per-model outcome distribution analysis|
|3    |Does contention-driven routing save money?       |Cost tracking + performance comparison |

-----

## Artifacts by Phase

```
Phase 0 (NOW)
├── docs/memoric-binary.md         (RFC)
├── perdura_memoric.py             (encoder/decoder)
└── experiments/memoric_eval.py    (validation)

Phase 1 (DONE)
├── perdura.py                     (CLI)
├── perdura_server.py              (MCP station)
└── perdura_graph.json             (state, local)

Phase 1.5 (PLANNED)
├── perdura.py (updated)           (graph + memoric integration)
├── visualization/                 (mind map web UI)
└── docs/

Phase 2 (PLANNED)
├── perdura.py (updated)           (track record computation)
├── track_records.json             (per-model scores)
└── analytics/

Phase 3 (PLANNED)
├── perdura_router.py              (routing engine)
├── model_registry.json            (model metadata)
└── cost_tracking.json
```

-----

## Success Metrics

By end of Phase 3, Perdura should exhibit:

1. **Inverted persistence claim:** A model can be swapped mid-session; graph state survives unharmed. ✓ (testable in Phase 1)
1. **Epistemic track records claim:** Per-model reliability measurable from embedding space and merge history. ✓ (testable in Phase 2)
1. **Contention-driven economics claim:** Cheap models handle low-contention subgraphs; expensive models see only where disagreement clusters. ✓ (measurable in Phase 3)

-----

## Next Steps

1. **This week:** Run Phase 0 validation experiments on a real Perdura session
1. **If all three pass:** Lock memoric binary spec (incl. semantic-hash choice), commit to repo
1. **Iterate:** Adjust distance metrics or bit allocations if experiments hint at improvements
1. **Phase 1.5 kickoff:** Integrate memoric binary into graph storage and briefing assembly
