"""Step 6 scheduler on simulated 20-commit windows with known culprits."""

import math
import random
import statistics

import pytest

from perfhound.search import FixedTester, SPRTTester, localize

N = 20


class Window:
    """good = c0; commits before `culprit` run at 1.0 x base, from the culprit on at `ratio` x base."""

    def __init__(self, culprit, ratio=0.6, sigma=0.08, spike_p=0.0, seed=0):
        self.commits = [f"c{i}" for i in range(N + 1)]
        self.mean = {c: (0.025 * (ratio if i >= culprit else 1.0)) for i, c in enumerate(self.commits)}
        self.sigma, self.spike_p = sigma, spike_p
        self.rng = random.Random(seed)
        self.runs = 0

    def draw(self, c):
        self.runs += 1
        x = self.mean[c] * math.exp(self.rng.gauss(0, self.sigma))
        return x * 3 if self.rng.random() < self.spike_p else x


def prior_on(target, mass=0.6):
    rest = (1 - mass) / (N - 1)
    return [mass if j + 1 == target else rest for j in range(N)]


def run(kind, *, trials=300, ratio=0.6, spike_p=0.0, tester=None, update="soft"):
    hits, runs, tests = 0, [], []
    for t in range(trials):
        rng = random.Random(1000 + t)
        k = rng.randint(1, N)
        w = Window(k, ratio=ratio, spike_p=spike_p, seed=t)
        prior = {"uniform": None, "right": prior_on(k), "wrong": prior_on(N + 1 - k if N + 1 - k != k else (k % N) + 1)}[kind]
        r = localize(w, w.commits, prior, tester=tester, update=update)
        hits += r.index == k
        runs.append(r.runs)
        tests.append(r.tests)
    return hits / trials, statistics.mean(runs), statistics.mean(tests)


def test_uniform_prior_behaves_like_bisection():
    acc, runs, tests = run("uniform")
    assert acc >= 0.95
    assert 4 <= tests <= 8                       # log2(20) = 4.3, + confirmation tests near the end


def test_good_prior_saves_runs_and_wrong_prior_costs_only_runs():
    acc_u, runs_u, _ = run("uniform")
    acc_r, runs_r, tests_r = run("right")
    acc_w, runs_w, _ = run("wrong")
    assert acc_r >= 0.95 and acc_w >= 0.90       # a wrong prior must not make it wrong ...
    assert runs_r < runs_u < runs_w              # ... it only costs runs
    assert tests_r <= 3


def test_noise_and_spikes_soft_beats_hard():
    acc_soft, _, _ = run("uniform", ratio=0.85, spike_p=0.1)
    acc_hard, _, _ = run("uniform", ratio=0.85, spike_p=0.1, tester=FixedTester(1), update="hard")
    assert acc_soft >= 0.75                      # measured 0.81; misses are mostly "no change" calls
    assert acc_soft > acc_hard + 0.3             # git-bisect-style single runs fall apart (0.33)


def test_honest_cost_on_easy_cases():
    """Big clean change: plain bisection with ONE run per step is cheap and correct. We pay for robustness
    (>= 2 samples per test + confirmation tests); only a good prior brings us level. Keep this visible."""
    _, runs_bisect1, _ = run("uniform", tester=FixedTester(1), update="hard")
    _, runs_uniform, _ = run("uniform")
    _, runs_right, _ = run("right")
    assert runs_bisect1 < runs_uniform and runs_right <= runs_bisect1 + 1


def test_no_change_is_reported_not_guessed():
    w = Window(culprit=N + 5)                    # culprit outside the window: no change at all
    r = localize(w, w.commits)
    assert r.stopped == "no_change" and r.culprit is None


def test_culprit_at_the_edges():
    for k in (1, N):
        w = Window(k, seed=k)
        assert localize(w, w.commits).index == k


def test_bad_arguments():
    w = Window(3)
    with pytest.raises(ValueError):
        localize(w, w.commits, [1.0] * 5)
    with pytest.raises(ValueError):
        localize(w, w.commits, update="maybe")
