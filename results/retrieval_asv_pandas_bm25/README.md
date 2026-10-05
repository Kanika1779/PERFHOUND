# Retrieval on REAL regressions — pandas asv-runner (BM25 only)

Cases: `data/cases_asv_pandas_n20_seed0.jsonl` (102), built by `scripts/build_asv_cases.py` from
`data/asv_pandas_regressions.json` (108 asv-runner issues, Dec 2024 – Sep 2026, snapshot 2026-10-05;
6 regressions < 10 % skipped). Each case: 20 consecutive pandas main-line commits, the CI-flagged
commit at a random position; query = regressed ASV benchmark name + parameters + benchmark class source.

| method | R@1 | R@3 | R@5 | R@10 | MRR |
|---|---|---|---|---|---|
| random (expected) | 0.05 | 0.15 | 0.25 | 0.50 | 0.18 |
| largest diff first | 0.14 | 0.36 | 0.48 | 0.75 | 0.31 |
| BM25 message | 0.23 | 0.40 | 0.50 | 0.72 | 0.37 |
| BM25 functions | 0.29 | 0.52 | 0.63 | 0.75 | 0.45 |
| BM25 paths | 0.30 | 0.52 | 0.63 | 0.79 | 0.46 |
| BM25 diff | 0.32 | 0.53 | 0.62 | 0.80 | 0.47 |
| BM25 meta (message+paths+functions) | 0.33 | 0.52 | 0.64 | 0.75 | 0.48 |
| **BM25 all (+diff)** | **0.39** | 0.58 | 0.62 | 0.82 | **0.52** |

## Compared with SWE-fficiency (same method, same window size)
BM25 meta R@1: SWE-fficiency 0.85 vs pandas real regressions **0.33**. SWE-fficiency workloads were
written FOR the culprit (and culprits are deliberate optimizations); pandas' ASV suite was written
independently and these are regressions found by CI. **This is the realistic setting** — SWE-fficiency
is an upper bound. Unlike SWE-fficiency, adding the diff HELPS here (0.33 -> 0.39): regressions are
often in code the benchmark reaches indirectly, which only the diff mentions.

## Caveats
- Ground truth = the commit asv-runner flagged. asv-runner benchmarks every main commit, but a skipped
  run would make the true culprit an earlier commit (label noise; not yet verified by re-running).
- "largest diff first" beats random here (0.14) — regressions come with bigger changes than optimizations.
- Many cases are from 2026, after the LLM's likely training data: the Gemini prior on these is a
  contamination-free test. Next: hybrid + Gemini prior on these cases (Windows: HF model + key).
- Computed in the device VM (BM25 needs no model); pandas clone: blob-less, 57 MB.
