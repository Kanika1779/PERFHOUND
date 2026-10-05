"""Record every commit of every case window, so search strategies can be REPLAYED offline.

    python scripts/measure_windows.py data/cases_real_n20_seed0.jsonl --probe        # 1 process per case: cost table
    python scripts/measure_windows.py data/cases_real_n20_seed0.jsonl --max-process 30 # record cases whose process <= 30 s

Why record & replay: one benchmark sample can cost 45 s (dask-5553: setup() builds a
1M x 200 DataFrame before each repeat). Running git bisect, Perfhound and every baseline
LIVE on every case would take weeks. Instead every commit in the window (good + 20
candidates) gets the same number of samples, collected round-robin in a shuffled order
each round (slow drift spreads over all commits instead of hitting one). Strategies then
draw from these recorded samples: same real noise for every method -> fair comparison,
and re-running an experiment costs seconds. Live mode stays for demos.

Output: <out>/<case>.json, rewritten after every round (resumable: re-run continues).
The culprit is stored for SCORING only; replay code must not show it to strategies.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import datetime
from pathlib import Path

from perfhound.bench import BenchmarkError, BenchmarkRunner, EnvError, EnvManager, audit_workload, infer_env
from perfhound.gateway import Gateway, RegressionCase
from perfhound.gateway.fetcher import RepoFetcher
from perfhound.gateway.gitcmd import run_git


def safe_name(case_id: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in case_id)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cases")
    ap.add_argument("--out", default="results/windows")
    ap.add_argument("--samples", type=int, default=6, help="samples per commit (rounds)")
    ap.add_argument("--repeat", type=int, default=5)
    ap.add_argument("--timeout", type=float, default=900)
    ap.add_argument("--case")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--probe", action="store_true", help="only time ONE process per case (at bad), write probe.jsonl")
    ap.add_argument("--max-process", type=float, help="skip cases whose probed process time exceeds this (seconds)")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    cases = [RegressionCase.from_json(l) for l in Path(args.cases).read_text(encoding="utf-8").splitlines() if l.strip()]
    cases = [c for c in cases if audit_workload(c.benchmark.workload if c.benchmark else None)[0] == "clean"]
    if args.case:
        cases = [c for c in cases if args.case in c.case_id]
    probe_path = out / "probe.jsonl"
    probes = {}
    if probe_path.exists():
        for l in probe_path.read_text(encoding="utf-8").splitlines():
            if l.strip():
                r = json.loads(l)
                if "process_s" in r or r["case_id"] not in probes:
                    probes[r["case_id"]] = r          # a later success replaces an earlier failure
    if not args.probe and probes:
        cases.sort(key=lambda c: probes.get(c.case_id, {}).get("process_s") or 1e9)   # cheapest first
        if args.max_process is not None:
            cases = [c for c in cases if (probes.get(c.case_id, {}).get("process_s") or 1e9) <= args.max_process]
    cases = cases[: args.limit] if args.limit else cases
    fetcher, envs = RepoFetcher(), EnvManager()
    print(f"{len(cases)} clean cases selected", flush=True)

    for i, case in enumerate(cases, 1):
        if args.probe and "process_s" in probes.get(case.case_id, {}):
            continue                                  # failed probes are retried
        dest = out / (safe_name(case.case_id) + ".json")
        rec = json.loads(dest.read_text(encoding="utf-8")) if dest.exists() and not args.probe else None
        if rec and rec.get("complete"):
            print(f"[{i}/{len(cases)}] {case.case_id}: complete, skipping")
            continue
        print(f"[{i}/{len(cases)}] {case.case_id}", flush=True)
        t0 = time.perf_counter()
        try:
            repo = fetcher.fetch(case.repo)
            date = datetime.fromisoformat(run_git(repo, "show", "-s", "--format=%cI", case.bad).stdout.strip())
            spec = infer_env(case, date)
            python = envs.python_for(spec)
            if args.probe:
                with BenchmarkRunner(case, repo, python, inner_repeat=args.repeat, timeout=args.timeout, warmup=0) as r:
                    s = r.run_once(case.bad)
                row = {"case_id": case.case_id, "process_s": round(s.wall, 1), "value_ms": round(s.value * 1000, 3),
                       "est_hours": round(s.wall * 21 * (args.samples + 1) / 3600, 2)}
                print(f"  process {s.wall:.1f} s -> about {row['est_hours']} h for {args.samples} samples x 21 commits", flush=True)
            else:
                hidden = case.for_localizer()
                cands = Gateway.for_case(hidden, fetcher=fetcher, cache=True).candidates_for(hidden)
                commits = [case.good] + [c.sha for c in sorted(cands, key=lambda c: c.position)]
                if rec is None:
                    rec = {"case_id": case.case_id, "direction": case.direction, "commits": commits,
                           "culprit_index": commits.index(case.culprit),      # scoring only
                           "env": {"python": spec.python, "requirements": list(spec.requirements), "as_of": spec.exclude_newer},
                           "repeat": args.repeat, "samples": {c: [] for c in commits}, "wall": {c: [] for c in commits},
                           "complete": False}
                rng = random.Random(f"{case.case_id}:{len(rec['samples'][commits[0]])}")
                with BenchmarkRunner(case, repo, python, inner_repeat=args.repeat, timeout=args.timeout,
                                     max_worktrees=len(commits)) as r:
                    while min(len(v) for v in rec["samples"].values()) < args.samples:
                        rnd = min(len(v) for v in rec["samples"].values()) + 1
                        order = [c for c in commits if len(rec["samples"][c]) < rnd]
                        rng.shuffle(order)
                        t_round = time.perf_counter()
                        for c in order:
                            s = r.run_once(c)
                            rec["samples"][c].append(s.value)
                            rec["wall"][c].append(round(s.wall, 2))
                        rec["python_used"] = s.python
                        dest.write_text(json.dumps(rec), encoding="utf-8")
                        print(f"  round {rnd}/{args.samples} done ({time.perf_counter() - t_round:.0f} s)", flush=True)
                rec["complete"] = True
                dest.write_text(json.dumps(rec), encoding="utf-8")
                ci = rec["culprit_index"]
                med = lambda v: sorted(v)[len(v) // 2]
                print(f"  culprit {med(rec['samples'][commits[ci - 1]]) * 1000:.2f} ms -> "
                      f"{med(rec['samples'][commits[ci]]) * 1000:.2f} ms", flush=True)
                continue
        except (EnvError, BenchmarkError) as e:
            row = {"case_id": case.case_id, "error": str(e)[-1500:]}
            print(f"  FAILED: {str(e)[:500]}", flush=True)
            if not args.probe:
                (out / (safe_name(case.case_id) + ".error.txt")).write_text(str(e), encoding="utf-8")
                continue
        with open(probe_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        probes[case.case_id] = row
    if args.probe:
        ok = [p for p in probes.values() if "process_s" in p]
        print(f"\nprobed {len(ok)} cases, {len(probes) - len(ok)} failed; total estimate "
              f"{sum(p['est_hours'] for p in ok):.1f} h for {args.samples} samples per commit")
        for p in sorted(ok, key=lambda p: p["process_s"]):
            print(f"  {p['process_s']:7.1f} s  {p['est_hours']:6.2f} h  {p['case_id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
