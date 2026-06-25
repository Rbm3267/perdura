# Phase 3 Escalation A/B — Real-Worker Results

**Date:** 2026-06-25 · **Arms:** real frontier workers (Claude, Gemini) vs
scripted local tier (`--local mock`) · **Status: RUN — verdict consistent
and decisive against the routing thesis as measured: 6/6 clean real-worker
runs, contention-routing has never once beaten periodic escalation on
outcome flips (5 losses, 1 tie).**

This is the decisive Phase 3 experiment CLAUDE.md's "Key decisions" and
`docs/enterprise.md` §7 have been flagging as open since E2: does
contention-triggered escalation put frontier spend where it changes
outcomes more than periodic or random escalation, at equal cost? It has
now actually run, multiple times, with real Claude and Gemini calls. This
document is the record.

## What was and wasn't real

`experiments/escalation_ab.py --real` runs the same four-arm protocol as
the synthetic positive control (`contention` / `periodic` / `random` /
`cheap`, same seeded graph, same metrics) but with real `ClaudeWorker` /
`GeminiWorker` instances on the escalation path instead of a scripted
challenger. **The local tier was still `--local mock`** — no Qwen or other
local model server was reachable in this sandbox (ports 1234/11434 both
unreachable), so every non-escalating turn was scripted filler, not a real
cheap-tier judgment. This run tests one half of the thesis cleanly (does
contention-routed *frontier* spend beat scheduled/random frontier spend at
equal cost) and says nothing about the other half (is local-by-default
actually good enough most of the time) — that needs a real local model,
not yet available in any environment this has been built in.

Router cost units: `claude=3.0`, `gemini=1.0`, `qwen=mock=0.0`
(`perdura_router.py DEFAULT_COSTS`). Escalation threshold `0.15`
(`DEFAULT_ESCALATE_AT`). With no track record at session start, reliability
ties default to the cheaper option, so **Gemini absorbed nearly every real
escalation**; Claude was only exercised by an explicit `--frontier claude`
run.

## Runs, in order

