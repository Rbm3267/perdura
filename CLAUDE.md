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
docs/design.md         Full design doc and rationale
docs/overview.html     Transit-map architecture visual
experiments/debate.py  Precursor multi-model debate loop

## Phase roadmap
Full detail in ROADMAP.md; docs/memoric-binary.md is the Phase 0 RFC.
- Phase 0   Memoric binary — synthetic arm done (docs/phase0-validation.md);
  real-session arm pending; spec NOT locked
- Phase 1   ✅ Graph + delta loop, Claude/Gemini/Qwen, round-robin, CLI + MCP station
- Phase 1.5 STARTED — pluggable retrieval (perdura_retrieval.py;
  --retriever graph|hybrid|chroma, graph = required baseline arm) and
  mind-map viz (perdura.py viz)
- Phase 2   Per-model track records from node outcomes
- Phase 3   Epistemic router (contention-driven escalation, cost budgets)

## Key decisions already made — do not re-litigate
- perdura_graph.json is gitignored; the mind's state stays local by default
- Workers never see authorship of prior nodes (counters anchoring)
- Schema validation is strict; fix malformed deltas in parse_delta, not schema
- Memoric binary is derived state — computed on demand (encode_node), never
  persisted into the graph file (no migrations, no staleness, spec stays free)
- contention() defaults to the 0.5/0.5 edge+memoric blend (flipped
  2026-06-10); --memoric-weight 0 reproduces the original edge-only metric
  and is the required baseline arm in Phase 0 experiments
