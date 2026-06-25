# Phase 3 Escalation A/B — Real-Worker Results

**Date:** 2026-06-25 · **Arms:** real frontier workers (Claude, Gemini) vs
scripted local tier (`--local mock`) · **Status: RUN — verdict thin but
consistent, and it cuts against the routing thesis as measured.**

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

## Findings

### 1. Targeting precision works exactly as designed

In every clean run, mean contention at the moment of escalation
(`cont@esc`) is highest for the contention arm — 2–7× periodic's, and
higher than random's in two of three runs:

| run | contention | periodic | random |
|---|---|---|---|
| 2 (n=1) | 0.400 | 0.062 | 0.267 |
| 3 (n=4) | 0.433 | 0.169 | 0.187 |
| 5 (n=4/4/7) | 0.355 | 0.082 | 0.147 |

The router's actual job — find the hottest live disagreement and send
frontier spend there — is not in question. It does that.

### 2. At equal cost, contention-routing produced fewer outcome flips, three runs in a row

| run | contention flips | periodic flips (equal cost) |
|---|---|---|
| 2, n=1 | 0 | 1 |
| 3, n=4 | 1 | 4 |
| 5, n=4 | 1 | 3 |

Same direction every time, two different Gemini-key configurations, one
Claude-only configuration. Taken at face value, that is mildly *against*
the thesis the router exists to prove, not for it. The samples are small
(1–4 escalations per arm per run), but the direction has not flipped once
across three independent real-worker runs.

### 3. The dilution confound: more budget did not give contention more chances

Contention's escalation count was **4 in both run 3 (budget=4) and run 5
(budget=12)** — 3× the budget did not produce 3× the escalations. This
seeded graph's contention ratio decays through simple dilution: every turn
adds nodes/edges, which shrinks the contention ratio whether or not
anything is being resolved. Once it drops under the 0.15 threshold, the
contention policy structurally cannot fire again on this graph — more
budget only helps if the signal itself stays live. Periodic and random
aren't gated this way, so they kept spending up to budget regardless. This
means simply re-running longer on this same seeded graph will not grow
contention's sample size; a bigger contention-arm sample needs either a
richer seed graph that keeps generating organic disagreement, or repeated
runs with fresh seeds/RNG draws.

### 4. An open alternate reading: "flips" may undercount what contention-routing actually does

In run 3, the contention arm finished with the *lowest* final contention
(0.089) of the three escalating arms (periodic 0.135, random 0.208) —
consistent with it spending its frontier calls *settling* the one hot
dispute rather than spawning fresh ones, which a raw flip count (new
contradicts/supersession edges) does not credit. Run 5 is more ambiguous
(contention 0.042 vs. random 0.040, periodic 0.076) — not a clean repeat
of the pattern. This is a real measurement question, not a rescue of the
headline number: it needs a metric that credits dispute *resolution*, not
just dispute *creation*, before this reading can be treated as more than
a hypothesis for the next run to test.

## What this run does not settle

- **The local tier was never real.** `--local mock` stood in for Qwen in
  every run. "Cheap-by-default is good enough most of the time" — the
  other half of claim 3 — has no real-worker evidence yet either way.
- **One seed graph, thin n.** All clean runs share the same seeded graph
  (one contested question, one calm one) and 1–4 escalations per arm.
  This is a real, consistent signal, not noise — but it is not a large-n
  result.
- **Gemini did almost all the real work.** Claude was only exercised in
  isolation (run 2, n=1) because the router's cost tiebreak (no track
  record yet → reliability ties → cheaper Gemini wins) selects Gemini by
  default whenever both are eligible.

## Verdict

| | |
|---|---|
| Targeting precision (cont@esc) | **Confirmed** — contention-routing finds the hottest live disagreement, every clean run |
| Outcome flips at equal cost | **Against the thesis, 3/3 clean runs** — contention-routing trails periodic every time |
| Dilution confound | **Real** — this seed graph caps contention's escalation count regardless of budget; longer runs don't grow its sample |
| Alternate "resolution not creation" reading | **Open** — supported once (run 3), ambiguous once (run 5); needs a dedicated metric, not assumed |
| Local-tier-is-good-enough (other half of claim 3) | **Untested** — `--local mock` throughout; needs a real cheap model |
| Gate for E2+ (`docs/enterprise.md` §7) | **Run, not passed as stated.** The required A/B has now executed for real; on the metric it specifies (outcome flips at equal cost), contention-routing did not win. The override that let E2/E3 ship ahead of this gate is no longer covering an untested claim — it is covering a claim that has been tested and, so far, has not supported the routing thesis. |

This narrows the open question to one specific, falsifiable mechanism
inside claim 3 (contention-driven escalation vs. scheduled/random
escalation at equal cost). It says nothing about claims 1 (inverted
persistence) or 2 (epistemic track records), and the merge/attribution
machinery ran perfectly across every run, real-key failures included — no
delta was ever lost or mis-merged because of the auth and rate-limit
errors above; the conductor logged them and continued. What it does mean:
the specific economic bet that justifies "spend only where it disagrees
with itself" now has real, consistent, if thin, evidence against it, and
the project record should say so plainly rather than continue treating it
as merely unverified.

## Reproducing

```bash
# clean two-frontier run (what produced runs 3 and 5 above)
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
