# Phase 0 Validation Results

**Date:** 2026-06-10 · **Arms:** synthetic ground truth + real session
**Status: PARTIAL — spec stays unlocked.** The synthetic arm validates the
mechanism; the first real-session arm revealed a *protocol* gap that must
be fixed before the experiments can score real data (see "Real-session
arm" below).

## Why a synthetic arm

This environment has no model API access, so these runs use controlled
graphs with *planted* ground truth: contended questions (two lexically
distinct camps, asymmetric confidence, `contradicts` edges arriving only
after several claims exist) vs consensus questions (paraphrased agreement,
decisions promoting reliable workers' claims). A pass here means the
mechanism can detect what it was designed to detect — necessary, not
sufficient. Real multi-model sessions are the sufficient-condition test.

## Methodology corrections made during validation

Three of the draft experiments had structural defects that this run
surfaced and fixed (all in `experiments/memoric_eval.py`):

1. **Experiment 1 now replays time.** The draft measured final-state
   association (circular: scatter of a contended graph vs edges of a
   contended graph). It now replays each question's claims in arrival
   order and scores scatter at checkpoints *before any contradicts edge
   exists* against whether one arrives later — the RFC's actual claim.
2. **Experiment 2's reliability formula was degenerate.** A worker with
   zero bad outcomes got `bad_scatter = 0` → reliability clamped to 0, so
   the best and worst workers scored identically (correlation exactly 0.0).
   Also, superseded nodes carry the stale flag, and `epistemic_distance`
   returns 0 for stale nodes by design — distances to the bad-outcome set
   were always zero. Replaced with `d_bad / (d_good + d_bad)` against
   *global* outcome sets, with outcome nodes encoded as live.
3. **Experiment 3's top-3 overlap was tie-brittle.** Edge counts tie
   constantly; both metrics then pick an arbitrary 3 of the tied set and
   the overlap measures dict ordering. Replaced with pairwise order
   preservation: over every question pair the edge metric strictly orders,
   the fraction scatter orders the same way.

## Results

| Experiment | Criterion | blake2b | simhash | Verdict |
|---|---|---|---|---|
| 1 — Hidden disagreement (temporal replay) | AUC > 0.7 | **1.00** | **1.00** | ✅ PASS |
| 2 — Track records in vector space | \|r\| > 0.6 | 0.04 | −0.61 | ⚠️ **INCONCLUSIVE** |
| 3 — Compression / routing equivalence | > 0.9 order preservation | 0.872 | 0.830 | ❌ NEAR-MISS |

### Experiment 1 — PASS (with a caveat)

Scatter at pre-contradiction checkpoints perfectly separates questions
heading toward contradiction from consensus questions (contended mean
scatter ≈ 0.19/0.16 vs consensus ≈ 0.05/0.04; separation ratio ≈ 4.0×
blake2b, 4.5× simhash). The planted signal (confidence asymmetry +
lexically distinct camps) is deliberately clean, so AUC 1.0 reflects an
easy synthetic separation, not expected real-world performance. The
mechanism works; the real-session arm calibrates how well.

### Experiment 2 — INCONCLUSIVE, not a pass

The simhash arm technically clears the |r| > 0.6 bar, **and we decline
it**: the correlation is computed over 3 workers (3 data points), the
reliability spread is ~0.01, and the sign is negative. That is noise
clearing a bar, not signal. The corrected metric behaves sanely (no more
degenerate zeros), but per-worker distance-to-outcome discrimination needs
many more workers and organically produced outcomes than a synthetic
session provides. **Decision: experiment 2 requires the real-session arm
with ≥4 workers and several dozen outcome nodes.**

### Experiment 3 — near-miss worth understanding

0.87/0.83 order preservation means memoric-only contention reproduces the
edge metric's strict preferences ~85% of the time, with disagreements
concentrated among questions of *similar* contention — where ordering
matters least for routing. Two readings: (a) slightly tune distance
weights to close the gap, or (b) note that perfect agreement is not
actually the goal — the blend exists because scatter sees things edges
don't (experiment 1). Re-evaluate on real sessions before tuning weights
toward a metric we partially want to disagree with.

### Open question 6 (blake2b vs simhash) — still open

Both hashes pass experiment 1 on synthetic text; blake2b shows larger
absolute separation, simhash a larger ratio. Synthetic paraphrases can't
settle this — real model prose (where opposing claims share vocabulary)
is exactly the case simhash exists for. Keep both arms in the
real-session run.

## Running the real-session arm (local machine, keys + LM Studio)

```bash
# build a real graph: ~6 questions, 24+ turns, 3 workers
python perdura.py new "What memory compaction policy best preserves epistemic history?"
# ...add 5 more questions...
python perdura.py run --turns 24            # qwen, claude, gemini round-robin

# evaluate both semantic-hash arms
python experiments/memoric_eval.py --graph perdura_graph.json --semantic blake2b
python experiments/memoric_eval.py --graph perdura_graph.json --semantic simhash
# baseline routing arm for comparison
python perdura.py show --memoric-weight 0
```

## Real-session arm (2026-06-10): the consensus-collapse finding

**Session:** 6 seed questions, 18 turns, `claude-sonnet-4-6` +
`gemini-2.5-flash` round-robin. 47 nodes, 79 edges, **120 accepted /
0 rejected deltas** — the parse/validate pipeline held perfectly against
two real frontier models (two Gemini turns failed on provider 503s; the
conductor logged them and continued, losing nothing).

**All three experiments returned `insufficient_data` — and that is the
finding.** The metrics didn't fail; the session produced almost none of
the signal they measure:

| Signal the experiments need | What the session produced |
|---|---|
| contradicts edges | **1** (vs 27 claims, 79 edges) |
| decision / supersede outcomes | **0** of either |
| confidence spread | every claim between **0.72–0.95** — the "low" bucket is empty |
| ≥2 claims per question | workers spawned 14 *new* questions; most have 0–1 answering claims |

Two strong frontier models, prompted that "disagreement is valuable
signal," **converged anyway** — they refined, supported, and extended each
other (49 refines/supports/depends_on edges) and contradicted essentially
never. Self-reported confidence is uniformly high and therefore carries no
information (anchoring probe: 1/27 high-confidence claims ever challenged;
no low-confidence bucket exists to compare against — issue #6's concern is
real but in an unexpected direction: *confidence as workers report it is
nearly constant*).

**Interpretation.** This is a protocol gap, not an encoding failure — and
it is, ironically, evidence *for* the design's own thesis: contention-driven
economics requires heterogeneous labor. Homogeneous frontier workers
produce consensus, not contention. Protocol changes for the next session:

1. **Adversarial boarding** — ✅ shipped (`--adversarial-every N`). A
   deterministic devil's-advocate preamble makes the worker attack the
   strongest live claim instead of extending it. **Validated against real
   models:** 4 adversarial turns (Claude + Gemini) on the consensus graph
   produced 4 new contradicts edges (1 → 5) and lifted global contention
   from ~0 to 0.13 — the signal the experiments need, manufactured on
   demand.
2. **Heterogeneous workers** — add local Qwen (the design's default labor);
   small models disagreeing with frontier models is research question #3.
3. **Contested seeds** — seed opposing claims alongside questions, or pick
   questions with genuinely opposing schools of thought.
4. **Bound question spawning** — 6 seeds became 20 questions in 18 turns;
   claim depth per question must outpace frontier growth for any
   per-question metric to bind.

### Next real-session arm (the spec-locking run)

```bash
# heterogeneous labor + adversarial boarding + contested seeds
python perdura.py run --turns 30 --workers qwen,claude,gemini \
    --adversarial-every 3
python experiments/memoric_eval.py --graph perdura_graph.json --semantic blake2b
python experiments/memoric_eval.py --graph perdura_graph.json --semantic simhash
```

With contention now reachable, this run is what settles open question 6,
clears experiments 1+3 on real data, and gives experiment 2 its first real
outcome nodes — i.e. locks the spec and (per issue #11) earns the
memoric_weight flip to 0.5.

## Spec-locking attempt (2026-06-11): the exogeneity finding

**Session:** 6 *contested* seed questions, 24 turns, Claude + Gemini with
`--adversarial-every 3`. The protocol fix worked exactly as designed:
**12 contradicts edges** (vs 1), confidence spread widened (0.65–0.90),
128/128 deltas accepted. The eval also gained a traversal fix — real
workers build refinement chains (claim refines claim answers question),
so per-question claims are now collected by neighborhood expansion rather
than direct `answers` edges only (the flat-graph assumption was a
synthetic-generator artifact).

**Results on real data:** experiment 1 AUC 0.54 (blake2b) / 0.33
(simhash) over 31 replay checkpoints — chance level. Experiment 3 order
preservation 0.56. Experiment 2 still blocked (0 decision/supersede
outcomes in 24 turns).

**Why — and why this is a finding, not a failure:** adversarially
manufactured contradictions are **exogenous**. The critic attacks the
strongest claim wherever it boards, regardless of pre-existing divergence
— so by construction there is no latent scatter signal preceding those
edges. You cannot validate *hidden disagreement detection* against
disagreement that was injected by prompt. Adversarial boarding solves the
contention *supply* problem (and feeds Phase 2 real outcome signal — see
below) but is the wrong substrate for experiments 1 and 3, which need
**organic** contention: genuinely divergent priors between workers.

**What the session DID validate:**

- **Phase 2 track records produced their first real differentiation:**
  gemini 0.667 vs claude 0.400 — with the honest caveat that in a
  2-worker adversarial protocol, "challenged" partly measures who wrote
  the earlier/more-attacked claims; rubric calibration is open work.
- **The anchoring probe has real signal at last** (issue #6):
  high-confidence claims are challenged at 0.227 vs 0.333 for
  low-confidence — a +0.106 gap consistent with confidence-anchoring,
  on small n (25 claims). A `--mask-confidence` comparison run is now a
  meaningful experiment.
- The pipeline remains perfect: 248 accepted / 0 rejected deltas across
  both real sessions.

**Path to lock, sharpened:** the spec-locking session requires *organic*
contention — heterogeneous workers (local Qwen vs frontier models,
research question #3) on contested seeds, with adversarial turns reserved
for outcome generation, and experiment 1 scored only against
contradictions arising on non-adversarial turns. `memoric_weight` stays 0.

## Infra fix (2026-06-25): boarding_mode provenance

"Experiment 1 scored only against contradictions arising on non-adversarial
turns" above was a scoring requirement with no way to meet it: nothing on
disk recorded *how* a node or edge boarded, only the worker name and
timestamp — neither of which reliably separates protocols in a mixed-mode
run (organic turns interleaved with `--adversarial-every` critic turns and,
now, `--audit-every` stance-auditor turns).

`Node` and `Edge` (`perdura.py`) now carry a `boarding_mode` field —
`organic` (default), `adversarial`, or `audit` — stamped by the conductor
in `merge_delta` from the boarding path that produced the delta in
`run_turns`. The default keeps every pre-existing graph file loading
unchanged (old dicts simply lack the key; the dataclass default backfills
`organic`).

This closes the gap for the two consumers that needed the split:

- `experiments/memoric_eval.py` experiment 1 now scores `auc_replay`
  against contradicts edges tagged `organic` or `audit` only. The old
  unfiltered number is still reported, as `auc_replay_including_adversarial`,
  for comparison.
- `experiments/collision_probe.py` (the inversion-finding probe) now
  splits contradicting pairs into organic+audit vs adversarial and reports
  the per-metric AUCs and collision-band recall for each set separately,
  alongside the pooled `all` figure. The 2026-06-12 calibration session
  that produced the original collision-band numbers ran under
  `--adversarial-every`, so those numbers describe exogenous contradiction;
  whether the same band recalls *organic* disagreement was previously
  unmeasured by construction, not just unfiltered.

This is an infrastructure fix, not a new data point: graphs recorded before
this change have no `boarding_mode` on disk, so the organic/adversarial
split can't be recovered retroactively for the 2026-06-11 session above —
its headline numbers (AUC 0.54/0.33, order preservation 0.56) stand as
reported. What changes is what the *next* real-session run (organic,
heterogeneous-worker contention, per "Path to lock" above) will be able to
measure cleanly.

## Real-session arm (2026-06-25): experiment 1's first real pass

**Session:** 6 contested seed questions, 30 turns, Claude + Gemini, the
first run to combine `--adversarial-every 3` and `--audit-every 5` and the
first to exercise the `boarding_mode` infra fix above on fresh data. 58
live nodes, 126 edges, final contention 0.372, **178/178 deltas accepted, 0
rejected** across both workers. As in every prior real session, the two
frontier workers spawned sub-questions faster than they answered seeds: 30
turns only ever boarded against 2 of the 6 seeds (the 4-day-week and
nuclear-decarbonization topics); the other four seeds never got a single
claim. Bound question spawning (the 2026-06-10 finding's item 4) is still
unresolved.

| Experiment | Criterion | blake2b | simhash | Verdict |
|---|---|---|---|---|
| 1 — Hidden disagreement (organic+audit, real data) | AUC > 0.7 | 0.370 | **0.944** | ✅ **PASS (simhash only)** |
| 2 — Track records in vector space | \|r\| > 0.6 | n/a | n/a | insufficient_data (0 decisions/supersessions) |
| 3 — Compression / routing equivalence | > 0.9 order preservation | 0.764 | 0.868 | ❌ near-miss |

**Experiment 1 passes for the first time on real model data — and settles
open question 6 against blake2b.** Filtered to `organic`+`audit` contradicts
edges (61 of 66 replay checkpoints precede one), simhash scatter
discriminates almost perfectly (AUC 0.944; mean scatter 0.101 before a
contradiction vs 0.084 with none). blake2b scores 0.370 — *worse than
chance* — on the same checkpoints. This is consistent with blake2b being a
cryptographic hash with no locality preservation: two semantically close
claims hash to unrelated bit patterns, so any "scatter" computed from it is
noise by construction. simhash is locality-preserving by design (similar
text → similar hash), which is exactly what a disagreement-detection metric
needs. The honest caveat: the "no contradiction" class has only 5 of 66
checkpoints, so this AUC is on a small, imbalanced sample — encouraging,
not yet a large-n result.

**Experiment 3 narrows the gap but still misses.** simhash order
preservation reaches 0.868 (vs 0.56 in the 2026-06-11 run, and approaching
the 0.87 synthetic near-miss) — the top-3 contended-question ranking now
matches the edge-only baseline exactly. blake2b stays well behind (0.764).
Still short of the 0.9 bar; not enough to flip `memoric_weight`'s default.

**Experiment 2 is blocked for the third real session in a row** — this run
produced 0 `decision` nodes and 0 supersessions (the claim-outcome lineage
shows 0 promoted for both workers). This is now a pattern, not bad luck:
nothing in `run_turns`'s normal boarding protocol ever resolves a question
into a decision or marks a claim superseded — that requires either a
dedicated resolution boarding type or much longer runs that happen to
trigger supersession logic incidentally. More turns or more contention will
not unblock experiment 2 on their own.

**`collision_probe.py` reconfirms the inversion finding on `boarding_mode`-tagged
data for the first time** — 10 contradicting pairs (4 organic+audit, 6
adversarial) vs 425 non-contradicting pairs:

| metric | all | organic+audit | adversarial |
|---|---|---|---|
| semantic_blake2b | 0.520 | 0.688 | 0.408 |
| semantic_simhash | 0.270 | 0.395 | 0.187 |
| conf_delta | 0.608 | 0.388 | 0.754 |
| epistemic_simhash | 0.536 | 0.398 | 0.628 |

simhash distance is inverted for *both* boarding modes (<0.5: contradicting
pairs are closer than random), confirming the 2026-06-12 inversion finding
holds on genuinely organic disagreement, not just manufactured contention —
organic+audit's inversion (0.395) is real but milder than adversarial's
(0.187), where the devil's-advocate critic produces a near-paraphrase
counter-claim by construction. The collision band (Hamming distance 8–17,
the live threshold behind `--audit-every`) recalls 6/6 adversarial pairs
but only **2/4 organic** ones — the stance-auditor mechanism that exists
specifically to catch organic disagreement is currently missing half of it
in this run.

**What this run validates:** experiment 1 clears its bar on real data for
the first time, decisively in favor of simhash over blake2b — the clearest
evidence yet on open question 6. The inversion finding holds on organic,
not just adversarial, contention.

**What it doesn't:** spec lock requires 1 *and* 3, and experiment 3 is
still a near-miss. Experiment 2 needs a different intervention (an
explicit decision/supersession path), not just another run of this shape.
The heterogeneous-worker gap (no local model reachable in any environment
this has run in) remains completely untested — this is still two frontier
models, now with audit and adversarial boarding layered in, not the
local-vs-frontier divergence the original design calls for.

## Verdict

| | |
|---|---|
| Mechanism (exp 1, synthetic) | **Validated** — scatter predicts contradiction before edges exist |
| Delta pipeline (real models) | **Validated** — 426/426 deltas parsed and merged across three real sessions, 0 rejected |
| Hidden disagreement (exp 1, real data) | **PASS (2026-06-25, simhash only)** — AUC 0.944, organic+audit-filtered, on small/imbalanced n (5 of 66 checkpoints negative) |
| Open question 6 (blake2b vs simhash) | **Settled toward simhash on real data** — blake2b AUC 0.370 (worse than chance) on the same checkpoints; not locality-preserving |
| Track records (exp 2) | **Blocked three real sessions running** — 0 decision/supersession outcomes each time; needs a dedicated resolution path, not more turns |
| Routing equivalence (exp 3) | Synthetic near-miss (0.87); real arm narrowed 0.56 → **0.868 (2026-06-25, simhash)** — still short of 0.9 |
| Exogeneity finding | **Adversarial contention can't validate hidden-disagreement detection** — organic contention (heterogeneous workers) required |
| Exogeneity fix | **Shipped (2026-06-25)** — boarding_mode on every node/edge; exp 1 and the inversion probe score organic+audit separately from adversarial |
| Inversion finding | **Reconfirmed on organic data (2026-06-25)** — simhash distance inverted for both organic+audit (0.395) and adversarial (0.187) contradicting pairs; collision-band recall 2/4 organic, 6/6 adversarial |
| Track records (Phase 2) | **First real differentiation** — gemini 0.667 vs claude 0.400 (rubric calibration open) |
| Spec lock | **No** — exp 1 now passes (simhash); exp 3 still a near-miss; exp 2 structurally blocked |
| memoric_weight default | stays **0** (issue #11 gate not cleared — needs exp 1 *and* 3) |
| Heterogeneous workers | **Still untested** — no local model reachable in any environment this has run in; all three real sessions are frontier-only (Claude + Gemini) |
| Phase 1.5 | Proceeding — exp 1 is the signal Phase 1.5 consumes, and it now holds on real data too |
