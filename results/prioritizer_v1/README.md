# Step 3 — LLM prioritizer results

`python scripts/eval_prioritizer.py data/cases_real_n20_seed0.jsonl` (main) and
`... --no-message --out results/prioritizer_v1_nomsg` (ablation). Windows laptop, 2026-10-05.

Model gemini-3.5-flash-lite (temperature 0), retriever = hybrid BM25+jina, top-k 10 shown to the LLM in
chronological order with neutral ids, PR numbers/SHAs redacted, prior p = eps/n + (1-eps)(w·LLM + (1-w)·retrieval),
w = 0.7, eps = 0.05. 68 cases × 20 candidates. LLM failures: 0. ~470k prompt tokens per full run (free tier).

**bits** = −log2 p(culprit). An ideal weighted bisection needs about this many benchmark comparisons;
uniform over 20 commits = log2 20 = 4.32.

| prior | R@1 | R@3 | R@5 | MRR | mean bits |
|---|---|---|---|---|---|
| uniform (plain bisection) | – | – | – | – | 4.32 |
| retrieval only | 0.85 | 0.96 | 0.99 | 0.91 | 2.60 |
| **retrieval + LLM (w 0.7)** | **1.00** | 1.00 | 1.00 | **1.00** | **0.78** |
| LLM only (w 1) | 1.00 | 1.00 | 1.00 | 1.00 | 0.40 |
| *ablation: commit messages hidden from LLM* | | | | | |
| retrieval + LLM | 0.97 | 1.00 | 1.00 | 0.99 | 0.85 |
| LLM only | 0.99 | 1.00 | 1.00 | 0.99 | 0.48 |

retrieval+LLM has lower bits than retrieval alone in **68/68** cases (both runs).

## Findings
1. The LLM adds signal beyond retrieval: every case where retrieval missed rank 1 (10 cases, e.g. xarray-7374
   rank 9 → 1, sympy-15909 rank 5 → 1) is fixed.
2. **It reads the code, not the commit message.** Hiding messages costs ~2 cases at rank 1 and +0.07 bits.
   The reasons in `llm_answers.jsonl` cite concrete code changes ("vectorizing random state generation",
   "id-based seen set"), never a PR identity.
3. LLM-only beats the 0.7 blend in bits. **w is NOT tuned on this set** — doing so would fit an easy dataset.

## Why this is an UPPER BOUND, not the headline result
- Workloads were written for the culprit (60/68 call a function it changed) — a direct lexical bridge.
- Culprits are deliberate optimization PRs and the prompt says "it got faster": among ~10 commits one rewrites
  the hot path for speed. A real regression is a side effect inside an innocent-looking commit.
- Contamination: sympy/dask/xarray are public; the model may have seen these PRs. Redaction and the
  no-message ablation reduce but cannot exclude this.
- Next: culprit-independent benchmarks with real regressions (pandas asv-runner, verified by benchmarking).
