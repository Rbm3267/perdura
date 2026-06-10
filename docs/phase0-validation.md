# Phase 0 Validation — Synthetic Arm Results

**Date:** 2026-06-10 · **Arm:** synthetic ground truth (necessary condition)
**Graph:** `experiments/synthetic_session.py` (68 live nodes, 89 edges,
16 contradicts, 3 workers with planted reliability, seed 7)
**Status: PARTIAL — do not lock the spec yet.** The real-session arm must
run before Phase 0 closes (see "Running the real-session arm" below).

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

## Verdict

| | |
|---|---|
| Mechanism (exp 1) | **Validated** — scatter predicts contradiction before edges exist |
| Track records (exp 2) | **Not yet testable** — needs real workers and outcomes |
| Routing equivalence (exp 3) | **Close** — 0.87; re-test on real sessions before tuning |
| Spec lock | **No** — blocked on the real-session arm |
| Phase 1.5 start | **Unblocked** — exp 1 is the signal Phase 1.5 consumes, and it holds |
