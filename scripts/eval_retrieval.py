"""RAG v1 evaluation: where does the culprit rank among the candidates?

    python scripts/eval_retrieval.py data/cases_real_n20_seed0.jsonl --out results/retrieval_v1

Methods
  random        expected value of a random order (the floor)
  largest_diff  biggest change first (a cheap heuristic a reviewer would try)
  bm25:<fields> BM25 between the benchmark/workload and commit documents

Culprit is read from the case file ONLY to score the result; the ranking
itself sees case.for_localizer() and the candidates.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from perfhound.gateway import Gateway, RegressionCase
from perfhound.gateway.cache import Cache
from perfhound.gateway.fetcher import RepoFetcher
from perfhound.rag.documents import build_commit_documents, build_query
from perfhound.rag.metrics import RetrievalResult, mrr, random_mrr, random_recall_at, recall_at
from perfhound.rag.retriever import rank_candidates

FIELD_SETS = {
    "message": ("message",),
    "functions": ("functions",),
    "paths": ("paths",),
    "diff": ("diff",),
    "meta": ("message", "paths", "functions"),
    "all": ("message", "paths", "functions", "diff"),
}
KS = (1, 3, 5, 10)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cases")
    ap.add_argument("--out", default="results/retrieval_v1")
    ap.add_argument("--repos", default=None, help="RepoFetcher base dir")
    ap.add_argument("--cache", default=None)
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()

    cases = [RegressionCase.from_json(l) for l in Path(args.cases).read_text(encoding="utf-8").splitlines() if l.strip()]
    cases = cases[: args.limit] if args.limit else cases
    fetcher = RepoFetcher(args.repos) if args.repos else RepoFetcher()
    cache = Cache(args.cache) if args.cache else True

    results: dict[str, list[RetrievalResult]] = {m: [] for m in ["largest_diff", *[f"bm25:{f}" for f in FIELD_SETS]]}
    rows = []
    for case in cases:
        hidden = case.for_localizer()
        gw = Gateway.for_case(hidden, fetcher=fetcher, cache=cache)
        cands = gw.candidates_for(hidden)
        query = build_query(hidden)
        docs = build_commit_documents(cands)   # all candidates are <= bad by construction
        n = len(cands)
        row = {"case_id": case.case_id, "n": n, "truth_position": case.metadata.get("truth_position")}

        size_order = sorted(cands, key=lambda c: (-sum((f.additions or 0) + (f.deletions or 0) for f in c.files), -c.position))
        rank = [c.sha for c in size_order].index(case.culprit) + 1
        results["largest_diff"].append(RetrievalResult(case.case_id, n, rank))
        row["largest_diff"] = rank
        for name, fields in FIELD_SETS.items():
            ranked = rank_candidates(query, docs, fields=fields)
            rank = next(r.rank for r in ranked if r.sha == case.culprit)
            results[f"bm25:{name}"].append(RetrievalResult(case.case_id, n, rank))
            row[f"bm25:{name}"] = rank
        rows.append(row)
        print(f"{case.case_id:<55} " + " ".join(f"{k}={v}" for k, v in row.items() if k.startswith(("bm25:all", "largest"))),
              flush=True)

    any_method = next(iter(results.values()))
    table = [{"method": "random (expected)", **{f"recall@{k}": random_recall_at(any_method, k) for k in KS},
              "mrr": random_mrr(any_method)}]
    for method, res in results.items():
        table.append({"method": method, **{f"recall@{k}": recall_at(res, k) for k in KS}, "mrr": mrr(res)})

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "per_case.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    (out / "summary.json").write_text(json.dumps({"cases": len(cases), "table": table}, indent=2), encoding="utf-8")

    print(f"\n{len(cases)} cases, {any_method[0].n} candidates each (culprit known)\n")
    head = f"{'method':<20}" + "".join(f"{'R@' + str(k):>8}" for k in KS) + f"{'MRR':>8}"
    print(head)
    print("-" * len(head))
    for t in table:
        print(f"{t['method']:<20}" + "".join(f"{t[f'recall@{k}']:>8.2f}" for k in KS) + f"{t['mrr']:>8.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
