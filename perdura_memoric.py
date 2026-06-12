"""
perdura_memoric.py — Memoric binary encoding and distance metrics.

Memoric binary is a 96-bit (12-byte) epistemic encoding that captures:
- Semantic content (48-bit hash)
- Node type (4 bits)
- Confidence (5 bits)
- Domain tags (6 bits)
- Temporal + supersession context (33 bits: 16 time + 16 hash + 1 stale flag)

Use this to detect hidden disagreement, compute per-model track records,
and compress the graph without losing routing quality.

Spec: docs/memoric-binary.md (Phase 0 RFC — validation required before
integration; see experiments/memoric_eval.py).
"""

import base64
import struct
from dataclasses import dataclass
from hashlib import blake2b


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TYPE_MAP = {
    "question": 0b0000,
    "claim": 0b0001,
    "evidence": 0b0010,
    "decision": 0b0011,
    "rejected": 0b0100,
}
INVERSE_TYPE_MAP = {v: k for k, v in TYPE_MAP.items()}

DOMAIN_MAP = {
    "architecture": 0,
    "epistemology": 1,
    "economics": 2,
    "engineering": 3,
    "design": 4,
    "research": 5,
}

TIME_RESOLUTION = 3600  # 1 hour granularity


# ---------------------------------------------------------------------------
# Memoric binary dataclass
# ---------------------------------------------------------------------------

@dataclass
class MemoricBinary:
    """Packed representation of a node's epistemic context."""
    semantic: int      # 48 bits
    type: int          # 4 bits
    confidence: int    # 5 bits (0-31)
    domain: int        # 6 bits (bitmap)
    time: int          # 16 bits
    supersession: int  # 17 bits: bit 16 = stale flag, bits 0-15 = target hash

    def to_bytes(self) -> bytes:
        """Pack into 12 bytes (96 bits).

        Layout (per RFC §3, with the byte-7 reserved bit carrying the
        supersession stale flag — 17 provenance flag+hash bits cannot fit
        alongside 16 time bits in the 4-byte tail alone):
          bytes 0-5 : semantic (48 bits)
          byte  6   : type (4) | confidence_hi (4)
          byte  7   : confidence_lo (1) | domain (6) | stale flag (1)
          bytes 8-11: time (16) | supersession hash (16)
        """
        bytes_0_5 = self.semantic.to_bytes(6, "big")
        conf_hi = (self.confidence >> 1) & 0xF
        byte_6 = ((self.type & 0xF) << 4) | conf_hi
        conf_lo = self.confidence & 0x1
        stale = (self.supersession >> 16) & 0x1
        byte_7 = (conf_lo << 7) | ((self.domain & 0x3F) << 1) | stale
        tail = ((self.time & 0xFFFF) << 16) | (self.supersession & 0xFFFF)
        return bytes_0_5 + bytes([byte_6, byte_7]) + struct.pack(">I", tail)

    @staticmethod
    def from_bytes(b: bytes) -> "MemoricBinary":
        """Unpack from 12 bytes."""
        if len(b) != 12:
            raise ValueError(f"Expected 12 bytes, got {len(b)}")
        semantic = int.from_bytes(b[0:6], "big")
        type_ = (b[6] >> 4) & 0xF
        conf_hi = b[6] & 0xF
        conf_lo = (b[7] >> 7) & 0x1
        domain = (b[7] >> 1) & 0x3F
        stale = b[7] & 0x1
        tail = struct.unpack(">I", b[8:12])[0]
        return MemoricBinary(
            semantic=semantic,
            type=type_,
            confidence=(conf_hi << 1) | conf_lo,
            domain=domain,
            time=(tail >> 16) & 0xFFFF,
            supersession=(stale << 16) | (tail & 0xFFFF),
        )


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------

def blake2b_48bits(text: str) -> int:
    """48-bit cryptographic hash of text (Blake2b).

    NOTE: avalanche property means any two distinct texts differ in ~24 of
    48 bits regardless of similarity — this detects exact duplicates only.
    For graded text similarity use simhash_48bits (RFC open question #6).
    """
    h = blake2b(text.encode(), digest_size=6)
    return int.from_bytes(h.digest(), "big")


def simhash_48bits(text: str) -> int:
    """Locality-sensitive 48-bit sketch over character 3-grams.

    Similar texts land near each other in Hamming space, so
    semantic_distance becomes a graded similarity signal instead of a
    duplicate detector. Deterministic, no embedding model required.
    """
    t = " ".join(text.lower().split())
    grams = [t[i:i + 3] for i in range(max(1, len(t) - 2))]
    counts = [0] * 48
    for g in grams:
        h = int.from_bytes(blake2b(g.encode(), digest_size=6).digest(), "big")
        for bit in range(48):
            counts[bit] += 1 if (h >> bit) & 1 else -1
    out = 0
    for bit in range(48):
        if counts[bit] > 0:
            out |= 1 << bit
    return out


