# Memoric Binary — Perdura's Native Epistemic Encoding

**RFC Status:** Phase 0 (validation required before Phase 1.5 retrieval layer)
**Date:** 2026-06-10
**Implementation:** `perdura_memoric.py` · **Validation:** `experiments/memoric_eval.py`

## Abstract

Memoric binary is a compact bit-packed encoding that captures not just semantic content but the *epistemic context* of a knowledge graph node: its type, confidence, domain, temporal position, and supersession lineage. Unlike generic embeddings (nomic-embed-text, etc.), memoric binary is tailored to Perdura's specific job: detecting hidden disagreement, enabling per-model track records in vector space, and compressing the graph without losing routing quality.

## 1. Design Rationale

**Generic embeddings are semantically lossy for Perdura's use case.** Two claims with opposite meanings might embed nearby if they use similar language. A claim with 0.3 confidence and one with 0.8 confidence embed identically if the text is the same. A question claim and an evidence claim embed the same even though they serve different epistemic roles.

Memoric binary solves this by encoding the *context of assertion*, not just the assertion itself. Every bit carries provenance.

## 2. Specification

Memoric binary is a **96-bit encoding** (12 bytes). It is composed of five fields, packed in this order:

### 2.1 Semantic field (48 bits)

The first 48 bits are a **semantic hash** of the node's text. This is not a full embedding — it's a fast, lossy, dimensionality-reduced sketch:

```
semantic_hash = hash_text_to_48bits(node.text)
  method: Blake2b(text)[:6] (first 6 bytes of Blake2b output)
  property: deterministic, fast, captures text content
```

Why 48 bits? Enough to detect near-duplicates and text similarity via bitwise distance (Hamming), compact enough to keep the full encoding small.

> **⚠ Known limitation (see Open Question 6):** Blake2b is a cryptographic
> hash with the avalanche property — any two *distinct* texts differ in
> ~24/48 bits regardless of how similar they are. As specified, this field
> detects exact duplicates only; it does not give graded text similarity.
> `perdura_memoric.simhash_48bits` (a locality-sensitive sketch over
> character 3-grams, same width) is implemented as a candidate replacement,
> selectable via `encode(..., semantic_fn=simhash_48bits)`. Validation
> should be run with both before the spec is locked.

### 2.2 Type field (4 bits)

Encodes the node type. Each type has distinct epistemic weight:

```
0000 = question    (driving work, no confidence value usually)
0001 = claim       (assertion, confidence-scored)
0010 = evidence    (supporting material, sourced)
0011 = decision    (resolved choice, highest epistemic weight)
0100 = rejected    (abandoned path, includes reason)
1111 = reserved    (future use)
```

### 2.3 Confidence field (5 bits)

Quantized confidence score (0.0–1.0 mapped to 0–31):

```
conf_bits = int(node.confidence * 31)  # 0–31 (5 bits)
  0–10   = low confidence (0.0–0.32)
  11–20  = medium (0.35–0.65)
  21–31  = high (0.68–1.0)
```

Questions have a fixed confidence_bits = 31 (assumed certainty of the question itself).

### 2.4 Domain field (6 bits)

A **domain bitmap** — each bit represents a domain tag. 6 bits = 6 first-class domains:

```
bit 0 = "architecture"
bit 1 = "epistemology"
bit 2 = "economics"
bit 3 = "engineering"
bit 4 = "design"
bit 5 = "research"

domain_bits = 0
for tag in node.domain_tags:
    if tag in DOMAIN_MAP:
        domain_bits |= (1 << DOMAIN_MAP[tag])
```

Allows fast filtering: find all economics + engineering claims with `domain_bits & 0b001100`.

### 2.5 Provenance field (33 bits)

The most interesting part: encodes *temporal and supersession context*.

**Temporal (16 bits):**

- Quantized node creation time relative to graph birth
- `time_bits = (created_at - graph_start) // TIME_RESOLUTION`
- TIME_RESOLUTION = 3600 (1 hour granularity)
- Allows ~7.5 years of history in 16 bits (clamped at the max)

**Supersession (17 bits = 1 stale flag + 16-bit target hash):**

- If the node is live: `supersession_bits = 0`
- If superseded: stale flag set, plus a 16-bit hash of the superseding id:

```
if node.superseded_by:
    supersession_bits = 0x10000 | blake2b_16bits(node.superseded_by)
else:
    supersession_bits = 0
```

## 3. Binary Layout

