"""Look inside one case: every repetition's time at culprit^ and culprit, and what each statistic says.

    python scripts/inspect_case.py data/cases_real_n20_seed0.jsonl sympy-21455 [--processes 3] [--repeat 5]

Use it when a recorded window shows no change where the dataset promises one: it separates
"the workload does not exercise the change" from "our statistic hides it" (e.g. cache hits after
the first, cold repetition). The culprit is read here only to show the ground-truth pair.
"""

import argparse
import statistics
import sys
from datetime import datetime
from pathlib import Path

from perfhound.bench import BenchmarkRunner, EnvManager, infer_env
from perfhound.gateway import RegressionCase
from perfhound.gateway.fetcher import RepoFetcher
from perfhound.gateway.gitcmd import run_git


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cases")
    ap.add_argument("case", help="text contained in the case id, e.g. sympy-21455")
    ap.add_argument("--processes", type=int, default=3)
    ap.add_argument("--repeat", type=int, default=5)
    args = ap.parse_args()
    cases = [RegressionCase.from_json(l) for l in Path(args.cases).read_text(encoding="utf-8").splitlines() if l.strip()]
    case = next((c for c in cases if args.case in c.case_id), None)
    if case is None:
        raise SystemExit(f"no case matches {args.case!r}")
    repo = RepoFetcher().fetch(case.repo)
    date = datetime.fromisoformat(run_git(repo, "show", "-s", "--format=%cI", case.bad).stdout.strip())
    python = EnvManager().python_for(infer_env(case, date))
    parent = run_git(repo, "rev-parse", case.culprit + "^1").stdout.strip()
    print(f"{case.case_id}  (direction {case.direction})")
    stats = {}
    with BenchmarkRunner(case, repo, python, inner_repeat=args.repeat, warmup=1) as r:
        for label, sha in (("culprit^", parent), ("culprit ", case.culprit)):
            procs = [r.run_once(sha).times for _ in range(args.processes)]
            for i, t in enumerate(procs, 1):
                print(f"  {label} process {i}: " + "  ".join(f"{x * 1000:9.4f}" for x in t) + "   ms per repetition")
            stats[label] = {k: statistics.median(f(t) for t in procs) for k, f in
                            {"first": lambda v: v[0], "mean": statistics.mean, "median": statistics.median}.items()}
    print("\n  statistic   culprit^ ms   culprit ms   ratio")
    for k in ("first", "mean", "median"):
        a, b = stats["culprit^"][k], stats["culprit "][k]
        print(f"  {k:<9} {a * 1000:12.4f} {b * 1000:12.4f}   {b / a:6.3f}")
    print("\n  ratio ~1.0 under every statistic -> the workload does not show this change on this machine.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