| # | Config | Real calls | Outcome |
|---|---|---|---|
| 1 | `--frontier claude,gemini`, turns=12/budget=4 | 12 Gemini attempts, **all 401** (wrong key type — OAuth token, not a Developer API key) | **Discarded.** Zero real deltas merged in any arm; flips=0 everywhere reflects nothing happening, not a result. |
| 2 | `--frontier claude` only, turns=12/budget=4 | 3 real Claude calls, 0 errors | Clean but n=1 escalation/arm (budget 4 ÷ cost 3, integer floor) — a single coin flip per arm, reported below for completeness only. |
| 3 | `--frontier claude,gemini`, turns=12/budget=4 | 12 real Gemini calls, 0 errors (Claude never selected — cost tiebreak) | **Clean, n=4 escalations/arm.** |
| 4 | `--frontier claude,gemini`, turns=36/budget=12 | 17 Gemini attempts, **11 failed HTTP 429** (free-tier quota) | **Discarded.** Over half the "real" calls silently failed; flip counts reflect rate-limit timing, not model behavior. |
| 5 | `--frontier claude,gemini`, turns=36/budget=12 (paid tier) | 15 real Gemini calls, 0 errors (Claude never selected) | **Clean, n=4 escalations for contention/periodic; n=7 for random** (random's own coin flips landed high this run — breaks cost parity against random, not against periodic). |
| 6 | `--frontier claude,gemini`, turns=12/budget=4 | 12 real Gemini calls, 0 errors (Claude never selected) | **Clean, n=4 escalations/arm.** |
| 7 | `--frontier claude,gemini`, turns=12/budget=4 | 12 real Gemini calls, 0 errors (Claude never selected) | **Clean, n=4 escalations/arm.** |
| 8 | `--frontier claude,gemini`, turns=12/budget=4 | 12 real Gemini calls, 0 errors (Claude never selected) | **Clean, n=4 escalations/arm.** |

### Results tables (clean runs only)

**Run 2 — Claude only, n=1 escalation/arm:**

| arm | spend | escal. | flips | cont@esc | final cont. |
|---|---|---|---|---|---|
| contention | 3 | 1 | 0 | 0.400 | 0.053 |
| periodic | 3 | 1 | 1 | 0.062 | 0.102 |
| random | 3 | 1 | 2 | 0.267 | 0.161 |
| cheap | 0 | 0 | 0 | 0.0 | 0.053 |

**Run 3 — Gemini, turns=12/budget=4, n=4 escalations/arm:**

| arm | spend | escal. | flips | cont@esc | final cont. |
|---|---|---|---|---|---|
| contention | 4 | 4 | 1 | 0.4333 | 0.089 |
| periodic | 4 | 4 | 4 | 0.1693 | 0.135 |
| random | 4 | 4 | 3 | 0.1872 | 0.208 |
| cheap | 0 | 0 | 0 | 0.0 | 0.053 |

**Run 5 — Gemini paid tier, turns=36/budget=12:**

| arm | spend | escal. | flips | cont@esc | final cont. |
|---|---|---|---|---|---|
| contention | 4 | 4 | 1 | 0.3553 | 0.042 |
| periodic | 4 | 4 | 3 | 0.0818 | 0.076 |
| random | 7 | 7 | 1 | 0.1469 | 0.040 |
| cheap | 0 | 0 | 0 | 0.0 | 0.021 |

(Random spent 7 vs. contention's 4 in run 5 — not an equal-cost
comparison. Periodic vs. contention *is* equal cost in every clean run.)

**Run 6 — Gemini, turns=12/budget=4, n=4 escalations/arm:**

| arm | spend | escal. | flips | cont@esc | final cont. |
|---|---|---|---|---|---|
| contention | 4 | 4 | 1 | 0.4048 | 0.108 |
| periodic | 4 | 4 | 8 | 0.2682 | 0.457 |
| random | 4 | 4 | 3 | 0.2905 | 0.213 |
| cheap | 0 | 0 | 0 | 0.0 | 0.053 |

**Run 7 — Gemini, turns=12/budget=4, n=4 escalations/arm:**

| arm | spend | escal. | flips | cont@esc | final cont. |
|---|---|---|---|---|---|
| contention | 4 | 4 | 1 | 0.2667 | 0.102 |
| periodic | 4 | 4 | 3 | 0.1445 | 0.220 |
| random | 4 | 4 | 2 | 0.2105 | 0.135 |
| cheap | 0 | 0 | 0 | 0.0 | 0.053 |

**Run 8 — Gemini, turns=12/budget=4, n=4 escalations/arm:**

| arm | spend | escal. | flips | cont@esc | final cont. |
|---|---|---|---|---|---|
| contention | 4 | 4 | 1 | 0.2667 | 0.110 |
| periodic | 4 | 4 | 1 | 0.1283 | 0.062 |
| random | 4 | 4 | 2 | 0.3438 | 0.129 |
| cheap | 0 | 0 | 0 | 0.0 | 0.053 |

(Run 8 is the first tie: contention and periodic both landed exactly 1
flip. Contention has still never *beaten* periodic in any clean run.)

### Pooled totals across all 6 clean runs (2, 3, 5, 6, 7, 8)

| arm | total spend | total escal. | total flips | flips/escalation | mean cont@esc (escalation-weighted) |
|---|---|---|---|---|---|
| contention | 23 | 21 | 5 | 0.238 | 0.348 |
| periodic | 23 | 21 | 20 | 0.952 | 0.154 |
| random | 26 | 24 | 13 | 0.542 | 0.226 |
| cheap | 0 | 0 | 0 | — | 0.0 |

Per-run direction (contention vs. periodic, equal cost): **contention
loses in 5 of 6 runs (2, 3, 5, 6, 7) and ties in 1 (8). It has not won
once.**

## Findings

### 1. Targeting precision works exactly as designed

In every clean run, mean contention at the moment of escalation
(`cont@esc`) is highest for the contention arm — and the pooled,
escalation-weighted mean across all 6 runs confirms it (0.348 vs. 0.154
periodic vs. 0.226 random):

| run | contention | periodic | random |
|---|---|---|---|
| 2 (n=1) | 0.400 | 0.062 | 0.267 |
| 3 (n=4) | 0.433 | 0.169 | 0.187 |
| 5 (n=4/4/7) | 0.355 | 0.082 | 0.147 |
| 6 (n=4) | 0.405 | 0.268 | 0.291 |
| 7 (n=4) | 0.267 | 0.145 | 0.211 |
| 8 (n=4) | 0.267 | 0.128 | 0.344 |
| **pooled (weighted)** | **0.348** | **0.154** | **0.226** |

Contention beats periodic in all 6 runs and beats random in 5 of 6 (random
edges it out only in run 8). The router's actual job — find the hottest
live disagreement and send frontier spend there — is not in question. It
does that, and the larger sample makes the margin clearer, not weaker.

### 2. At equal cost, contention-routing produced fewer outcome flips — 6 runs straight, never once ahead

| run | contention flips | periodic flips (equal cost) |
|---|---|---|
| 2, n=1 | 0 | 1 |
| 3, n=4 | 1 | 4 |
| 5, n=4 | 1 | 3 |
| 6, n=4 | 1 | 8 |
| 7, n=4 | 1 | 3 |
| 8, n=4 | 1 | 1 (tie) |
| **pooled** | **5 / 21 escalations** | **20 / 21 escalations** |

Same direction in 5 of 6 runs and a tie in the 6th — across two different
Gemini-key configurations and one Claude-only configuration. Pooled,
periodic produced 4× contention's flip rate per escalation (0.952 vs.
0.238 flips/escalation). The samples per run are still small (1–8
escalations), but contention-routing has not won a single one of 6
independent real-worker runs. At this point the direction is the finding,
not a coin flip that happened to land the same way three or six times.

