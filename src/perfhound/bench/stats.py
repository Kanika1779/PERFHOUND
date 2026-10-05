"""Small, dependency-free statistics for benchmark samples (Step 5 adds SPRT)."""

from __future__ import annotations

import random
import statistics
from typing import Sequence


def ratio_ci(before: Sequence[float], after: Sequence[float], *, n_boot: int = 2000, alpha: float = 0.05,
             seed: int = 0) -> tuple[float, float, float]:
    """median(after) / median(before) with a percentile-bootstrap (1 - alpha) interval."""
    if len(before) < 2 or len(after) < 2:
        raise ValueError("need at least 2 samples per side")
    ratio = statistics.median(after) / statistics.median(before)
    rng = random.Random(seed)
    boots = []
    for _ in range(n_boot):
        a = [rng.choice(before) for _ in before]
        b = [rng.choice(after) for _ in after]
        boots.append(statistics.median(b) / statistics.median(a))
    boots.sort()
    lo = boots[int(alpha / 2 * n_boot)]
    hi = boots[min(n_boot - 1, int((1 - alpha / 2) * n_boot))]
    return ratio, lo, hi


def verdict(ratio: float, lo: float, hi: float, *, min_effect: float = 0.05) -> str:
    """'faster' / 'slower' when the interval excludes 1 AND the estimate moved at least min_effect."""
    if hi < 1 and ratio <= 1 - min_effect:
        return "faster"
    if lo > 1 and ratio >= 1 + min_effect:
        return "slower"
    return "unclear"
