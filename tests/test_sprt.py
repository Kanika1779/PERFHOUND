"""SPRT (Step 5) on simulated laptop noise with known truth.

Noise model from the Windows trials: multiplicative (log-normal) with sigma ~0.08,
plus occasional spikes (dask-5553: 23 ms -> 64-96 ms under memory pressure).
"""

import json
import math
import random

import pytest

from perfhound.search import BAD, GOOD, SPRT, ReplaySource, calibrate, classify, estimate_levels

GOOD_MS, SIGMA = 0.025, 0.08


class SimSource:
    def __init__(self, means, sigma=SIGMA, spike_p=0.0, spike_x=3.0, seed=0):
        self.means, self.sigma, self.spike_p, self.spike_x = means, sigma, spike_p, spike_x
        self.rng = random.Random(seed)
        self.runs = 0

    def draw(self, commit):
        self.runs += 1
        x = self.means[commit] * math.exp(self.rng.gauss(0, self.sigma))
        if self.rng.random() < self.spike_p:
            x *= self.spike_x
        return x


def error_rate(ratio, *, spike_p=0.0, trials=1500, alpha=0.05, beta=0.05):
    """Calibrate on good/bad, then classify a commit that is truly good and one truly bad."""
    wrong = samples = forced = 0
    for t in range(trials):
        src = SimSource({"g": GOOD_MS, "b": GOOD_MS * ratio}, spike_p=spike_p, seed=t)
        cal = calibrate(src, "g", "b")
        for truth in ("g", "b"):
            v = classify(src, truth, cal.levels, alpha=alpha, beta=beta)
            wrong += v.label != (GOOD if truth == "g" else BAD)
            samples += v.samples
            forced += v.forced
    n = 2 * trials
    return wrong / n, samples / n, forced / n


def test_levels_recover_truth():
    rng = random.Random(1)
    g = [GOOD_MS * math.exp(rng.gauss(0, SIGMA)) for _ in range(30)]
    b = [GOOD_MS * 0.24 * math.exp(rng.gauss(0, SIGMA)) for _ in range(30)]
    lv = estimate_levels(g, b)
    assert lv.ratio == pytest.approx(0.24, rel=0.06)
    assert lv.sigma == pytest.approx(SIGMA, rel=0.35)
    assert lv.separation > 10


def test_big_change_decides_in_two_samples():
    # dask-5553 on the laptop: 0.24 ratio, d ~ 18
    err, mean_n, _ = error_rate(0.24, trials=300)
    assert err == 0 and mean_n == pytest.approx(2.0)


def test_error_rate_near_alpha_for_a_modest_change():
    # 15 % change at 8 % noise: d ~ 2, the hard-but-realistic regime
    err, mean_n, forced = error_rate(0.85)
    assert err <= 0.08                     # nominal 0.05; levels are ESTIMATED from few samples
    assert 2 <= mean_n <= 8 and forced < 0.05


def test_spikes_do_not_flip_verdicts():
    err_clean, _, _ = error_rate(0.6, trials=800)
    err_spiky, n_spiky, _ = error_rate(0.6, spike_p=0.10, trials=800)
    assert err_spiky <= 0.03 and err_spiky <= err_clean + 0.02
    lv = estimate_levels([1.0, 1.02, 0.98], [0.6, 0.61, 0.59])
    t = SPRT(lv)
    t.update(3.0)                          # one 3x spike on a good commit ...
    assert t.decision is None              # ... is not a verdict (min_samples, clipping)
    t.update(0.6)                          # ... and one normal 'bad' sample cancels it
    assert t.decision is None


def test_clipping_bounds_one_sample():
    lv = estimate_levels([1.0, 1.01, 0.99], [0.5, 0.51, 0.49])
    t = SPRT(lv)
    t.update(1000.0)
    assert abs(t.llr) <= t.cap + 1e-9 and t.cap < t.upper       # one sample can never decide alone


def test_forced_decision_when_undecidable():
    lv = estimate_levels([1.0, 1.05, 0.95], [0.9, 0.95, 0.86])      # ~10 % apart, ~5 % noise
    t = SPRT(lv, max_samples=6)
    mid = math.exp((lv.good + lv.bad) / 2)
    while t.decision is None:
        t.update(mid)                      # exactly in the middle: no evidence either way
    assert t.forced and t.n == 6


def test_calibrate_detects_no_change():
    src = SimSource({"g": GOOD_MS, "b": GOOD_MS}, seed=3)
    cal = calibrate(src, "g", "b")
    assert not cal.changed and len(cal.good_samples) == 20
    src = SimSource({"g": GOOD_MS, "b": GOOD_MS * 0.5}, seed=3)
    cal = calibrate(src, "g", "b")
    assert cal.changed and len(cal.good_samples) == 5


def test_detects_a_modest_change_despite_spikes():
    """15 % change, 8 % noise, 10 % spikes: the old effect-size rule (d >= 2) found it 44 % of the time."""
    hits = sum(calibrate(SimSource({"g": GOOD_MS, "b": GOOD_MS * 0.85}, spike_p=0.1, seed=s), "g", "b").changed
               for s in range(500))
    assert hits / 500 >= 0.8


def test_false_change_rate_is_low():
    """No real change: calibrate must rarely claim one (otherwise we 'localize' noise)."""
    false = sum(calibrate(SimSource({"g": GOOD_MS, "b": GOOD_MS}, seed=s), "g", "b").changed for s in range(1000))
    assert false / 1000 <= 0.05


def test_replay_source_without_then_with_replacement(tmp_path):
    rec = {"commits": ["a", "b"], "culprit_index": 1, "samples": {"a": [1.0, 2.0, 3.0], "b": [4.0]}}
    p = tmp_path / "w.json"
    p.write_text(json.dumps(rec))
    src, info = ReplaySource.from_window(p, seed=7)
    assert sorted(src.draw("a") for _ in range(3)) == [1.0, 2.0, 3.0]     # every recorded sample once
    assert src.reused == 0
    src.draw("a")
    assert src.reused == 1 and src.runs == 4 and src.per_commit["a"] == 4
    one, _ = ReplaySource.from_window(p, seed=7)
    two, _ = ReplaySource.from_window(p, seed=7)
    assert [one.draw("a") for _ in range(5)] == [two.draw("a") for _ in range(5)]      # seeded = reproducible
    with pytest.raises(KeyError):
        src.draw("zzz")


def test_bad_inputs():
    lv = estimate_levels([1, 1.1], [2, 2.1])
    with pytest.raises(ValueError):
        SPRT(lv, alpha=0.7)
    with pytest.raises(ValueError):
        SPRT(lv).update(0)
    with pytest.raises(ValueError):
        estimate_levels([1], [2, 3])
