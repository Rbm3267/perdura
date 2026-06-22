---
name: memoric-conductor
description: Use for sessions with many small, heterogeneous subtasks where always using the same model wastes money, but classifying-and-routing each one needs to stay cheap. A local/cheap model (haiku) is the standing conductor for every subtask — it reduces the task to a compact record (domain, task type, confidence needed) instead of carrying full transcripts, then deterministic code (ledger.py, no LLM judgment) looks up the per-domain track record and remaining token budget to decide local vs frontier. Distinct from contention-router: routes by task-fit and historical outcome (supersession), not by disagreement between parallel takes.
---

# Memoric Conductor

A single persistent, append-only ledger ("the train") rides underneath the
session. A cheap model handles every subtask by default; a frontier model
is pulled in only where the domain's own history says cheap underperforms
there, or the task explicitly demands it — and the decision is made by
code, not by an LLM guessing whether it's qualified.

This is `perdura_memoric.py` (compact derived encoding: domain, type,
confidence, supersession — deliberately *not* the contention/edge fields)
and `perdura_router.py` (per-domain budget ledger, reliability-per-cost
selection) ported to single-session granularity. The routing signal here
is task-fit + outcome history, not contradiction — that's what makes this
a different skill from `contention-router`, not a variant of it.

## The train

`ledger.py` in this skill's directory maintains `train.jsonl` next to it —
one JSON object per line, append-only:

- a **task record**: `{id, ts, domain, type, confidence, tier, model, cost, semantic}`
- a **supersede event**: `{event: "supersede", target: <id>, ts}`

Never edit a line in place. If a later subtask redoes or overturns an
earlier one, append a supersede event for it — same supersede-never-delete
discipline as the Perdura graph, and it's what lets track record reflect
quality without needing a contradiction signal: a model whose work in a
domain keeps getting superseded has a worsening reliability score there,
independent of whether anything ever "disagreed" with it.

## Procedure (run this for each non-trivial subtask)

1. **Classify, don't transcribe.** As the standing local model, reduce the
   subtask to: a `--domain` tag (pick a short stable label you'll reuse
   across the session — `frontend`, `security`, `data-migration`, etc.,
   not a one-off description), a `--type` (`generate` / `review` /
   `debug` / `research` / whatever recurs in this session — consistency
   across calls matters more than the exact vocabulary), and a
   `--confidence` (0–1, how much margin for error this subtask tolerates).

2. **Ask the ledger, don't guess.** Run:
   ```
   python .claude/skills/memoric-conductor/ledger.py pick \
       --domain <domain> --type <type> --budget <remaining session budget>
   ```
   This is deterministic code — same invariant as Perdura's conductor
   ("no LLM judgment in the merge path"). It returns `tier: local` unless
   the domain+type's frontier track record beats local's by the escalation
   margin, or the task is brand new to a domain that's historically needed
   frontier. It also returns `reason` — surface this, don't suppress it.

3. **Do the work at the picked tier.**
   - `local` → handle it yourself (the standing haiku-tier model).
   - `frontier` → dispatch one `Agent` call with `model: "opus"` for just
     this subtask. Give it the subtask, not the whole session transcript —
     the train carries state forward as compact records, not context bloat.

4. **Append the outcome to the train:**
   ```
   python .claude/skills/memoric-conductor/ledger.py append \
       --domain <domain> --type <type> --confidence <c> \
       --tier <local|frontier> --model <name> --cost <tokens-or-$> \
       --text "<short task description, for the semantic fingerprint only>"
   ```
   `--text` is hashed (blake2b, 64-bit), never stored verbatim — the train
   stays small and never leaks task content into a side-channel file.

5. **If a later subtask overturns an earlier one** (you redo it, the user
   says it was wrong, a frontier pass corrects a local one), run:
   ```
   python .claude/skills/memoric-conductor/ledger.py supersede --target <id>
   ```
   using the `id` printed when that record was appended.

6. **Show the routing decision**, every time, briefly: which tier, why
   (`reason` from step 2), and running domain spend. Silent routing is the
   one thing this skill can't do — the whole point is that escalation is
   visible and earned by history, not vibes.

Run `python .claude/skills/memoric-conductor/ledger.py report` any time to
see the current per-(domain, type) track record across tiers.

## Guardrails

- Don't let the local model's classification (step 1) also make the
  escalate/stay decision — that's exactly the "LLM judgment in the merge
  path" Perdura's conductor invariant forbids. The classification feeds the
  ledger; the ledger decides.
- Don't reuse ad-hoc domain/type strings per call — track record only
  accumulates signal if the same subtask shape maps to the same
  `(domain, type)` key across calls in a session.
- Treat `train.jsonl` as session/repo state, not scratch — it's meant to
  accumulate across a session (or longer, if checked in) the same way the
  Perdura graph accumulates across turns. Don't delete it to "reset";
  append a supersede event instead if a record turns out wrong.

## Known limitation

Reliability here is Laplace-smoothed "fraction not later superseded" —
a real proxy, but a noisier one than Perdura's own claim-outcome tracking,
since nothing forces every subtask to ever get revisited (no revisit =
no supersede = looks reliable by default). Track record is only as good
as how consistently superseded entries get marked in step 5.
