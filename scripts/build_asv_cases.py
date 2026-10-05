"""Build Perfhound cases from the pandas asv-runner regression snapshot.

    python scripts/fetch_asv_regressions.py                 # once: issues -> data/asv_pandas_regressions.json
    python scripts/build_asv_cases.py                       # -> data/cases_asv_pandas_n20_seed0.jsonl

Each case: 20 real pandas main-line commits, the CI-flagged commit at a random position,
symptom = the regressed ASV benchmark (name, parameters, source code). Failures are listed.
"""

import argparse
import sys
from collections import Counter
from pathlib import Path

from perfhound.gateway.sources import open_source


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--regressions", default="data/asv_pandas_regressions.json")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--min-pct", type=float, default=10.0, help="ignore regressions smaller than this (noise)")
    ap.add_argument("--out")
    args = ap.parse_args()
    out = Path(args.out or f"data/cases_asv_pandas_n{args.n}_seed{args.seed}.jsonl")
    src = open_source("asv-runner", regressions_file=args.regressions, n=args.n, seed=args.seed, min_pct=args.min_pct)
    cases = list(src.cases())
    out.write_text("".join(c.to_json() + "\n" for c in cases), encoding="utf-8")
    print(f"{len(cases)} cases -> {out}")
    for reason, k in Counter(f["reason"] for f in src.failures).items():
        print(f"  skipped {k}: {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
