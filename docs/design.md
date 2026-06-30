# Perdura — Design Document
**Tagline:** The mind that outlives its models.
**Name:** From *perdurantism* — the philosophy that a thing persists as a series
of temporal parts rather than one enduring object. Also literally "it endures"
(Spanish/Italian/Portuguese). Namespace verified clean on PyPI, npm, and GitHub
as of 2026-06-09.
**Author:** Bennett · **Date:** 2026-06-09 · **Status:** Draft v0.1

---

## 1. Thesis

> A persistent knowledge graph that outlives every model that works it,
> building an attributed, per-model track record of which LLMs to trust —
> in which domains — as it goes.

Conventional multi-agent systems treat agents as persistent and memory as
incidental. Perdura inverts this: **memory is the persistent entity; LLMs are
ephemeral workers** that board, contribute graph deltas, and disembark. The
"mind" survives every model that ever powered it.

## 2. Novel claims

These are the three claims this project exists to prove. Each layer requires
the one before it.

1. **Inverted persistence.** The knowledge graph is the system of record.
   Models are interchangeable, stateless labor. A model can be swapped
   mid-task with zero loss of working state because no working state lives in
   the model.
2. **Per-model epistemic track records.** Every node and edge is attributed to
   the model that produced it, with a confidence score. Over time the graph
   accumulates evidence about which models are reliable in which domains —
   the memory evaluates its workers.
3. **Contention-driven economics.** Routing decisions are made on *epistemic
   state*, not just cost. Cheap/local models are the default labor force;
   expensive frontier models are summoned only when contradiction-edge
   density rises in a subgraph or a high-stakes node is contested. Spend
   money where the graph disagrees with itself.

*Status note (2026-06-25): claim 3's escalation **trigger** has been revised
by data, not reversed. The real 6-run A/B (docs/phase3-ab-results.md) found
contention-triggered escalation never beats periodic escalation on outcome
flips at equal cost (5 losses, 1 tie). Cheap-by-default and
frontier-summoned-to-bound-spend both still stand; what changed is which
signal decides *when* to summon — the shipped router's recommended default
is now a periodic cadence (`perdura_router.py`), with contention-triggered
escalation kept as a fully supported research arm (its targeting precision —
finding the hottest live disagreement — is confirmed; its economic payoff is
not). See CLAUDE.md's claim 3 and docs/phase3-ab-results.md for the full
record.*

## 3. Prior art positioning (what this is *not*)

- **Blackboard systems (classic AI):** shared workspace, but workers were
  hand-coded specialists, not commodity LLMs with learned track records.
- **MemGPT / Letta, Zep/Graphiti:** give *an agent* long-term memory. Perdura
  has no privileged agent; the memory itself is the agent-like entity.
- **Multi-agent debate (Du et al.):** improves answers via critique rounds, but
  agents are fixed for the session and memory is the transcript.
- **LLM routers (RouteLLM, Martian):** route per-query on cost/quality
  predictions. Perdura routes per-*subgraph* on live epistemic signals
  (contention, confidence, attribution history).

*Survey updated 2026-06-10 — closest new neighbors since the draft:*

