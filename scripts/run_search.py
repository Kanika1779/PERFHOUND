"""Step 7: replay every search strategy on the recorded windows - the main experiment.

    python scripts/eval_prioritizer.py data/cases_real_n20_seed0.jsonl --dump-priors results/priors.jsonl
    python scripts/run_search.py results/windows --priors results/priors.jsonl --repeats 50

Methods (all pay the same calibration of good/bad; cost = every benchmark run):
  bisect1        git bisect, 1 run per step, hard updates, uniform        (what people do)
  bisect5        git bisect, median of 5 runs per step, hard, uniform     (careful people)
  pba_uniform    probabilistic bisection + SPRT, no prior                 (our statistics alone)
  pba_retrieval  + RAG prior                                              (ablation)
  perfhound      + RAG + LLM prior                                        (full system)
Each method runs `--repeats` times per case with different replay seeds (different draws from
the recorded samples). The culprit index is read ONLY to score.
Replay caveat, reported: when a method needs more samples of a commit than were recorded,
samples are reused ("reuse %"). High reuse flatters that method - record more if it is high.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from pathlib import Path

from perfhound.search import FixedTester, ReplaySource, SPRTTester, localize

METHODS = {
    "bisect1": dict(tester=lambda: FixedTester(1), update="hard", prior=None),
    "bisect5": dict(tester=lambda: FixedTester(5), update="hard", prior=None),
    "pba_uniform": dict(tester=lambda: SPRTTester(), update="soft", prior=None),
    "pba_retrieval": dict(tester=lambda: SPRTTester(), update="soft", prior="retrieval"),
    "perfhound": dict(tester=lambda: SPRTTester(), update="soft", prior="llm+retr"),
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("windows", help="folder written by scripts/measure_windows.py")
    ap.add_argument("--priors", help="JSONL from eval_prioritizer.py --dump-priors")
    ap.add_argument("--repeats", type=int, default=50)
    ap.add_argument("--out", default="results/search_v1")
    ap.add_argument("--stat", choices=["mean", "median", "first", "min"], default="mean",
                    help="per-process statistic recomputed from raw times (primary: mean = SWE-fficiency's definition)")
    args = ap.parse_args()

    priors = {}
    if args.priors:
        for l in Path(args.priors).read_text(encoding="utf-8").splitlines():
            if l.strip():
                r = json.loads(l)
                priors[r["case_id"]] = r
    windows = []
    for p in sorted(Path(args.windows).glob("*.json")):
        if p.name == "probe.jsonl":
            continue
        rec = json.loads(p.read_text(encoding="utf-8"))
        if rec.get("complete"):
            if "times" in rec:
                import statistics as st
                f = {"mean": st.mean, "median": st.median, "first": lambda v: v[0], "min": min}[args.stat]
                rec["samples"] = {c: [f(t) for t in ts] for c, ts in rec["times"].items()}
            elif args.stat != "median":
                print(f"  {rec['case_id']}: old recording without raw times - skipped")
                continue
            windows.append(rec)
    if not windows:
        print("no complete windows found - run scripts/measure_windows.py first")
        return 1
    methods = {m: c for m, c in METHODS.items() if c["prior"] is None or priors}
    print(f"{len(windows)} recorded cases, {args.repeats} replays each, stat={args.stat}, methods: {', '.join(methods)}", flush=True)

    rows = []
    for w in windows:
        commits, truth = w["commits"], w["culprit_index"]
        pr = priors.get(w["case_id"])
        if pr and pr["candidates"] != commits[1:]:
            print(f"  {w['case_id']}: prior commit order differs from the window - skipped for prior methods")
            pr = None
        for m, cfg in methods.items():
            if cfg["prior"] and pr is None:
                continue
            prior = pr[cfg["prior"]] if cfg["prior"] else None
            for rep in range(args.repeats):
                src = ReplaySource(w["samples"], seed=f"{w['case_id']}:{rep}")
                r = localize(src, commits, prior, tester=cfg["tester"](), update=cfg["update"])
                rows.append({"case_id": w["case_id"], "method": m, "rep": rep, "correct": r.index == truth,
                             "no_change": r.stopped == "no_change", "runs": r.runs, "calibration_runs": r.calibration_runs,
                             "tests": r.tests, "reused": src.reused, "confidence": round(r.confidence, 4)})
        print(f"  {w['case_id']}: done", flush=True)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "runs.csv", "w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=list(rows[0]))
        wr.writeheader()
        wr.writerows(rows)
    table = []
    for m in methods:
        rs = [r for r in rows if r["method"] == m]
        if not rs:
            continue
        cases = sorted({r["case_id"] for r in rs})
        table.append({"method": m, "cases": len(cases), "accuracy": statistics.mean(r["correct"] for r in rs),
                      "no_change": statistics.mean(r["no_change"] for r in rs),
                      "mean_runs": statistics.mean(r["runs"] for r in rs),
                      "median_runs": statistics.median(r["runs"] for r in rs),
                      "mean_tests": statistics.mean(r["tests"] for r in rs),
                      "reuse_pct": 100 * sum(r["reused"] for r in rs) / max(1, sum(r["runs"] for r in rs))})
    (out / "summary.json").write_text(json.dumps({"cases": len(windows), "repeats": args.repeats, "stat": args.stat, "table": table}, indent=2),
                                      encoding="utf-8")
    print(f"\n{'method':<15}{'cases':>6}{'accuracy':>10}{'no-change':>11}{'mean runs':>11}{'median':>8}{'tests':>7}{'reuse %':>9}")
    for t in table:
        print(f"{t['method']:<15}{t['cases']:>6}{t['accuracy']:>10.3f}{t['no_change']:>11.3f}{t['mean_runs']:>11.1f}"
              f"{t['median_runs']:>8.0f}{t['mean_tests']:>7.1f}{t['reuse_pct']:>9.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
