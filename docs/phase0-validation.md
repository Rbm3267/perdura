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

## Verdict

| | |
|---|---|
| Mechanism (exp 1, synthetic) | **Validated** — scatter predicts contradiction before edges exist |
| Delta pipeline (real models) | **Validated** — 120/120 deltas parsed and merged, 0 rejected |
| Experiments on real data | **Blocked on protocol** — consensus collapse starves all three metrics |
| Track records (exp 2) | Not yet testable — zero outcome nodes produced |
| Routing equivalence (exp 3) | Synthetic near-miss (0.87); real arm 0.56 — confounded by exogenous contention |
| Exogeneity finding | **Adversarial contention can't validate hidden-disagreement detection** — organic contention (heterogeneous workers) required |
| Exogeneity fix | **Shipped (2026-06-25)** — boarding_mode on every node/edge; exp 1 and the inversion probe score organic+audit separately from adversarial. Infra only — doesn't retroactively change the 2026-06-11 numbers above |
| Track records (Phase 2) | **First real differentiation** — gemini 0.667 vs claude 0.400 (rubric calibration open) |
| Spec lock | **No** — re-run the real arm with the protocol fixes above |
| memoric_weight default | stays **0** (issue #11 gate not cleared) |
| Phase 1.5 | Proceeding — exp 1 is the signal Phase 1.5 consumes, and it holds |
