# RAG v1 (BM25) — retrieval results

`python scripts/eval_retrieval.py data/cases_real_n20_seed0.jsonl --out results/retrieval_v1`

67 SWE-fficiency real-change cases (xarray-7374 missing from this run; see v2 README) (sympy 38, dask 19, xarray 10), 20 candidates each, culprit = expert
optimization PR at a random position. Query = the task's workload script. Rank of the culprit among 20.

| method | R@1 | R@3 | R@5 | R@10 | MRR |
|---|---|---|---|---|---|
| random (expected) | 0.05 | 0.15 | 0.25 | 0.50 | 0.18 |
| largest diff first | 0.01 | 0.06 | 0.12 | 0.37 | 0.13 |
| BM25 message | 0.69 | 0.81 | 0.88 | 0.97 | 0.77 |
| BM25 functions | 0.81 | 0.93 | 1.00 | 1.00 | 0.87 |
| BM25 paths | 0.51 | 0.72 | 0.79 | 0.94 | 0.63 |
| BM25 diff | 0.55 | 0.75 | 0.85 | 0.91 | 0.68 |
| **BM25 meta** (message+paths+functions) | **0.87** | 0.94 | 0.99 | 1.00 | **0.91** |
| BM25 all (+diff) | 0.76 | 0.96 | 1.00 | 1.00 | 0.87 |

## Read this before quoting the numbers (they are OPTIMISTIC)
- The workloads were written FOR the optimization PR (and these are the LLM-generated "synthetic" ones).
  In 60/68 cases the workload literally calls a function the culprit changed, so BM25 mostly matches a name.
  A real CI benchmark is written without knowing the culprit and often reaches the slow code indirectly
  (deep in the call chain) - expect much lower recall there. This motivates v2's profiler-based query.
- The merge/squash commit message carries the PR title/branch name, which often names the optimized code
  (message-only R@1 = 0.69).
- Adding the raw diff hurts R@1 (0.87 -> 0.76): diff text is long and noisy; function names are the signal.
- "largest diff first" is worse than random: performance PRs are usually small.
- Unbiased evaluation needs benchmarks independent of the culprit: pandas asv-runner cases (roadmap).
