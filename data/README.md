# Perfhound data

## tasks/swefficiency_pure_python.json
69 SWE-fficiency tasks from the pure-Python repos (sympy 39, dask 19, xarray 11): instance id,
PR number, workload script, expert speedup.

- Source: github.com/swefficiency/swefficiency (Apache-2.0 / CC-BY-4.0), taken from
  `predictions/workload_generation/workload_generation.jsonl` (workloads) and the first
  `eval_reports/eval_report_*.csv` (`gold_speedup_ratio`).
- **Caveat:** these workloads are the repo's LLM-GENERATED ones (`workload_origin: "synthetic"`),
  not the official Hugging Face workloads (huggingface.co was blocked in the build environment).
  Replace with the official ones (`hf=True`) when possible and say which were used in the report.

## cases_real_n20_seed0.jsonl
67 real performance-change cases (no injection), one per task. Skipped: sympy #11789 (its PR commit
is not on the first-parent main line) and xarray #7374 (the task has no workload). Built with:

    perfhound cases swefficiency-real -o tasks_file=data/tasks/swefficiency_pure_python.json -o n=20 -o seed=0 --json --show-truth

- Each case: 20 consecutive real main-line commits; the expert optimization PR is at a random
  position 1..20 (`metadata.truth_position`); direction "faster"; ground_truth "reported".
- Checked: culprit at the stated position in every case, function-level changes found for every culprit,
  0 unparseable files.
- In 60 of 68 cases the workload literally calls a function the culprit changed (workloads were written
  for the PR) - retrieval results on this set are optimistic, see results/retrieval_v1/README.md.
- **Not yet validated by measurement** (roadmap step 4): 2 cases have an expected time change < 5 %,
  5 cases 5–20 % - likely too small to measure; other commits in a window may also move a workload.
- Repos are recorded as GitHub URLs; `Gateway.for_case()` clones them on first use.
- Contains the culprit: never feed this file to the localizer directly - use `case.for_localizer()`.
