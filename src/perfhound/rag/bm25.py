"""Okapi BM25 (Robertson & Zaragoza) - small, dependency-free, deterministic.

score(q, d) = sum over query terms t of
    idf(t) * tf(t,d) * (k1 + 1) / (tf(t,d) + k1 * (1 - b + b * |d| / avgdl))
with idf(t) = ln(1 + (N - df(t) + 0.5) / (df(t) + 0.5))   (always >= 0)

Query terms are counted with multiplicity: a function the benchmark calls five
times weighs more than one it mentions once.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Sequence


class BM25:
    def __init__(self, documents: Sequence[Sequence[str]], *, k1: float = 1.5, b: float = 0.75) -> None:
        if not documents:
            raise ValueError("BM25 needs at least one document")
        self.k1, self.b = k1, b
        self.tf = [Counter(doc) for doc in documents]
        self.lengths = [len(doc) for doc in documents]
        self.avgdl = (sum(self.lengths) / len(self.lengths)) or 1.0
        df: Counter[str] = Counter()
        for counts in self.tf:
            df.update(counts.keys())
        n = len(documents)
        self.idf = {t: math.log(1.0 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    def score(self, query: Sequence[str], index: int) -> float:
        counts, length = self.tf[index], self.lengths[index]
        norm = self.k1 * (1.0 - self.b + self.b * length / self.avgdl)
        total = 0.0
        for term, q_count in Counter(query).items():
            f = counts.get(term, 0)
            if f:
                total += q_count * self.idf[term] * f * (self.k1 + 1.0) / (f + norm)
        return total

    def scores(self, query: Sequence[str]) -> list[float]:
        return [self.score(query, i) for i in range(len(self.tf))]
