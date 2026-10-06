# Live end-to-end test with the integrated (new) gateway — 2026-10-06

`perfhound localize <case>.yaml --no-llm` on real GitHub repositories (sympy, xarray); cases in
`examples/real_cases/` (SWE-fficiency real windows: 20 real main-line commits, expert performance PR = known culprit).
Run in a Linux cloud VM (2 vCPU). LLM OFF (no Gemini key there), dense retrieval OFF (Hugging Face blocked) -> BM25 prior only.
GitHub OFF (evaluation rule: PR titles give the answer away on this dataset).

| case | repo | culprit pos | BM25 rank of culprit | result | runs (incl. 10 calibration) | tests |
|---|---|---|---|---|---|---|
| sympy-27051 prime() | sympy/sympy | 18/20 | 1 | **MATCH** (96.5 %) | 18 | 4 |
| sympy-21169 pretty printer | sympy/sympy | 3/20 | 1 | **MATCH** (96.6 %) | 18 | 4 |
| sympy-15909 | sympy/sympy | 18/20 | 2 | **MATCH** (97.4 %) | 20 | 5 |
| xarray-9001 Variable fastpath | pydata/xarray | 18/20 | 3 | **MISS**: said #8536 (pos 19), confidence 98.6 % | 26 | 7 |
| sympy-10919 partitions | sympy/sympy | 11/20 | 2 | **no answer**: good vs bad not significantly different (ratio 1.011) | 40 | 0 |

xarray-9001 diagnosis: the workload takes ~6-16 microseconds; good->bad = 16.0 -> 12.5 us (ratio 0.78). Re-measured by hand
(200 reps x 5 processes): parent 6.6-8.0 us, culprit #9001 5.7-6.4 us, next commit #8536 5.5-6.7 us -> the speed-up
does appear at #9001, but the step is inside the noise of 2 runs/test; the search called #9001 "good" twice and was
over-confident (98.6 %). Weakness: µs-scale workloads need more runs per test / a noise-aware stop rule.

sympy-10919 diagnosis: the workload does not reproduce the expert speed-up in the era-matched env (python 3.8, 2016) on
this machine; the localizer correctly refused to name a culprit instead of guessing.

## Unseen repository: networkx 3.2 -> 3.3 (not in any dataset) — `examples/networkx_regression.yaml`

Real slowdown: `nx.has_path` on a tiny graph, 4.8 -> 6.2 ms per 2000 calls (ratio 1.29). 208 first-parent commits.
Ground truth established INDEPENDENTLY of Perfhound by measuring the commits by hand (every 20th, then 141-159):
the step is at #150 `eec4012 Transmogrify _dispatchable objects into functions (#7298)` (~64 -> ~84 ms).

`perfhound localize examples/networkx_regression.yaml --no-llm` -> **CULPRIT eec4012 (#7298), confidence 99.1 %,
38 benchmark runs (10 calibration), 11 tests** — MATCH. BM25 prior was useless here (top suspects were docs/tests
commits; the benchmark text does not mention dispatching), so the probabilistic bisection did the work.
Bug found on the way and fixed: `localize` crashed when `bad` is a signed/annotated tag (`git show` printed the tag
header) -> `localize.commit_date()` peels `^{commit}`; test `test_commit_date_of_an_annotated_tag`.
