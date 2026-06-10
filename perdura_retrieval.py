"""
perdura_retrieval.py — Phase 1.5 retrieval layer.

Briefing assembly goes through a Retriever: given the graph and a question,
return the set of node ids worth considering (build_briefing then ranks by
confidence and applies the character budget, unchanged).

- GraphRetriever  — Phase 1 behavior, reproduced exactly: 2-hop
  neighborhood plus all open questions. The default; the baseline arm for
  research question #1 (does briefing-only context win, at what size?).
- HybridRetriever — BM25 + dense similarity + graph expansion. The dense
  channel uses hashed character-trigram vectors (deterministic, no model
  download); swap in a learned embedding later without touching callers.
- ChromaIndex     — optional persistent dense index (pip install chromadb).
  Vectors are supplied explicitly, so Chroma never fetches a default
  embedding model.

Usage:
    python perdura.py run --turns 6 --retriever hybrid
    python perdura.py run --turns 6 --retriever chroma   # persistent index
"""

import math
import re
from abc import ABC, abstractmethod
from hashlib import blake2b

DENSE_DIM = 256


# ---------------------------------------------------------------------------
# Scoring primitives
# ---------------------------------------------------------------------------

def _tokens(text: str) -> list:
    return re.findall(r"[a-z0-9]+", text.lower())


def hash_vector(text: str) -> list:
    """L2-normalized hashed char-trigram bag. Deterministic, model-free —
    the Phase 1.5 placeholder for a learned dense embedding."""
    t = " ".join(text.lower().split())
    v = [0.0] * DENSE_DIM
    for i in range(max(1, len(t) - 2)):
        h = int.from_bytes(blake2b(t[i:i + 3].encode(), digest_size=4).digest(),
                           "big")
        v[h % DENSE_DIM] += 1.0
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / norm for x in v]


def cosine(a: list, b: list) -> float:
    return sum(x * y for x, y in zip(a, b))


class BM25:
    """Minimal Okapi BM25 over a {doc_id: text} corpus."""

    def __init__(self, docs: dict, k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.tf = {did: {} for did in docs}
        self.df: dict = {}
        self.dl = {}
        for did, text in docs.items():
            toks = _tokens(text)
            self.dl[did] = len(toks) or 1
            for tok in toks:
                self.tf[did][tok] = self.tf[did].get(tok, 0) + 1
            for tok in set(toks):
                self.df[tok] = self.df.get(tok, 0) + 1
        self.n = len(docs)
        self.avgdl = (sum(self.dl.values()) / self.n) if self.n else 1.0

    def score(self, query: str) -> dict:
        scores = {did: 0.0 for did in self.tf}
        for tok in set(_tokens(query)):
            if tok not in self.df:
                continue
            idf = math.log(1 + (self.n - self.df[tok] + 0.5) / (self.df[tok] + 0.5))
            for did, tf in self.tf.items():
                f = tf.get(tok, 0)
                if not f:
                    continue
                denom = f + self.k1 * (1 - self.b + self.b * self.dl[did] / self.avgdl)
                scores[did] += idf * f * (self.k1 + 1) / denom
        return scores


def _normalized(scores: dict) -> dict:
    top = max(scores.values(), default=0.0)
    return {k: v / top for k, v in scores.items()} if top > 0 else scores


# ---------------------------------------------------------------------------
# Retrievers
# ---------------------------------------------------------------------------

class Retriever(ABC):
    @abstractmethod
    def retrieve(self, graph, question) -> set:
        """Return the node ids the briefing should consider."""


class GraphRetriever(Retriever):
    """Phase 1 behavior exactly: n-hop neighborhood + all open questions."""

    def __init__(self, hops: int = 2):
        self.hops = hops

    def retrieve(self, graph, question) -> set:
        ids = graph.neighborhood(question.id, hops=self.hops)
        ids |= {q.id for q in graph.open_questions()}
        return ids


class HybridRetriever(Retriever):
    """BM25 + dense similarity seeds, expanded along graph edges.

    Reaches relevant nodes the 2-hop neighborhood misses (cross-question
    evidence, orphaned claims) while expansion keeps structural context.
    """

    def __init__(self, top_k: int = 20, expand_hops: int = 1,
                 bm25_weight: float = 0.5, index=None):
        self.top_k = top_k
        self.expand_hops = expand_hops
        self.bm25_weight = bm25_weight
        self.index = index  # optional ChromaIndex for the dense channel

    def retrieve(self, graph, question) -> set:
        cands = {n.id: n.text for n in graph.live_nodes()
                 if n.id != question.id}
        if not cands:
            return {question.id}

        bm25 = _normalized(BM25(cands).score(question.text))
        if self.index is not None:
            self.index.sync(graph)
            dense = self.index.query(question.text, k=len(cands))
        else:
            qv = hash_vector(question.text)
            dense = {nid: cosine(qv, hash_vector(text))
                     for nid, text in cands.items()}
        dense = _normalized(dense)

        combined = {nid: self.bm25_weight * bm25.get(nid, 0.0)
                    + (1 - self.bm25_weight) * dense.get(nid, 0.0)
                    for nid in cands}
        seeds = {nid for nid, _ in sorted(combined.items(),
                                          key=lambda kv: -kv[1])[: self.top_k]}

        ids = {question.id} | seeds
        frontier = set(ids)
        for _ in range(self.expand_hops):
            nxt = set()
            for e in graph.edges.values():
                if e.src in frontier and e.dst not in ids:
                    nxt.add(e.dst)
                if e.dst in frontier and e.src not in ids:
                    nxt.add(e.src)
            ids |= nxt
            frontier = nxt
        ids |= {q.id for q in graph.open_questions()}
        return ids


# ---------------------------------------------------------------------------
# Optional persistent dense index (ChromaDB)
# ---------------------------------------------------------------------------

class ChromaIndex:
    """Persistent dense index. Embeddings are supplied explicitly
    (hash_vector), so Chroma never downloads an embedding model."""

    def __init__(self, path: str = ".perdura_chroma", collection: str = "nodes"):
        import chromadb
        self._col = chromadb.PersistentClient(path=path).get_or_create_collection(
            collection, metadata={"hnsw:space": "cosine"})
        self._synced: set = set()

    def sync(self, graph):
        fresh = [n for n in graph.live_nodes() if n.id not in self._synced]
        if not fresh:
            return
        self._col.upsert(
            ids=[n.id for n in fresh],
            embeddings=[hash_vector(n.text) for n in fresh],
            documents=[n.text for n in fresh])
        self._synced |= {n.id for n in fresh}

    def query(self, text: str, k: int) -> dict:
        total = self._col.count()
        if not total:
            return {}
        res = self._col.query(query_embeddings=[hash_vector(text)],
                              n_results=min(k, total))
        return {nid: 1.0 - dist
                for nid, dist in zip(res["ids"][0], res["distances"][0])}


RETRIEVERS = {
    "graph": lambda: GraphRetriever(),
    "hybrid": lambda: HybridRetriever(),
    "chroma": lambda: HybridRetriever(index=ChromaIndex()),
}
