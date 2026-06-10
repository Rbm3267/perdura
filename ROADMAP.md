# Perdura Roadmap — Updated with Phase 0

## Overview

Perdura is a persistent knowledge graph where LLMs are ephemeral workers. The roadmap now includes Phase 0 — foundational research on **memoric binary**, a native epistemic encoding that enables early contention detection and per-model track records in vector space.

-----

## Phase 0: Memoric Binary (NOW)

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

## Phase 1.5: Memoric Retrieval Layer

**Goal:** Integrate memoric binary into the graph storage and briefing assembly.

**Scope:**

- Memoric binary per node — **derived on demand** via `encode_node`, never
  persisted (decision: docs/memoric-binary.md §6.1; persistence only ever
  as a versioned cache if ChromaDB integration needs it)
- Assemble briefings with memoric binary included
- Contention blend `(1-w)*edge_signal + w*embedding_scatter` — implemented;
  **default flipped to w=0.5 on 2026-06-10** (ahead of Phase 0 validation,
  by decision). `--memoric-weight 0` remains the edge-only baseline for
  experiments; if validation fails, flipping back is the same one-liner
- Visualize graph as a force-directed mind map (connectivity + contention)
- ChromaDB hybrid retrieval (BM25 + dense embeddings + graph expansion)

**Deliverables:**

- Updated `perdura.py` with memoric binary computation
- Retrieval index integration
- Graph visualization (web or graphviz)
- Updated briefing format to include memoric binary

**Timeline:** 2–3 weeks post-Phase 0 validation

-----

## Phase 2: Track Records

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

## Phase 3: Epistemic Router

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
