"""Where benchmark samples come from. Strategies only see `draw(commit) -> seconds` and `runs`.

ReplaySource  recorded windows (scripts/measure_windows.py): every commit's real samples are
              handed out in a random order without replacement; when a commit's samples run out
              they are reused (with replacement) and `reused` counts it - report it, because a
              strategy that needs more samples than were recorded is being flattered.
LiveSource    runs the benchmark for real (BenchmarkRunner) - for demos.
"""

from __future__ import annotations

import json
import random
from collections import Counter
from pathlib import Path


class ReplaySource:
    def __init__(self, samples: dict[str, list[float]], *, seed: int | str = 0) -> None:
        self._rng = random.Random(seed)
        self._samples = {c: list(v) for c, v in samples.items()}
        self._queues = {c: self._rng.sample(v, len(v)) for c, v in self._samples.items()}
        self.runs = 0
        self.per_commit: Counter[str] = Counter()
        self.reused = 0

    @classmethod
    def from_window(cls, path: str | Path, *, seed: int | str = 0) -> tuple["ReplaySource", dict]:
        """Returns (source, info). info has commits (good first) and, for SCORING ONLY, culprit_index."""
        rec = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(rec["samples"], seed=seed), rec

    def draw(self, commit: str) -> float:
        if commit not in self._samples or not self._samples[commit]:
            raise KeyError(f"no recorded samples for {commit[:10]}")
        self.runs += 1
        self.per_commit[commit] += 1
        q = self._queues[commit]
        if q:
            return q.pop()
        self.reused += 1
        return self._rng.choice(self._samples[commit])


class LiveSource:
    def __init__(self, runner) -> None:
        self.runner = runner
        self.runs = 0
        self.per_commit: Counter[str] = Counter()
        self.reused = 0

    def draw(self, commit: str) -> float:
        self.runs += 1
        self.per_commit[commit] += 1
        return self.runner.run_once(commit).value
