"""Retrieval metrics for cases with a known culprit."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class RetrievalResult:
    case_id: str
    n: int             # number of candidates
    rank: int          # rank of the culprit, 1 = top


def recall_at(results: Sequence[RetrievalResult], k: int) -> float:
    return sum(r.rank <= k for r in results) / len(results)


def mrr(results: Sequence[RetrievalResult]) -> float:
    return sum(1.0 / r.rank for r in results) / len(results)


def random_recall_at(results: Sequence[RetrievalResult], k: int) -> float:
    """Expected recall@k of a random order (the floor every method must beat)."""
    return sum(min(k, r.n) / r.n for r in results) / len(results)


def random_mrr(results: Sequence[RetrievalResult]) -> float:
    return sum(sum(1.0 / i for i in range(1, r.n + 1)) / r.n for r in results) / len(results)