- **Blackboard-MAS revival ([arXiv:2507.01701](https://arxiv.org/abs/2507.01701),
  [arXiv:2510.01285](https://arxiv.org/abs/2510.01285)):** stateless LLM agents
  around a shared workspace, beating master-slave orchestration by 13–57%.
  Structurally the closest neighbor — but the workspace is untyped, nothing is
  attributed, and there are no track records or contention economics.
- **Memory-augmented routing ([arXiv:2603.23013](https://arxiv.org/abs/2603.23013)):**
  "knowledge access beats model size" — an 8B model with memory recovers 69% of
  a 235B model at 4% of cost. Flat conversational memory, though, and routing
  decisions are unchanged by it. In Perdura the routing signal *is* the
  memory's epistemic state.
- **Cascade / self-consistency escalation (production routers, surveyed in
  [arXiv:2603.04445](https://arxiv.org/html/2603.04445v2)):** escalate when
  samples of one model disagree on one query — per-query, ephemeral, then
  forgotten. Perdura escalates on disagreement accumulated in the persistent
  artifact, across models and over time.

Borrowed infrastructure is a feature, not a compromise: graph storage,
embeddings, and hybrid retrieval are solved problems (ChromaDB, BM25+dense —
same stack as forge-rag).

## 4. Architecture

```
┌─────────────────────────────────────────────────────┐
│                    THE TRAIN (persistent)            │
│                                                      │
│   Knowledge Graph ──── Retrieval Index (Phase 1.5)   │
│        │                                             │
│   Conductor (deterministic code + thin LLM step)     │
│        │                                             │
│   Router / Registry (Phase 3)                        │
└────────┬────────────────┬────────────────┬──────────┘
         │ board          │ board          │ board
     ┌───┴───┐        ┌───┴────┐       ┌───┴───┐
     │ Qwen  │        │ Claude │       │Gemini │   ← STATIONS (ephemeral)
     └───────┘        └────────┘       └───────┘
```

- **Knowledge graph** — typed nodes and edges (schema below). The living
  mind map. Append-mostly; nodes are superseded, not deleted, preserving the
  temporal record.
- **Conductor** — deterministic code that manages boarding/disembarking,
  generates briefings (bounded subgraph extracts), validates and merges
  deltas, and maintains contention metrics. The only LLM involvement is the
  delta-extraction step performed by the boarding worker itself.
- **Workers** — any LLM behind a uniform interface. Receive a briefing, return
  a JSON delta. No transcript access; the briefing stays bounded regardless of
  how many models have ever boarded.
- **Router (Phase 3)** — model registry with domain tags, cost, latency, and
  accumulated track record. Matches open questions to workers; escalates on
  contention.

## 5. Graph schema

### Node types
| Type | Meaning |
|---|---|
| `question` | An open question driving work. Has `status: open/resolved` |
| `claim` | A position/assertion. Has `confidence: 0–1` |
| `evidence` | Supporting material, source-attributed |
| `decision` | A resolved choice, links to the claims that won |
| `rejected` | A path abandoned, with the reason (prevents re-litigating) |

### Common node metadata
`id`, `type`, `text`, `domain_tags[]`, `created_by` (model id),
`confidence`, `created_at`, `superseded_by` (nullable)

### Edge types
| Type | Meaning |
|---|---|
| `supports` | claim/evidence → claim |
| `contradicts` | claim ↔ claim (drives contention metrics) |
| `refines` | claim → claim (sharper version) |
| `answers` | claim → question |
| `depends_on` | claim/decision → claim/decision |

### Contention metric (the routing signal)
For any subgraph S:

```
edge_signal(S) = contradicts_edges(S) / claim_nodes(S), confidence-weighted
contention(S)  = (1 - w) * edge_signal(S) + w * embedding_scatter(S)
```

where `embedding_scatter` is the mean pairwise epistemic distance of S's
claims in memoric binary (docs/memoric-binary.md §4.3). **The default `w`
tracks Phase 0 status:** `w = 0` (edge-only) until experiments 1 and 3 pass
(docs/phase0-validation.md), then 0.5. Opt in early with
`--memoric-weight 0.5`. Rising contention → escalate to a stronger or
domain-specialist model.

## 6. Worker lifecycle ("stations")

1. **Board** — conductor selects open questions (Phase 1: round-robin;
   Phase 3: routed) and assembles a briefing: the open question(s) plus the
   1–2 hop neighborhood of relevant nodes, capped at a token budget.
2. **Contribute** — worker returns a strict-JSON delta: new nodes, new edges,
   confidence updates, and optionally `supersedes` references. No prose.
3. **Validate & merge** — conductor schema-checks the delta, assigns IDs,
   stamps attribution, merges, recomputes contention.
4. **Disembark** — worker is released. Nothing it "knew" is lost because it
   never knew anything the graph didn't.

Swap triggers (Phase 3): cost/usage budgets, domain-tag match to a specialist
model, stagnation (no new edges for k turns), or contention escalation.

## 7. Phase plan

- **Phase 1 (now):** Graph memory + delta extraction with Claude, Gemini, and
  local Qwen. JSON-file persistence. Round-robin boarding. CLI. Proves the
  inverted-persistence loop works end to end.
- **Phase 1.5:** Retrieval layer — embed nodes into ChromaDB (forge-rag
  pattern), briefings assembled by hybrid search + graph expansion instead of
  tag matching. Mind-map visualization (likely MapLibre-style force graph in
  Tech-Editorial Futurism dark/cyan).
- **Phase 2:** Attribution analytics — per-model, per-domain track records
  computed from how often a model's claims end up supported, contradicted,
  superseded, or promoted to decisions.
- **Phase 3:** The router — registry + contention-driven escalation +
  cost/usage budgets + specialist summoning. This is where claims #2 and #3
  get proven with real accumulated data.

## 8. Open research questions

1. Does briefing-only context (no transcript) degrade contribution quality vs.
   full-history debate, and at what graph size does it win?
2. What contention threshold actually predicts "a frontier model will change
   the outcome here"? (Measure: how often escalation flips a consensus.)
3. Do small local models contribute net-positive deltas or mostly noise the
   conductor must filter? (Per-model precision of merged-and-surviving nodes.)
4. Who compacts best — should superseding/merging of stale claims be
   deterministic, done by the departing worker, or by a dedicated model?

## 9. Risks

- **Delta-extraction reliability:** small models may emit malformed JSON or
  low-quality graph structure. Mitigation: strict schema validation, retry
  with repair prompt, and per-model rejection-rate tracking (which itself
  feeds claim #2).
- **Graph sprawl:** without compaction the briefing budget erodes. Mitigation:
  supersede semantics from day one; compaction policy is research question #4.
- **Anchoring/sycophancy:** workers may defer to high-confidence existing
  nodes. Mitigation: attribution is hidden from workers (they see content and
  confidence, never which model wrote it).