```
Byte   0–5  : semantic_hash (48 bits)
Byte   6    : type (4 bits) | confidence_hi (4 bits of the 5-bit confidence)
Byte   7    : confidence_lo (1 bit) | domain (6 bits) | stale flag (1 bit)
Byte   8–11 : time (16 bits) | supersession target hash (16 bits)
```

Total: **96 bits (12 bytes)** per node. Every bit is allocated:
48 + 4 + 5 + 6 + 1 + 16 + 16 = 96.

### Implementation notes (v0.1)

Two deviations from the original draft, made because 33 provenance bits
cannot fit in the 4-byte tail alongside nothing else:

1. The byte-7 bit originally marked *reserved* now carries the supersession
   **stale flag**; the tail holds exactly time (16) + target hash (16).
   Without this, `time >= 2^15` overflowed the 32-bit pack and the high
   time bit was silently lost on round-trip.
2. The draft's second supersession variant ("I am a replacement" + count of
   nodes replaced) is **not implemented** — it overloaded the same bit
   pattern as the stale flag and is deferred until an experiment needs it.

## 4. Distance Metrics

### 4.1 Semantic distance (text similarity)

**Hamming distance** on the semantic_hash field:

```python
def semantic_distance(mb1: MemoricBinary, mb2: MemoricBinary) -> int:
    # XOR the 48-bit semantic fields and count 1-bits
    return popcount(mb1.semantic ^ mb2.semantic)  # 0–48
```

Low distance = similar text (with simhash; with Blake2b, only 0 is meaningful — see §2.1).

### 4.2 Epistemic distance (disagreement detection)

**Composite distance** accounting for type, confidence, and supersession:

```python
def epistemic_distance(mb1, mb2) -> float:
    # If either node is stale, contention is resolved, not live
    if mb1.is_stale or mb2.is_stale:
        return 0.0

    type_penalty = 2.0 if mb1.type != mb2.type else 0.0      # epistemic roles
    conf_delta = abs(mb1.confidence - mb2.confidence) / 31.0  # disagreement signal
    semantic_norm = semantic_distance(mb1, mb2) / 48.0

    return min(1.0, (type_penalty + 2.0 * conf_delta + 1.0 * semantic_norm) / 5.0)
```

### 4.3 Embedding scatter (contention signal)

**For a set of claims on the same question:**

```python
def embedding_scatter(nodes: list[MemoricBinary]) -> float:
    """Average pairwise epistemic distance."""
    if len(nodes) < 2:
        return 0.0
    distances = [epistemic_distance(n1, n2)
                 for i, n1 in enumerate(nodes) for n2 in nodes[i+1:]]
    return sum(distances) / len(distances)
```

High scatter = the graph disagrees with itself here = contention signal.

## 5. Encoder / Decoder

See `perdura_memoric.py` for the reference implementation: `encode(node_dict,
graph_start_time, semantic_fn=...)` → `MemoricBinary`, `decode(mb,
graph_start_time)` → metadata dict, plus `to_bytes`/`from_bytes` (12-byte
round-trip) and base64 helpers for JSON storage.

Decoding is **lossy by design**: you cannot reconstruct the original text
from memoric binary. You can reconstruct type, confidence (5-bit precision),
domain tags, creation hour, liveness, and the supersession target hash.

## 6. Integration with Perdura

### 6.1 Storage — DECIDED: derived, never persisted

Memoric binary is **derived state**: deterministically recomputable from
fields the graph already stores (text, type, confidence, domain_tags,
created_at, superseded_by). It is therefore computed on demand via
`encode_node(node, graph_start_time)` and **not** written into
`perdura_graph.json`.

Consequences of this decision:

- **No schema change, no migration.** Old graph files load in new code and
  vice versa; `Node(**n)` loading never meets an unknown field.
- **No staleness.** Confidence updates and supersession changes are picked
  up automatically on the next encode; a persisted copy would silently rot.
- **The spec stays free to change** (bit allocation, semantic_fn — open
  question 6) without re-encoding anything, because nothing is encoded at
  rest.

If Phase 1.5's ChromaDB integration makes recomputation a measurable cost,
persistence may be added then — strictly as a cache keyed by a
`memoric_version` field, with recompute-on-mismatch. The graph's
source-of-truth fields remain authoritative either way.

### 6.2 Briefing assembly (Phase 1.5)

Briefings may include the memoric binary per node. Workers don't *use* it
(they see the text); it's there for the conductor to reason about.

### 6.3 Contention recompute — DECIDED: default tracks Phase 0 status

Implemented in `Graph.contention(node_ids, memoric_weight=...)`:

