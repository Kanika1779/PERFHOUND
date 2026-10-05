"""Step 5: SPRT - is this commit like `good` or like `bad`? Decided with as few samples as possible.

Wald's Sequential Probability Ratio Test. Benchmark times are compared on a LOG scale
(noise on a laptop is multiplicative: "8 % slower", not "2 ms slower").

    levels = estimate_levels(good_samples, bad_samples)   # where are 'good' and 'bad'?
    test = SPRT(levels)                                     # alpha = P(say bad | it is good)
    while test.decision is None:                            # beta  = P(say good | it is bad)
        test.update(source.draw(commit))

Per sample y = log(time):  LLR += [ (y - good)^2 - (y - bad)^2 ] / (2 sigma^2)
    LLR >= log((1 - beta) / alpha)  -> "bad"
    LLR <= log(beta / (1 - alpha))  -> "good"
    otherwise                       -> one more sample (up to max_samples, then forced by sign)

Robustness choices (a laptop is not a benchmark server - see dask-5553: 23 ms -> 96 ms spikes):
* levels are medians; noise is a pooled sd after damping spikes (winsorizing at 3 MAD).
* every sample is CLIPPED to [min(good, bad), max(good, bad)] before it counts. One sample can
  then move the LLR by at most d^2/2 (d = separation), so a single 3x spike can be cancelled by
  one normal sample instead of deciding the commit on its own.
* each sample's LLR contribution is CAPPED at 2/3 of the decision threshold (Huber's robust SPRT,
  Ann. Math. Stat. 1965: a clipped likelihood ratio). A verdict then needs net TWO agreeing samples:
  a clear case still takes 2 samples, a spike costs one extra sample instead of a wrong verdict.
  Simulated laptop noise (sigma 0.08, 10 % of samples 3x spikes), alpha = beta = 0.05:
      ratio 0.60: error 4.6 % -> 2.1 % with the cap (mean samples 2.2 -> 2.3)
      ratio 0.85: error 4.4 % -> 3.3 %                 (mean samples 7.5 -> 7.7)
  Without spikes: ratio 0.85 -> 2.3 % error, 3.7 samples; ratio <= 0.6 -> 0 errors, 2 samples.
* min_samples = 2: no verdict from one sample, however clear it looks.
* sigma has a floor (2 %: no laptop is quieter) and is inflated for the few samples it came from.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

GOOD, BAD = "good", "bad"


def _median(v: Sequence[float]) -> float:
    s = sorted(v)
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2


@dataclass(frozen=True)
class Levels:
    good: float          # log-time level of the good side (median)
    bad: float           # log-time level of the bad side (median)
    sigma: float         # noise on the log scale (robust, inflated for small n)
    n_good: int
    n_bad: int

    @property
    def separation(self) -> float:
        """d = distance between the levels in noise units. d >= 2.5: ~2 samples decide; d < 1: hard."""
        return abs(self.bad - self.good) / self.sigma

    @property
    def ratio(self) -> float:
        """bad / good time ratio (0.24 = bad is 4x faster)."""
        return math.exp(self.bad - self.good)


def _winsorized_sd(v: list[float]) -> float:
    """Sample sd (n - 1) after pulling points beyond median +- 3 MAD back to that edge (spike damping)."""
    m = _median(v)
    mad = 1.4826 * _median([abs(y - m) for y in v])
    if mad > 0:
        v = [min(max(y, m - 3 * mad), m + 3 * mad) for y in v]
    mean = sum(v) / len(v)
    return math.sqrt(sum((y - mean) ** 2 for y in v) / (len(v) - 1))


def estimate_levels(good: Sequence[float], bad: Sequence[float], *, min_sigma: float = 0.02) -> Levels:
    """Levels = medians of log-times. Noise = pooled winsorized sd, inflated for the few samples used.

    (A MAD-only estimate was tried first: with 3 samples per side it came out ~3x too small -
    the median point always has residual 0 - and the SPRT became overconfident. tests/test_sprt.py.)
    """
    if len(good) < 2 or len(bad) < 2:
        raise ValueError("need at least 2 samples of good and of bad")
    lg = [math.log(x) for x in good]
    lb = [math.log(x) for x in bad]
    sg, sb = _winsorized_sd(lg), _winsorized_sd(lb)
    pooled = math.sqrt(((len(lg) - 1) * sg ** 2 + (len(lb) - 1) * sb ** 2) / (len(lg) + len(lb) - 2))
    pooled *= math.sqrt(1 + 1 / min(len(lg), len(lb)))   # the levels themselves are uncertain
    return Levels(_median(lg), _median(lb), max(pooled, min_sigma), len(lg), len(lb))


@dataclass
class SPRT:
    levels: Levels
    alpha: float = 0.05
    beta: float = 0.05
    min_samples: int = 2
    max_samples: int = 24
    cap_factor: float = 2 / 3      # per-sample |LLR| <= cap_factor * threshold (Huber clipping)
    llr: float = 0.0
    n: int = 0
    decision: str | None = None
    forced: bool = False
    history: list[float] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not (0 < self.alpha < 0.5 and 0 < self.beta < 0.5):
            raise ValueError("alpha and beta must be in (0, 0.5)")
        self.upper = math.log((1 - self.beta) / self.alpha)
        self.lower = math.log(self.beta / (1 - self.alpha))
        self.cap = self.cap_factor * min(self.upper, -self.lower)

    def update(self, x: float) -> str | None:
        if self.decision is not None:
            raise RuntimeError("test already decided")
        if not x > 0:
            raise ValueError(f"benchmark time must be > 0, got {x}")
        lv = self.levels
        lo, hi = sorted((lv.good, lv.bad))
        y = min(max(math.log(x), lo), hi)
        step = ((y - lv.good) ** 2 - (y - lv.bad) ** 2) / (2 * lv.sigma ** 2)
        self.llr += min(max(step, -self.cap), self.cap)
        self.n += 1
        self.history.append(x)
        if self.n >= self.min_samples:
            if self.llr >= self.upper:
                self.decision = BAD
            elif self.llr <= self.lower:
                self.decision = GOOD
        if self.decision is None and self.n >= self.max_samples:
            self.decision, self.forced = (BAD if self.llr > 0 else GOOD), True
        return self.decision


@dataclass(frozen=True)
class Verdict:
    commit: str
    label: str           # "good" | "bad"
    samples: int
    llr: float
    forced: bool


def classify(source, commit: str, levels: Levels, **sprt_kw) -> Verdict:
    """Draw samples of `commit` from `source` until the SPRT decides."""
    t = SPRT(levels, **sprt_kw)
    while t.decision is None:
        t.update(source.draw(commit))
    return Verdict(commit, t.decision, t.n, t.llr, t.forced)


@dataclass(frozen=True)
class Calibration:
    levels: Levels
    good_samples: tuple[float, ...]
    bad_samples: tuple[float, ...]
    changed: bool        # is there a real change between good and bad at all?


def calibrate(source, good: str, bad: str, *, n_init: int = 5, max_n: int = 20, t_crit: float = 3.6,
              min_effect: float = 0.03) -> Calibration:
    """Sample both ends (interleaved) until the change is SIGNIFICANT, or give up at max_n each.

    changed  <=>  t = |log ratio| / (noise * sqrt(1/n_g + 1/n_b)) >= t_crit   AND   |ratio - 1| >= min_effect
    t_crit 3.6 is strict on purpose: the test is looked at after every pair (up to 16 looks).
    (A first version required separation d >= 2 instead. d is an EFFECT SIZE, not evidence: a real
    15 % change at 8 % noise has d ~ 2 and was called "no change" half the time, however many samples.)
    If never significant, `changed` is False: nothing to localize on this machine - say so, don't guess.
    """
    g, b = [], []
    for _ in range(n_init):
        g.append(source.draw(good))
        b.append(source.draw(bad))
    while True:
        lv = estimate_levels(g, b)
        raw_sigma = lv.sigma / math.sqrt(1 + 1 / min(len(g), len(b)))
        t = abs(lv.bad - lv.good) / (raw_sigma * math.sqrt(1 / len(g) + 1 / len(b)))
        changed = t >= t_crit and abs(lv.ratio - 1) >= min_effect
        if changed or len(g) >= max_n:
            return Calibration(lv, tuple(g), tuple(b), changed)
        g.append(source.draw(good))
        b.append(source.draw(bad))