### 3. The dilution confound: more budget did not give contention more chances

Contention's escalation count was **4 in run 3 (budget=4), run 5
(budget=12), and runs 6–8 (budget=4 each)** — 3× the budget in run 5 did
not produce 3× the escalations. This seeded graph's contention ratio
decays through simple dilution: every turn adds nodes/edges, which shrinks
the contention ratio whether or not anything is being resolved. Once it
drops under the 0.15 threshold, the contention policy structurally cannot
fire again on this graph — more budget only helps if the signal itself
stays live. Periodic and random aren't gated this way, so they kept
spending up to budget regardless. This means simply re-running longer on
this same seeded graph will not grow contention's sample size — confirmed
again by runs 6–8, which used the same turns=12/budget=4 config as run 3
and landed on the identical escalation count every time. A bigger
contention-arm sample needs either a richer seed graph that keeps
generating organic disagreement, or repeated independent runs (which is
what runs 6–8 did, and which is how the pooled n above grew from 1–4
escalations/arm/run to 21–24 escalations/arm pooled).

### 4. The alternate reading holds up, modestly, at the pooled level

Across all 6 runs, the contention arm's *mean final contention* (0.084)
is the lowest of the three escalating arms (periodic 0.175, random
0.148) — consistent with it spending its frontier calls *settling* the
hot dispute it targets rather than spawning fresh ones, which a raw flip
count (new contradicts/supersession edges) does not credit. This was
ambiguous on run 5 alone but now holds as a pooled pattern across the
larger sample. It is still not a clean rescue of the headline number: the
final-contention gap is also consistent with the dilution effect in
finding 3 (an arm that escalates "successfully" early and stops adding
contentious content has more turns left to dilute before the run ends) —
the two explanations aren't yet separable with the data this harness
produces. It remains a real measurement question that argues for a
dedicated resolution-vs-creation metric, not an assumption that the
headline flip-count result is somehow not real.

