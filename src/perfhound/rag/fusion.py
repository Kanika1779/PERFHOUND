"""Reciprocal Rank Fusion (Cormack, Clarke & Buettcher, SIGIR 2009).

    rrf(d) = sum over rankers r of  1 / (k + rank_r(d))      k = 60 (paper default)

Uses only RANKS, so BM25 scores (unbounded) and cosine similarities (-1..1)
never have to be put on one scale.
"""

from __future__ import annotations

from typing import Sequence

from .retriever import RankedCandidate


def reciprocal_rank_fusion(rankings: Sequence[Sequence[RankedCandidate]], *, k: int = 60) -> list[RankedCandidate]:
    if not rankings:
        return []
    fused: dict[str, float] = {}
    position: dict[str, int] = {}
    for ranking in rankings:
        for r in ranking:
            fused[r.sha] = fused.get(r.sha, 0.0) + 1.0 / (k + r.rank)
            position[r.sha] = r.position
    order = sorted(fused, key=lambda sha: (-fused[sha], -position[sha]))
    return [RankedCandidate(sha, position[sha], fused[sha], rank) for rank, sha in enumerate(order, start=1)]
