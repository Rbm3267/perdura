---
name: contention-router
description: Use when a decision, verdict, or judgment call is risky enough to be worth double-checking, but not risky enough to justify always paying for the strongest model — ambiguous code-review verdicts ("is this safe to merge"), "which approach is better", architecture trade-offs, root-cause guesses, or any either/or call where being wrong costs more than a couple of extra cheap model calls. Runs 2-3 fast/cheap independent takes (haiku) first and escalates to a frontier model (opus) only when those takes actually disagree on the verdict, instead of always paying frontier cost or always trusting one fast pass. Single-task version of Perdura's contention-driven-economics thesis: spend rises only where the takes contradict each other.
---

# Contention Router

Cheap models by default; a frontier model is summoned only where independent
cheap takes disagree. The point isn't to save tokens on easy calls — it's to
spend the expensive model exactly where disagreement shows there's something
to adjudicate, and nowhere else.

## When to use this

A binary or short-list judgment call where:
- a wrong answer is costly enough to want a second opinion, and
- the question is well-specified enough that "agree" / "disagree" is
  meaningful (not open-ended brainstorming — this is for verdicts, not
  ideation).

Examples: "is this migration safe under concurrent writes", "should this PR
block the release", "which of these two approaches handles the edge case",
"is this log line the root cause or a red herring".

## Procedure

1. **Frame the question as a forced verdict.** Reduce the judgment call to a
   short list of mutually exclusive labels (e.g. `safe` / `unsafe`, `A` /
   `B`, `root-cause` / `not-root-cause`). If the question can't be reduced
   this way, this skill doesn't fit — answer directly instead.

2. **Fire 2-3 independent cheap takes in parallel.** Use the `Agent` tool
   with `model: "haiku"`, one call per take, in a single message so they run
   concurrently. Give each the same question and context, but do not let them
   see each other's reasoning — independence is what makes disagreement
   informative. Use 3 takes by default (breaks ties); 2 only when the cheap
   model is itself expensive enough that a third call isn't worth it.

3. **Require a one-line forced verdict from each take**, on its own line at
   the end of its response, e.g. `VERDICT: unsafe`. Don't compare full
   reasoning text for similarity — Perdura's own validation found that
   *contradicting* claims are lexically closer than random pairs (same
   topic, opposite stance), so textual similarity is the wrong signal here.
   Compare the extracted verdict labels directly.

4. **Compute contention from verdict agreement, not text.**
   - All takes agree → contention = 0.
   - 3 takes, 2-1 split → contention = low/medium.
   - 3 takes, no majority (3-way split) or 2 takes that disagree →
     contention = high.

5. **Route on contention:**
   - **Low (unanimous, or 2-of-3 majority on a low-stakes call):** return the
     majority verdict directly. State that it was a cheap-consensus answer
     and what the minority view (if any) was, in one line — don't hide that
     a dissent existed even when not escalating.
   - **High (genuine split):** escalate. Spawn one `Agent` call with
     `model: "opus"`, passing it the original question *and* the cheap
     takes' verdicts + one-line reasoning each, and ask it to adjudicate
     explicitly between the conflicting positions — not to re-derive the
     answer from scratch blind to the disagreement.

6. **Always show the routing decision**, even when terse. Minimum output:
   ```
   Cheap takes: A, A, B (2-1)
   Contention: medium → escalating
   Frontier verdict: A — [one-line reason it sided with the majority / overturned it]
   ```
   This is the whole point: the cost discipline has to be visible, not a
   silent implementation detail. If a user can't see that escalation only
   happened because of disagreement, they can't trust the cheap path on the
   calls that *didn't* escalate.

## Guardrails

- Never skip step 2 to save time on a call this skill was invoked for — the
  cheap-by-default behavior only pays off because escalation is conditional,
  not skipped.
- Don't escalate on unanimous agreement just because the stakes feel high —
  that defeats the thesis (it's exactly the "always pay frontier cost"
  baseline this is meant to beat). If the stakes are high enough that even
  unanimous cheap agreement isn't trustworthy, say so and recommend a direct
  frontier-only answer instead of using this skill.
- If the cheap takes' one-line verdicts don't cleanly reduce to the label
  set from step 1 (hedging, "it depends"), treat that as contention too —
  an unclassifiable take is a disagreement with the premise of the question,
  not an agreement by default.

## Known limitation (carried over from Perdura itself)

Verdict-agreement is a heuristic stand-in for genuine epistemic contention,
not a validated measure of it — Perdura's own decisive experiment (does
contention-triggered escalation beat periodic/random escalation at equal
cost?) hasn't been run against real heterogeneous models yet. Treat this
skill's escalation decisions as a reasonable default, not a proven policy.

## Possible extension (not built)

A per-question-type track record — log `{question_type, cheap_verdict,
frontier_verdict, escalated, outcome}` somewhere durable (e.g.
`.claude/skills/contention-router/track.jsonl`) and use historical
agreement rates to tune the escalation threshold per category instead of
the fixed majority rule above. Skipped for v1 because it needs outcome
ground truth (was the verdict actually right?) that isn't available at
decision time — exactly the attribution/track-record machinery
`perdura_track.py` builds for the knowledge-graph version of this idea.