## What this run does not settle

- **The local tier was never real.** `--local mock` stood in for Qwen in
  every run. "Cheap-by-default is good enough most of the time" — the
  other half of claim 3 — has no real-worker evidence yet either way.
- **One seed graph.** All 6 clean runs share the same seeded graph (one
  contested question, one calm one). The pooled escalation count (21–24
  per arm) is no longer thin in the way 1–4 escalations/arm/run was, but
  it is still one seed topology, not a sweep across different graphs.
- **Gemini did almost all the real work.** Claude was only exercised in
  isolation (run 2, n=1) because the router's cost tiebreak (no track
  record yet → reliability ties → cheaper Gemini wins) selects Gemini by
  default whenever both are eligible.

## Verdict

| | |
|---|---|
| Targeting precision (cont@esc) | **Confirmed, strengthened by pooling** — contention beats periodic in 6/6 runs and random in 5/6; pooled weighted mean 0.348 vs. 0.154 vs. 0.226 |
| Outcome flips at equal cost | **Against the thesis, 6/6 clean runs** — contention-routing has never beaten periodic (5 losses, 1 tie); pooled 5 vs. 20 flips over 21 equal-cost escalations |
| Dilution confound | **Real, reconfirmed** — runs 6–8 repeat run 3's exact escalation count (4) on the same turns=12/budget=4 config; longer/bigger budget doesn't grow contention's per-run sample, only repeated independent runs do |
| Alternate "resolution not creation" reading | **Modestly supported at the pooled level** — contention arm's mean final contention (0.084) is the lowest of the three escalating arms across all 6 runs, but this isn't yet separable from the dilution confound; still needs a dedicated metric |
| Local-tier-is-good-enough (other half of claim 3) | **Untested** — `--local mock` throughout; needs a real cheap model |
| Gate for E2+ (`docs/enterprise.md` §7) | **Run, not passed as stated — now on a larger sample.** The required A/B has executed 6 times for real; on the metric it specifies (outcome flips at equal cost), contention-routing has not won once. The override that let E2/E3 ship ahead of this gate is no longer covering an untested claim — it is covering a claim that has been tested repeatedly and has not supported the routing thesis. |

This narrows the open question to one specific, falsifiable mechanism
inside claim 3 (contention-driven escalation vs. scheduled/random
escalation at equal cost). It says nothing about claims 1 (inverted
persistence) or 2 (epistemic track records), and the merge/attribution
machinery ran perfectly across every run, real-key failures included — no
delta was ever lost or mis-merged because of the auth and rate-limit
errors above; the conductor logged them and continued. What it does mean:
the specific economic bet that justifies "spend only where it disagrees
with itself" now has real, consistent evidence against it across 6
independent real-worker runs (21–24 escalations pooled per arm, not the
1–4/run of the first three), and the project record should say so plainly
rather than continue treating it as thin or merely unverified.

## Reproducing

```bash
# clean two-frontier run (what produced runs 3, 6, 7, 8 above — repeat for
# independent draws; the escalation schedule is deterministic per run but
# real model output is not, so each repetition is a fresh data point)
python experiments/escalation_ab.py --real --local mock --frontier claude,gemini
python experiments/escalation_ab.py --real --local mock --frontier claude,gemini --turns 36 --budget 12

# isolate Claude (avoids the cost-tiebreak always picking Gemini)
python experiments/escalation_ab.py --real --local mock --frontier claude --turns 12 --budget 4
```

Needs `ANTHROPIC_API_KEY` and/or `GEMINI_API_KEY` (paid tier for Gemini —
the free tier's 5 req/min, ~20/day cap corrupts any run past a handful of
escalations, as run 4 above shows). No Qwen/local model server has been
available in any environment this has run in, so `--local mock` remains
the only option for the non-escalating tier.