```python
contention = (1 - w) * edge_signal + w * embedding_scatter(claim_mbs)
```

`w` defaults to **0.0** (edge-only) until experiments 1 and 3 clear their
bars — the blend is the thing under test, so it stays out of the default
routing signal (issue #11; this reverts the brief 2026-06-10 early flip).
`--memoric-weight 0` (or `memoric_weight=0` per call) reproduces the
original edge-only docs/design.md §5 metric bit-for-bit; experiments 1 and
3 should keep using `w=0` as the baseline arm so validation still measures
the blend against the metric it replaced. The Phase 3 router eventually
owns the weight.

## 7. Validation Experiments

Implemented in `experiments/memoric_eval.py`.

### Experiment 1: Hidden disagreement detection

**Hypothesis:** Epistemic distance in memoric binary detects disagreement *before* explicit `contradicts` edges.

**Method:** compute embedding scatter per question over its answering claims; measure AUC of scatter as a predictor of contradicts edges. *Current implementation measures association on the final graph state; testing temporal precedence ("before the edge appears") requires replaying the merge log turn by turn — planned refinement.*

**Success criteria:** AUC > 0.7

### Experiment 2: Per-model track records in vector space

**Hypothesis:** Models that produce claims close to future `decision` nodes are more reliable.

**Method:** per worker, scatter against decision-linked nodes (good) vs superseded/rejected nodes (bad) → reliability score; correlate with acceptance rate aggregated from the merge log.

**Success criteria:** |correlation| > 0.6

### Experiment 3: Compression without information loss

**Hypothesis:** You can drop full-text retrieval and use memoric binary alone without losing routing quality.

**Method:** compare top-3 routing order under edge-only contention vs scatter-only contention. *Meaningful only with more than ~3 open questions.*

**Success criteria:** >90% routing agreement

## 7.5 The inversion finding (2026-06-12) — semantic distance, redefined

Post-hoc separability on a real session (`experiments/collision_probe.py`):
**contradicting claim pairs are lexically CLOSER than random pairs**
(simhash AUC 0.296, confidence-delta 0.371 — both inverted vs the spec's
assumption). Real disagreement is same-topic + opposite-stance; stance is
invisible to any hash. Consequences:

- §4.2's "higher distance = more likely disagreement" is **wrong for real
  model prose**. The synthetic pass was the generator's planted lexical/
  confidence asymmetry, not the world.
- **Redefinition:** the semantic field is a disagreement *locator*. Pairs
  in the collision band — closer than random, farther than duplicates
  (`COLLISION_LOW < d <= COLLISION_HIGH`) — are where latent disagreement
  lives. `perdura_memoric.collision_candidates()` finds them
  deterministically; the stance-audit boarding mode (`--audit-every`)
  has a cheap worker judge agree-vs-oppose and submit ordinary edges-only
  deltas through the normal validate/merge. Hash for candidates, small
  model for stance, frontier never needed.
- Open question 6 is **resolved by reframing**: simhash wins because
  collision detection is a lexical job (it also powers near-dup
  consolidation); blake2b retires to exact-dedup only. Scatter-based
  contention (§4.3/§6.3) should not advance to the default until rebuilt
  on stance-aware signals (audited edges or real embeddings).

## 8. Open Questions

1. **Time granularity** — is 1 hour fine enough? Or should it be 1 minute for fast sessions?
2. **Domain inflation** — as Perdura grows, new domain tags appear. How do you handle them? Rotate bits?
3. **Memoric binary versioning** — when the encoding changes (e.g. semantic_fn swap), do you re-encode all nodes? (`memoric_version` field exists for this.)
4. **Cross-model comparability** — memoric binary is model-independent by construction (hashes, not embeddings), but does that hold once a learned semantic field is introduced?
5. **Bit allocation** — is 48 bits for semantics the right trade-off? (Early experiments will answer this.)
6. **Semantic hash choice** — Blake2b's avalanche property makes the semantic field a duplicate detector, not a similarity signal (§2.1). Should the spec switch to `simhash_48bits` (locality-sensitive, same width, still deterministic) before locking? Run experiments 1 and 3 under both and compare.

## 9. Timeline

- **Week 1:** Implement encoder/decoder (perdura_memoric.py) ✅
- **Week 2:** Run Experiments 1–3 with mock data and real sessions
- **Week 3:** Iterate on spec based on results (e.g., adjust bit allocations, settle Open Question 6)
- **Week 4:** Lock spec, integrate into Phase 1.5 code (retrieval layer)
