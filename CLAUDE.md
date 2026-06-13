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
perdura_store.py       Pluggable persistence (JSON default / SQLite by extension)
perdura_service.py     Authenticated HTTP service — three planes, bearer auth (E1)
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
  mind-map viz (perdura.py viz)
- Phase 2   STARTED — track-record engine (perdura_track.py, perdura.py
  track, operator-only MCP tool); reliability = Laplace-smoothed claim
  outcomes (promoted/corroborated vs challenged/superseded), derived
  on demand like memoric binary
- Phase 3   KERNEL SHIPPED — perdura_router.py (--route, hard budgets,
  escalation by track-record reliability/cost) + escalation_ab.py harness
  (synthetic positive control passes; thesis verdict needs real workers)

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
- Storage is pluggable by file extension (perdura_store.py): JSON file is
  the default and stays byte-identical; .db/.sqlite[3] selects SQLite WAL.
  Same advisory lock + reload-merge-save discipline for every store
- `redact` is the one sanctioned exception to supersede-never-delete:
  operator-only, destroys node TEXT only (structure/attribution/lineage
  survive), logged — the GDPR escape hatch (docs/enterprise.md §5)
