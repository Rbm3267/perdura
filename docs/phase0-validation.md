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

1. **Heterogeneous workers** — add local Qwen (the design's default labor);
   small models disagreeing with frontier models is research question #3.
2. **Adversarial boarding** — a deterministic devil's-advocate prompt
   variant for every Nth turn ("find the weakest live claim and attack it").
3. **Contested seeds** — seed opposing claims alongside questions, or pick
   questions with genuinely opposing schools of thought.
4. **Bound question spawning** — 6 seeds became 20 questions in 18 turns;
   claim depth per question must outpace frontier growth for any
   per-question metric to bind.

## Verdict

| | |
|---|---|
| Mechanism (exp 1, synthetic) | **Validated** — scatter predicts contradiction before edges exist |
| Delta pipeline (real models) | **Validated** — 120/120 deltas parsed and merged, 0 rejected |
| Experiments on real data | **Blocked on protocol** — consensus collapse starves all three metrics |
| Track records (exp 2) | Not yet testable — zero outcome nodes produced |
| Routing equivalence (exp 3) | Synthetic near-miss (0.87); real arm had no contention to rank |
| Spec lock | **No** — re-run the real arm with the protocol fixes above |
| memoric_weight default | stays **0** (issue #11 gate not cleared) |
| Phase 1.5 | Proceeding — exp 1 is the signal Phase 1.5 consumes, and it holds |
