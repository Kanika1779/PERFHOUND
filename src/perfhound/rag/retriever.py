"""Rank candidate commits for a symptom query (RAG v1: BM25 only).

The output is a SCORE FOR EVERY CANDIDATE, not a filtered top-k: later the
scheduler turns scores into a prior over all commits, so a retrieval miss
costs extra benchmark runs, never a wrong answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from .bm25 import BM25
from .documents import FIELDS, CommitDocument
from .tokenize import tokenize


@dataclass(frozen=True)
class RankedCandidate:
    sha: str
    position: int
    score: float
    rank: int          # 1 = most suspicious


def rank_candidates(query: str, documents: Sequence[CommitDocument], *, fields: Iterable[str] = FIELDS,
                    k1: float = 1.5, b: float = 0.75) -> list[RankedCandidate]:
    fields = tuple(fields)
    if not documents:
        return []
    index = BM25([d.tokens(fields) for d in documents], k1=k1, b=b)
    scores = index.scores(tokenize(query))
    # ties: later commit first (deterministic; no information about the answer)
    order = sorted(range(len(documents)), key=lambda i: (-scores[i], -documents[i].position))
    return [RankedCandidate(documents[i].sha, documents[i].position, scores[i], rank)
            for rank, i in enumerate(order, start=1)]


def rank_dense(query: str, documents: Sequence[CommitDocument], embedder, *,
               fields: Iterable[str] = ("message", "paths", "functions")) -> list[RankedCandidate]:
    """Rank by cosine similarity between query and document embeddings."""
    from .embeddings import cosine

    fields = tuple(fields)
    if not documents:
        return []
    doc_vecs = embedder.embed_documents([d.text(fields) for d in documents])
    q = embedder.embed_query(query)
    scores = [cosine(q, v) for v in doc_vecs]
    order = sorted(range(len(documents)), key=lambda i: (-scores[i], -documents[i].position))
    return [RankedCandidate(documents[i].sha, documents[i].position, scores[i], rank)
            for rank, i in enumerate(order, start=1)]


def rank_hybrid(query: str, documents: Sequence[CommitDocument], embedder, *,
                bm25_fields: Iterable[str] = ("message", "paths", "functions"),
                dense_fields: Iterable[str] = ("message", "paths", "functions"), k: int = 60) -> list[RankedCandidate]:
    """BM25 + dense, fused with Reciprocal Rank Fusion."""
    from .fusion import reciprocal_rank_fusion

    return reciprocal_rank_fusion([
        rank_candidates(query, documents, fields=bm25_fields),
        rank_dense(query, documents, embedder, fields=dense_fields),
    ], k=k)