def blake2b_16bits(text: str) -> int:
    """16-bit hash for the supersession-target reference."""
    h = blake2b(text.encode(), digest_size=2)
    return int.from_bytes(h.digest(), "big")


def encode(node_dict: dict, graph_start_time: float,
           semantic_fn=blake2b_48bits) -> MemoricBinary:
    """
    Encode a Perdura node into memoric binary.

    Args:
        node_dict: dict with keys: type, text, confidence, domain_tags,
                   created_at, superseded_by
        graph_start_time: timestamp when the graph was created
        semantic_fn: 48-bit text hash (blake2b_48bits per RFC v0.1;
                     simhash_48bits for graded similarity)

    Returns:
        MemoricBinary instance
    """
    semantic = semantic_fn(node_dict.get("text") or "")

    type_bits = TYPE_MAP.get(node_dict.get("type", "claim"), 0b0001)

    if node_dict.get("type") == "question":
        conf_bits = 31
    else:
        conf = node_dict.get("confidence")
        conf = 0.5 if conf is None else conf
        conf_bits = max(0, min(31, int(conf * 31)))

    domain_bits = 0
    for tag in node_dict.get("domain_tags") or []:
        if tag in DOMAIN_MAP:
            domain_bits |= (1 << DOMAIN_MAP[tag])

    created_at = node_dict.get("created_at") or graph_start_time
    time_bits = max(0, min(0xFFFF,
                           int((created_at - graph_start_time) // TIME_RESOLUTION)))

    superseded_by = node_dict.get("superseded_by")
    if superseded_by:
        # Stale flag (bit 16) + 16-bit hash of the supersession target
        supersession_bits = 0x10000 | blake2b_16bits(superseded_by)
    else:
        supersession_bits = 0

    return MemoricBinary(
        semantic=semantic,
        type=type_bits,
        confidence=conf_bits,
        domain=domain_bits,
        time=time_bits,
        supersession=supersession_bits,
    )


def encode_node(node, graph_start_time: float,
                semantic_fn=blake2b_48bits) -> MemoricBinary:
    """Encode a perdura Node (dataclass or dict) on demand.

    Memoric binary is DERIVED state: deterministically recomputable from
    the node's source-of-truth fields. It is therefore never persisted in
    the graph file — no migrations, no staleness when confidence or
    supersession changes, and the spec (incl. semantic_fn) can evolve
    freely until Phase 0 locks it.
    """
    if not isinstance(node, dict):
        node = {k: getattr(node, k, None)
                for k in ("type", "text", "confidence", "domain_tags",
                          "created_at", "superseded_by")}
    return encode(node, graph_start_time, semantic_fn)


# ---------------------------------------------------------------------------
# Decoding (lossy)
# ---------------------------------------------------------------------------

def decode(mb: MemoricBinary, graph_start_time: float) -> dict:
    """
    Extract metadata from memoric binary. Semantic content is lost.

    Returns:
        dict with reconstructed metadata
    """
    return {
        "type": INVERSE_TYPE_MAP.get(mb.type, "claim"),
        "confidence": mb.confidence / 31.0,
        "domain_tags": [d for d, bit in DOMAIN_MAP.items()
                        if mb.domain & (1 << bit)],
        "created_at": graph_start_time + (mb.time * TIME_RESOLUTION),
        "is_live": not (mb.supersession & 0x10000),
        "supersession_hash": mb.supersession & 0xFFFF if mb.supersession else None,
    }


# ---------------------------------------------------------------------------
# Distance metrics
# ---------------------------------------------------------------------------

def popcount(x: int) -> int:
    """Count the number of 1-bits in x (Hamming weight)."""
    return x.bit_count()


def semantic_distance(mb1: MemoricBinary, mb2: MemoricBinary) -> int:
    """
    Semantic distance via Hamming distance on semantic hashes.

    Returns:
        int in [0, 48] — number of differing bits in the 48-bit semantic field
    """
    return popcount(mb1.semantic ^ mb2.semantic)


def epistemic_distance(
    mb1: MemoricBinary,
    mb2: MemoricBinary,
    graph_start_time: float = 0,
) -> float:
    """
    Epistemic distance accounting for type, confidence, supersession, and semantics.

    Higher distance = more likely disagreement.
    If either node is stale (superseded), returns 0 — resolved, not contention.

    Returns:
        float in [0, 1]
    """
    mb1_is_stale = (mb1.supersession & 0x10000) != 0
    mb2_is_stale = (mb2.supersession & 0x10000) != 0
    if mb1_is_stale or mb2_is_stale:
        return 0.0

    # Different types indicate different epistemic roles
    type_penalty = 2.0 if mb1.type != mb2.type else 0.0

    # Confidence mismatch
    conf_delta = abs(mb1.confidence - mb2.confidence) / 31.0

    # Semantic distance (normalized)
    semantic_norm = semantic_distance(mb1, mb2) / 48.0

    composite = (type_penalty +
                 2.0 * conf_delta +
                 1.0 * semantic_norm) / 5.0
    return min(1.0, composite)


def embedding_scatter(mbs: list[MemoricBinary], graph_start_time: float = 0) -> float:
    """
    Compute embedding scatter: average pairwise epistemic distance.

    High scatter = the graph disagrees with itself in this subgraph.

    Returns:
        float in [0, 1]
    """
    if len(mbs) < 2:
        return 0.0
    distances = [epistemic_distance(mb1, mb2, graph_start_time)
                 for i, mb1 in enumerate(mbs)
                 for mb2 in mbs[i + 1:]]
    return sum(distances) / len(distances) if distances else 0.0


def domain_overlap(mb1: MemoricBinary, mb2: MemoricBinary) -> int:
    """
    Count shared domain bits between two nodes.

    Returns:
        int in [0, 6] — number of overlapping domain tags
    """
    return popcount(mb1.domain & mb2.domain)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def bytes_to_hex(b: bytes) -> str:
    """Convert bytes to hex string for display/storage."""
    return b.hex()


def hex_to_bytes(h: str) -> bytes:
    """Convert hex string back to bytes."""
    return bytes.fromhex(h)


def memoric_to_base64(mb: MemoricBinary) -> str:
    """Encode memoric binary as base64 string for JSON storage."""
    return base64.b64encode(mb.to_bytes()).decode("ascii")


def base64_to_memoric(s: str) -> MemoricBinary:
    """Decode memoric binary from base64 string."""
    return MemoricBinary.from_bytes(base64.b64decode(s.encode("ascii")))


# ---------------------------------------------------------------------------
# Example usage
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import time

    now = time.time()

    node1 = {
        "type": "claim",
        "text": "Briefings should be confidence-ranked.",
        "confidence": 0.8,
        "domain_tags": ["architecture"],
        "created_at": now,
        "superseded_by": None,
    }
    node2 = {
        "type": "claim",
        "text": "Briefings should be time-sorted.",
        "confidence": 0.3,
        "domain_tags": ["architecture"],
        "created_at": now + 60,
        "superseded_by": None,
    }

    mb1 = encode(node1, now)
    mb2 = encode(node2, now)

    print("Node 1 encoding:", memoric_to_base64(mb1))
    print("Node 2 encoding:", memoric_to_base64(mb2))
    print()
    print("Semantic distance (blake2b):", semantic_distance(mb1, mb2))
    print("Semantic distance (simhash):",
          semantic_distance(encode(node1, now, simhash_48bits),
                            encode(node2, now, simhash_48bits)))
    print("Epistemic distance:", epistemic_distance(mb1, mb2, now))
    print("Embedding scatter:", embedding_scatter([mb1, mb2], now))
    print()
    print("Decoded node 1:", decode(mb1, now))

    # Round-trip integrity across field extremes
    for mb in (mb1,
               MemoricBinary(semantic=(1 << 48) - 1, type=0b0100,
                             confidence=31, domain=0b111111,
                             time=0xFFFF, supersession=0x1FFFF)):
        assert MemoricBinary.from_bytes(mb.to_bytes()) == mb, mb
    print("Round-trip: OK")


# ---------------------------------------------------------------------------
# Collision detection (the inversion finding, 2026-06-12)
# ---------------------------------------------------------------------------
# Measured on real sessions: contradicting claims are lexically CLOSER than
# random pairs (same topic, opposite stance) — so semantic distance is a
# disagreement LOCATOR, not a disagreement measure. Pairs inside the
# collision band are where latent disagreement lives; a cheap stance check
# (the audit boarding mode in perdura.py) decides agree-vs-oppose, which no
# hash can. experiments/collision_probe.py reproduces the calibration.

COLLISION_LOW = 8    # at/below: near-duplicate (consolidated at merge)
COLLISION_HIGH = 17  # above: probably different topics


def collision_candidates(graph, low=COLLISION_LOW, high=COLLISION_HIGH,
                         limit=6):
    """Unlinked live claim pairs in the lexical collision band, closest
    first: different workers, no existing edge — stance unknown."""
    claims = [n for n in graph.live_nodes() if n.type == "claim"]
    hashes = {n.id: simhash_48bits(n.text or "") for n in claims}
    linked = set()
    for e in graph.edges.values():
        linked.add(frozenset((e.src, e.dst)))

    out = []
    for i in range(len(claims)):
        for j in range(i + 1, len(claims)):
            a, b = claims[i], claims[j]
            # "Different workers" requires both attributions to exist —
            # unattributed pairs can't establish independent authorship.
            if (not a.created_by or not b.created_by
                    or a.created_by == b.created_by):
                continue
            if frozenset((a.id, b.id)) in linked:
                continue
            d = (hashes[a.id] ^ hashes[b.id]).bit_count()
            if low < d <= high:
                out.append((d, a, b))
    out.sort(key=lambda t: t[0])
    return [(a, b) for _, a, b in out[:limit]]
