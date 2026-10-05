"""Step 6: Scheduler - which commit to benchmark next, and when to stop.

Commits are a line:  c0 = good (known good) , c1 ... cn  (cn = bad).  Exactly one ci is the
culprit: every commit before it behaves like good, it and every commit after behave like bad.
Testing cm answers "is the culprit at or before m?".

Algorithm: PROBABILISTIC BISECTION (Horstein 1963; Waeber, Frazier & Henderson 2013).
    posterior <- prior over c1..cn               (uniform = git bisect; RAG+LLM = Perfhound)
    repeat:
        m <- weighted median: P(culprit <= m) closest to 1/2      (most informative test)
        verdict <- tester(cm)                                      (SPRT or fixed k samples)
        soft update with the tester's error rate e:
            verdict "bad"  -> j <= m  times (1 - e),  j > m  times e
            verdict "good" -> j <= m  times e,        j > m  times (1 - e)
    until max posterior >= confidence (or the test budget is spent)
Soft updates mean a wrong verdict is never fatal: it costs extra tests, the evidence recovers.
update="hard" (e = 0) is classic bisection: a wrong verdict eliminates the culprit for good.

Measured on simulated 20-commit windows (tests/test_scheduler.py; noise 8 %, culprit random):
                               40 % change, clean      15 % change, 10 % 3x spikes
    git bisect, 1 run/step       100 %, 14.4 runs        33 %, 26 runs
    git bisect, 5 runs/step      100 %, 31.8 runs        57 %, 41 runs
    PBA + SPRT, uniform prior    100 %, 22.7 runs        81 %, 49 runs
    PBA + SPRT, right prior      100 %, 13.8 runs        83 %, 31 runs
    PBA + SPRT, wrong prior      100 %, 23.8 runs        81 %, 52 runs
  -> a good prior saves ~40 % of runs; a wrong one costs ~5 %, never accuracy. On big clean
     changes plain bisection is as cheap as we are - our edge is noise (accuracy) + prior (runs).
  error e: 0.02 saves 2-6 runs but loses ~2 % accuracy under spikes; kept at 0.05 (safer).

Cost reported = benchmark runs (samples), INCLUDING the calibration of good and bad that every
method needs - that is what a developer pays for.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

from .sprt import BAD, GOOD, Levels, Verdict, calibrate, classify


class SPRTTester:
    """Sample until the SPRT is sure (Step 5)."""

    def __init__(self, **sprt_kw) -> None:
        self.kw = sprt_kw
        self.name = "sprt"

    def run(self, source, commit: str, levels: Levels) -> Verdict:
        return classify(source, commit, levels, **self.kw)


class FixedTester:
    """What people do by hand: k runs, compare the median with the midpoint between good and bad."""

    def __init__(self, k: int = 5) -> None:
        self.k = k
        self.name = f"fixed{k}"

    def run(self, source, commit: str, levels: Levels) -> Verdict:
        ys = sorted(math.log(source.draw(commit)) for _ in range(self.k))
        med = ys[len(ys) // 2] if len(ys) % 2 else (ys[len(ys) // 2 - 1] + ys[len(ys) // 2]) / 2
        label = BAD if abs(med - levels.bad) < abs(med - levels.good) else GOOD
        return Verdict(commit, label, self.k, 0.0, False)


@dataclass(frozen=True)
class Step:
    index: int
    commit: str
    verdict: str
    samples: int
    forced: bool
    p_max_after: float


@dataclass
class SearchResult:
    culprit: str | None
    index: int | None                  # position in `commits` (1..n), None if no change
    confidence: float
    runs: int                          # every benchmark sample, calibration included
    calibration_runs: int
    tests: int
    stopped: str                       # "confident" | "budget" | "no_change"
    posterior: list[float] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)


def _normalize(p: Sequence[float]) -> list[float]:
    t = sum(p)
    return [x / t for x in p]


def localize(source, commits: Sequence[str], prior: Sequence[float] | None = None, *, tester=None,
             error: float = 0.05, update: str = "soft", confidence: float = 0.95, max_tests: int = 40,
             calibration: dict | None = None, on_step=None) -> SearchResult:
    """commits = [good, c1, ..., cn]; prior over c1..cn (None = uniform). Returns the culprit estimate."""
    n = len(commits) - 1
    if n < 1:
        raise ValueError("need good plus at least one candidate")
    tester = tester or SPRTTester()
    if prior is None:
        post = [1.0 / n] * n
    else:
        if len(prior) != n:
            raise ValueError(f"prior has {len(prior)} entries for {n} candidates")
        post = _normalize([max(float(x), 1e-12) for x in prior])
    e = 0.0 if update == "hard" else error
    if update not in ("soft", "hard"):
        raise ValueError("update must be 'soft' or 'hard'")

    runs0 = source.runs
    cal = calibrate(source, commits[0], commits[-1], **(calibration or {}))
    cal_runs = source.runs - runs0
    if on_step:
        on_step(cal, post)
    if not cal.changed:
        return SearchResult(None, None, 0.0, source.runs - runs0, cal_runs, 0, "no_change", post, [])

    steps: list[Step] = []
    while max(post) < confidence and len(steps) < max_tests and n > 1:
        # weighted median over testable points m = 1..n-1 (cn is bad by calibration)
        cum, best, best_gap = 0.0, 1, 2.0
        for m in range(1, n):
            cum += post[m - 1]
            gap = abs(cum - 0.5)
            if gap < best_gap - 1e-12:
                best, best_gap = m, gap
        m = best
        v = tester.run(source, commits[m], cal.levels)
        bad = v.label == BAD
        post = [p * ((1 - e) if ((j + 1 <= m) == bad) else e) for j, p in enumerate(post)]
        if sum(post) == 0:                         # hard mode contradicted itself: restart from the prior
            post = [1.0 / n] * n
        post = _normalize(post)
        steps.append(Step(m, commits[m], v.label, v.samples, v.forced, max(post)))
        if on_step:
            on_step(steps[-1], post)

    j = max(range(n), key=lambda i: post[i])
    stopped = "confident" if max(post) >= confidence else "budget"
    return SearchResult(commits[j + 1], j + 1, post[j], source.runs - runs0, cal_runs, len(steps), stopped, post, steps)
