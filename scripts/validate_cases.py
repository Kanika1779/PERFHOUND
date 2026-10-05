"""Step 4 validation: is each case's performance change MEASURABLE on this machine?

    python scripts/validate_cases.py data/cases_real_n20_seed0.jsonl --limit 1      # try one first
    python scripts/validate_cases.py data/cases_real_n20_seed0.jsonl                # all (hours; resumable)

Per case: build the era-matched env (uv --exclude-newer), then measure with ABBA interleaving
  culprit^ vs culprit   is the ground-truth change real here, in the expected direction?
  good     vs bad       does the window as a whole show it (what the localizer will see)?
A case the scheduler can't SEE cannot be localized - unmeasurable cases are reported, not hidden.
Results are appended per case to <out>/results.jsonl; re-running skips finished cases.
The culprit is used here ONLY to verify ground truth, never by the localizer.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from perfhound.bench import (BenchmarkError, BenchmarkRunner, EnvError, EnvManager, infer_env, ratio_ci,
                             verdict)
from perfhound.gateway import RegressionCase
from perfhound.gateway.fetcher import RepoFetcher
from perfhound.gateway.gitcmd import run_git


def pair_stats(samples, before, after, direction, min_effect):
    a = [s.value for s in samples[before]]
    b = [s.value for s in samples[after]]
    ratio, lo, hi = ratio_ci(a, b)
    v = verdict(ratio, lo, hi, min_effect=min_effect)
    cv = max(statistics.stdev(x) / statistics.median(x) for x in (a, b))
    return {"before_ms": statistics.median(a) * 1000, "after_ms": statistics.median(b) * 1000,
            "ratio": ratio, "ci": [lo, hi], "verdict": v, "matches": v == direction, "noise_cv": cv}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cases")
    ap.add_argument("--out", default="results/bench_validation")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--case", help="only cases whose id contains this text")
    ap.add_argument("--rounds", type=int, default=5, help="ABBA rounds per pair (2 samples per commit per round)")
    ap.add_argument("--repeat", type=int, default=5, help="workload repetitions inside one process")
    ap.add_argument("--timeout", type=float, default=900)
    ap.add_argument("--min-effect", type=float, default=0.05)
    ap.add_argument("--no-endpoints", action="store_true", help="skip good vs bad")
    args = ap.parse_args()

    cases = [RegressionCase.from_json(l) for l in Path(args.cases).read_text(encoding="utf-8").splitlines() if l.strip()]
    if args.case:
        cases = [c for c in cases if args.case in c.case_id]
    cases = cases[: args.limit] if args.limit else cases
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    results_path = out / "results.jsonl"
    done = set()
    if results_path.exists():
        done = {json.loads(l)["case_id"] for l in results_path.read_text(encoding="utf-8").splitlines() if l.strip()}

    fetcher, envs = RepoFetcher(), EnvManager()
    for i, case in enumerate(cases, 1):
        if case.case_id in done:
            print(f"[{i}/{len(cases)}] {case.case_id}: already done, skipping")
            continue
        print(f"[{i}/{len(cases)}] {case.case_id}", flush=True)
        rec = {"case_id": case.case_id, "direction": case.direction,
               "expected_ratio": (1 / case.metadata["truth_expert_speedup"]) if case.metadata.get("truth_expert_speedup") else None}
        t0 = time.perf_counter()
        try:
            if not (case.benchmark and case.benchmark.workload):
                raise BenchmarkError("no workload in the case")
            repo = fetcher.fetch(case.repo)
            date = datetime.fromisoformat(run_git(repo, "show", "-s", "--format=%cI", case.bad).stdout.strip())
            spec = infer_env(case, date)
            rec["env"] = {"python": spec.python, "requirements": list(spec.requirements), "as_of": spec.exclude_newer}
            python = envs.python_for(spec)
            parent = run_git(repo, "rev-parse", case.culprit + "^1").stdout.strip()
            with BenchmarkRunner(case, repo, python, inner_repeat=args.repeat, timeout=args.timeout,
                                 log=lambda m: print(m, flush=True)) as runner:
                print(f"  culprit^ vs culprit ({args.rounds} ABBA rounds)", flush=True)
                s = runner.measure_interleaved(parent, case.culprit, args.rounds)
                rec["culprit_pair"] = pair_stats(s, parent, case.culprit, case.direction, args.min_effect)
                rec["python_used"] = s[parent][0].python
                if not args.no_endpoints:
                    print(f"  good vs bad ({args.rounds} ABBA rounds)", flush=True)
                    s = runner.measure_interleaved(case.good, case.bad, args.rounds)
                    rec["window_pair"] = pair_stats(s, case.good, case.bad, case.direction, args.min_effect)
                rec["processes"] = runner.runs
            ok = rec["culprit_pair"]["matches"] and (args.no_endpoints or rec["window_pair"]["matches"])
            rec["status"] = "measurable" if ok else "not_measurable"
        except EnvError as e:
            rec["status"], rec["error"] = "env_failed", str(e)[-2000:]
        except BenchmarkError as e:
            rec["status"], rec["error"] = "bench_failed", str(e)[-2000:]
        rec["seconds"] = round(time.perf_counter() - t0, 1)
        with open(results_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
        cp = rec.get("culprit_pair")
        detail = (f"ratio {cp['ratio']:.3f} [{cp['ci'][0]:.3f}, {cp['ci'][1]:.3f}] expected "
                  f"{rec['expected_ratio']:.3f}" if cp and rec["expected_ratio"] else "")
        print(f"  => {rec['status']} {detail} ({rec['seconds']:.0f} s)" + (f"\n  {rec['error'][:600]}" if "error" in rec else ""),
              flush=True)

    rows = [json.loads(l) for l in results_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    print(f"\n{len(rows)} cases validated: " + ", ".join(f"{k} {v}" for k, v in Counter(r["status"] for r in rows).items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
