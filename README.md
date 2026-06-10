# Perdura

**The mind that outlives its models.**

Perdura is a persistent knowledge graph that hires and fires LLMs based on
what it has learned about them — spending money only where it disagrees with
itself.

Conventional multi-agent systems treat agents as persistent and memory as
incidental. Perdura inverts this: **memory is the persistent entity; LLMs are
ephemeral workers** that board, contribute graph deltas, and disembark.
Because no working state ever lives inside a model, any model can be swapped
mid-task with zero loss. The mind survives every model that ever powered it.

> *Perdura* — from **perdurantism**, the philosophy that a thing persists as a
> series of temporal parts rather than one enduring object. Also literally
> "it endures" (Spanish / Italian / Portuguese).

## The three claims

1. **Inverted persistence.** The graph is the system of record. Models are
   stateless, commodity labor — swappable at any station because the state
   rides the train, not the worker.
2. **Per-model epistemic track records.** Every node is attributed. Over time
   the graph learns which models are reliable in which domains. The memory
   evaluates its workers.
3. **Contention-driven economics.** Cheap local models are the default labor
   force. Frontier and specialist models are summoned only where
   contradiction density rises in the graph.

## Quickstart

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...
export GEMINI_API_KEY=...
# local labor: LM Studio serving qwen3-14b on :1234 (default), Ollama also works

# Try the loop offline first (no API keys needed)
python perdura.py demo

# Seed the mind with its first question
python perdura.py new "What memory compaction policy best preserves epistemic history?"

# Run worker turns (round-robin: qwen -> claude -> gemini)
python perdura.py run --turns 6

# Inspect the graph and proto track records
python perdura.py show
```

All-local labor: `python perdura.py run --turns 10 --workers qwen`
Ollama instead of LM Studio: `--qwen-url http://localhost:11434/v1 --qwen-model qwen3:14b`

## How it works

- **Knowledge graph** — typed nodes (`question`, `claim`, `evidence`,
  `decision`, `rejected`) and typed edges (`supports`, `contradicts`,
  `refines`, `answers`, `depends_on`). Append-mostly: nodes are superseded,
  never deleted, preserving the temporal record of how the mind changed.
- **Workers** — any LLM behind a uniform interface. A worker sees a bounded
  briefing (the 2-hop neighborhood of the most-contended open question),
  never a transcript and never authorship of prior nodes, and returns a
  strict-JSON delta.
- **Conductor** — deterministic code. Validates deltas, stamps attribution,
  merges, and recomputes contention. No LLM judgment in the merge path.
- **Contention** — confidence-weighted `contradicts` edges per claim,
  optionally blended with memoric-binary embedding scatter
  (`--memoric-weight 0.5`; default stays edge-only until Phase 0 validation
  passes). It prioritizes which question gets worked next, and (Phase 3)
  decides when to escalate from local to frontier or specialist models.

Full design rationale: [docs/design.md](docs/design.md) ·
Visual overview: [docs/overview.html](docs/overview.html)

## Roadmap

| Phase | Scope | Status |
|---|---|---|
| 1 | Graph memory + delta extraction (Claude, Gemini, local Qwen), JSON persistence, round-robin boarding, CLI | ✅ built |
| 1.5 | Retrieval layer (ChromaDB, hybrid BM25 + dense + graph expansion); living mind-map visualization | planned |
| 2 | Attribution analytics: per-model, per-domain track records from node outcomes | planned |
| 3 | The epistemic router: registry, contention-driven escalation, cost budgets, specialist summoning | planned |

## Repository layout

```
perdura.py            Phase 1 implementation (graph, conductor, workers, CLI)
docs/design.md        Full design document (thesis, schema, requirements, risks)
docs/overview.html    Visual overview — architecture as a transit map
experiments/debate.py Precursor: the multi-model debate loop Perdura grew out of
```

## Status

Early-stage personal research. APIs, schema, and ideas will change.
