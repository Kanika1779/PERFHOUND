# RAG v2 (dense + hybrid) — retrieval results

Run on Kanika's Windows laptop (Hugging Face is blocked in the cloud/VM), after leak fix 41582b0:
`python scripts/eval_retrieval.py data/cases_real_n20_seed0.jsonl --embed-model BAAI/bge-small-en-v1.5 --embed-model jinaai/jina-embeddings-v2-base-code --out results/retrieval_v2`

68 SWE-fficiency real-change cases, 20 candidates each, query = workload script. Rank of the culprit among 20.

| method | R@1 | R@3 | R@5 | R@10 | MRR |
|---|---|---|---|---|---|
| random (expected) | 0.05 | 0.15 | 0.25 | 0.50 | 0.18 |
| largest diff first | 0.01 | 0.07 | 0.13 | 0.38 | 0.13 |
| BM25 message | 0.68 | 0.79 | 0.87 | 0.96 | 0.76 |
| BM25 functions | 0.79 | 0.91 | 0.99 | 0.99 | 0.86 |
| BM25 paths | 0.50 | 0.71 | 0.78 | 0.93 | 0.62 |
| BM25 diff | 0.54 | 0.76 | 0.85 | 0.90 | 0.68 |
| BM25 meta (message+paths+functions) | 0.85 | 0.93 | 0.97 | 0.99 | 0.90 |
| BM25 all (+diff) | 0.75 | 0.94 | 0.99 | 0.99 | 0.85 |
| dense bge-small-en-v1.5 (general) | 0.75 | 0.93 | 0.94 | 1.00 | 0.84 |
| hybrid BM25-meta + bge-small (RRF) | 0.84 | 0.94 | 1.00 | 1.00 | 0.90 |
| dense jina-embeddings-v2-base-code | 0.84 | 0.96 | 0.97 | 1.00 | 0.90 |
| **hybrid BM25-meta + jina-code (RRF)** — default | **0.85** | 0.96 | 0.99 | 1.00 | **0.91** |

## Leak fix: before vs after
Before 41582b0 the query contained the SWE-fficiency instance id (= culprit PR number). Re-run after the fix:
dense jina 0.84/0.90 and hybrid jina 0.85/0.91 are **unchanged**; dense bge 0.74→0.75 R@1. So the leak did
not inflate retrieval — but it had to be fixed before the LLM step, which would have read the number directly.
BM25 rows moved by ≤1 case (the query lost the token "dask"/"sympy" from the old name).

## What this does and does not show
- A code-trained embedder beats a general one (R@1 0.84 vs 0.75 dense). Solid.
- Hybrid vs BM25 meta: same R@1, +0.01 MRR. With 68 cases a 1–2 case difference is noise —
  **do not claim hybrid "beats" BM25 on this set.** Ceiling: BM25 alone already has R@10 0.99.
- Hybrid is still the default because BM25 and dense fail on *different* cases (e.g. dask-11625: BM25 7, dense 1;
  xarray-7374: BM25 16, dense-bge 1; xarray-5661: BM25 1, dense-jina 7). That matters more on queries that
  don't name the culprit's function — which this set mostly does.
- Bias: workloads were written for the culprit PR (60/68 call a function it changed). The unbiased number
  needs culprit-independent benchmarks (pandas asv-runner, verified) and profiler queries (2b).
