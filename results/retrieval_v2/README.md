# RAG v2 (dense + hybrid) — retrieval results

Run on Kanika's Windows laptop (Hugging Face is blocked in the cloud/VM):
`python scripts/eval_retrieval.py data/cases_real_n20_seed0.jsonl --embed-model BAAI/bge-small-en-v1.5 --embed-model jinaai/jina-embeddings-v2-base-code --out results/retrieval_v2`

> **Superseded until re-run:** the numbers below were measured BEFORE fix 41582b0. The query then contained the
> benchmark name = SWE-fficiency instance id, i.e. the culprit PR number ("dask__dask-10356" vs "(#10356)" in the
> commit message). BM25 rows are unaffected (digits are dropped by the tokenizer); dense/hybrid rows may be inflated.

68 SWE-fficiency real-change cases, 20 candidates each, query = workload script. Rank of the culprit among 20.
(v1 reported 67 cases: xarray-7374 was missing from that run. It is in the committed cases file; its BM25-meta
rank is 2, which is why BM25 meta R@1 reads 0.85 here vs 0.87 in v1. Same method, one more case.)

| method | R@1 | R@3 | R@5 | R@10 | MRR |
|---|---|---|---|---|---|
| random (expected) | 0.05 | 0.15 | 0.25 | 0.50 | 0.18 |
| largest diff first | 0.01 | 0.07 | 0.13 | 0.38 | 0.13 |
| BM25 message | 0.68 | 0.79 | 0.87 | 0.96 | 0.76 |
| BM25 functions | 0.81 | 0.93 | 1.00 | 1.00 | 0.87 |
| BM25 paths | 0.51 | 0.72 | 0.79 | 0.94 | 0.64 |
| BM25 diff | 0.54 | 0.74 | 0.84 | 0.90 | 0.67 |
| BM25 meta (message+paths+functions) | 0.85 | 0.94 | 0.99 | 1.00 | 0.91 |
| BM25 all (+diff) | 0.75 | 0.96 | 1.00 | 1.00 | 0.86 |
| dense bge-small-en-v1.5 (general) | 0.74 | 0.88 | 0.96 | 0.99 | 0.82 |
| hybrid BM25-meta + bge-small (RRF) | 0.82 | 0.96 | 0.99 | 1.00 | 0.89 |
| dense jina-embeddings-v2-base-code | 0.84 | 0.97 | 0.97 | 1.00 | 0.90 |
| **hybrid BM25-meta + jina-code (RRF)** — default | **0.85** | 0.96 | **1.00** | 1.00 | **0.91** |

## What this does and does not show
- A code-trained embedder beats a general one by a wide margin (R@1 0.84 vs 0.74). That finding is solid.
- Hybrid vs BM25 meta: identical R@1, +0.01 R@5/MRR. With 68 cases a 1–2 case difference is noise —
  **do not claim hybrid "beats" BM25 on this set.** The set has a ceiling: BM25 alone already has R@5 0.99.
- Why hybrid is still the default: BM25 and dense fail on *different* cases (complementary errors), e.g.
  dense rescues dask-11625 (7→1), sympy-23696 (4→1), xarray-4740/7472; BM25 rescues xarray-5661, sympy-15909.
  That matters more on queries that don't name the culprit's function — which this set mostly does.
- Same bias as v1: workloads were written for the culprit PR (60/68 call a function it changed). The
  unbiased number needs culprit-independent benchmarks (pandas asv-runner, verified) and profiler queries (2b).
