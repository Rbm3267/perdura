# Perdura — guidance for Claude sessions

**The mind that outlives its models.** A persistent knowledge graph where LLMs
are ephemeral workers: models board, contribute strict-JSON graph deltas, and
disembark. The memory is the system of record, not the agents. The full design
doc is `docs/design.md` — read it before touching architecture; **do not change
the graph schema or design decisions in it without asking Bennett.**

## Thesis & the three claims

> A persistent knowledge graph that hires and fires LLMs based on what it has
> learned about them, spending money only where it disagrees with itself.

Each claim requires the one before it:

1. **Inverted persistence.** The graph is the system of record; models are
   interchangeable, stateless labor. A model can be swapped mid-task with zero
   loss of working state because no working state lives in the model.
2. **Per-model epistemic track records.** Every node/edge is attributed to the
   model that produced it. Over time the graph accumulates evidence about which
   models are reliable in which domains — the memory evaluates its workers.
3. **Contention-driven economics.** Routing runs on epistemic state, not just
   cost: cheap/local models are the default labor force; frontier models are
   summoned only when contradiction-edge density rises in a subgraph.

## Graph schema (do not change without asking)

- **Node types:** `question` (has `status: open/resolved`), `claim` (has
  `confidence: 0–1`), `evidence`, `decision`, `rejected`.
- **Node metadata:** `id`, `type`, `text`, `domain_tags[]`, `created_by`
  (model id), `confidence`, `created_at`, `superseded_by` (nullable).
- **Edge types:** `supports`, `contradicts` (drives contention), `refines`,
  `answers`, `depends_on`. Edges carry `created_by`/`created_at` attribution.
- **Contention metric:** for subgraph S, contradicts-edges per claim node,
  weighted by node confidence (`Graph.contention`). This is the routing signal.

## Conductor invariants

The conductor is deterministic code (`merge_delta`, `run_turns`); the only LLM
step is delta extraction by the boarding worker itself. Hold these:

- **Deterministic merge path.** Validation, ID assignment, attribution
  stamping, and merging happen in code. Never let an LLM mutate the graph
  directly.
- **Supersede, never delete.** Nodes get `superseded_by`; the temporal record
  is preserved. There is no delete operation — don't add one.
- **Bounded briefings.** Workers see a briefing (open question + 1–2 hop
  neighborhood, capped at `BRIEFING_CHAR_BUDGET`), never the transcript. Cost
  stays flat regardless of how many models have ever boarded.
- **Attribution hidden from workers.** Briefings show content and confidence,
  never which model wrote a node (anti-anchoring/sycophancy). Attribution
  lives only in the graph for Phase 2 analytics.
- **Strict schema validation.** Malformed deltas are rejected or repaired via
  retry (`parse_delta` + `REPAIR_PROMPT`); never loosen validation in
  `merge_delta` to accommodate a sloppy model. Rejection rates are themselves
  track-record data.

## Repo layout

- `perdura.py` — the whole Phase 1 system: `Graph` (JSON-file persistence),
  briefing builder, `parse_delta` (tolerant extraction, strict downstream
  validation), `merge_delta` (conductor), workers (claude/gemini/qwen/mock),
  CLI (`new` / `run` / `show` / `demo`).
- `docs/design.md` — authoritative design doc (v0.1). `docs/overview.html` —
  presentation version.
- `experiments/debate.py` — earlier multi-agent debate experiment (prior-art
  baseline, not part of the loop).
- `perdura_graph.json` — the live graph state. **Gitignored; keep it that way.**
- `pyproject.toml` — packaging metadata (name reserved for PyPI).

## Development

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python perdura.py demo          # offline loop test (mock worker), must pass
python perdura.py new "Some question?"    # seed a question
python perdura.py run --turns 2 --workers qwen   # local-only (needs Ollama + qwen3:14b)
python perdura.py show
```

- `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` come from the shell env — never
  hardcode, echo, or commit them. Qwen runs via Ollama's OpenAI-compatible
  endpoint at `http://localhost:11434/v1` (no real key).
- There is no test suite yet; `perdura.py demo` is the smoke test.

## Phase roadmap

- **Phase 1 (now):** graph memory + delta extraction (Claude/Gemini/local
  Qwen), JSON-file persistence, round-robin boarding, CLI.
- **Phase 1.5:** retrieval layer — embed nodes into ChromaDB; briefings
  assembled by hybrid search (BM25 + dense, forge-rag pattern) + graph
  expansion. Mind-map visualization (force graph, dark/cyan).
- **Phase 2:** attribution analytics — per-model, per-domain track records from
  how often a model's claims end up supported, contradicted, superseded, or
  promoted to decisions. (`show`'s merge stats are the seed.)
- **Phase 3:** the epistemic router — registry + contention-driven escalation +
  cost budgets + specialist summoning. Proves claims #2 and #3.
