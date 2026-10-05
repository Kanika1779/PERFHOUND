"""Step 3 evaluation: does the LLM improve the prior over commits?

    python scripts/eval_prioritizer.py data/cases_real_n20_seed0.jsonl --out results/prioritizer_v1
    python scripts/eval_prioritizer.py data/cases_real_n20_seed0.jsonl --dry-run      # print one prompt, no API call

Per case, three priors over all n candidates:
  retrieval   rank-based prior from the retriever alone (hybrid BM25+jina by default; --retriever bm25 needs no model)
  llm+retr    eps/n + (1-eps)(w*LLM + (1-w)*retrieval), w = --w (default 0.7)
  llm only    w = 1 (same LLM answer, re-weighted - no extra API call)
Metrics: rank of the culprit (R@k, MRR) and bits = -log2 p(culprit). Bits is what matters for the
scheduler: an ideal weighted bisection needs about that many benchmark comparisons; uniform = log2 n.
The culprit is read from the case file ONLY for scoring.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from pathlib import Path

from perfhound.gateway import Gateway, RegressionCase
from perfhound.gateway.fetcher import RepoFetcher
from perfhound.llm import CachedLLM, GeminiClient, build_prompt, prioritize
from perfhound.llm.client import DEFAULT_MODEL
from perfhound.rag.documents import build_commit_documents, build_query
from perfhound.rag.metrics import RetrievalResult, mrr, recall_at
from perfhound.rag.retriever import rank_candidates, rank_hybrid

KS = (1, 3, 5)
META = ("message", "paths", "functions")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cases")
    ap.add_argument("--out", default="results/prioritizer_v1")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--retriever", choices=["hybrid", "bm25"], default="hybrid")
    ap.add_argument("--embed-model", default="jinaai/jina-embeddings-v2-base-code")
    ap.add_argument("--top-k", type=int, default=10)
    ap.add_argument("--w", type=float, default=0.7)
    ap.add_argument("--eps", type=float, default=0.05)
    ap.add_argument("--no-redact", action="store_true", help="ablation: keep PR/issue numbers in commit text")
    ap.add_argument("--no-message", action="store_true", help="ablation: hide commit messages from the LLM")
    ap.add_argument("--min-interval", type=float, default=4.5, help="seconds between API calls (free tier RPM)")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cases = [RegressionCase.from_json(l) for l in Path(args.cases).read_text(encoding="utf-8").splitlines() if l.strip()]
    cases = cases[: args.limit] if args.limit else cases
    fetcher = RepoFetcher()

    embedder = None
    if args.retriever == "hybrid":
        from perfhound.rag.embeddings import CachedEmbedder, FastEmbedEmbedder

        print(f"loading {args.embed_model} ...", flush=True)
        embedder = CachedEmbedder(FastEmbedEmbedder(args.embed_model))

    def rank(query, docs):
        if embedder is None:
            return rank_candidates(query, docs, fields=META)
        return rank_hybrid(query, docs, embedder)

    if args.dry_run:
        case = cases[0]
        hidden = case.for_localizer()
        cands = Gateway.for_case(hidden, fetcher=fetcher, cache=True).candidates_for(hidden)
        docs = build_commit_documents(cands)
        ranking = rank(build_query(hidden), docs)
        short = [d for d in docs if d.sha in {r.sha for r in ranking if r.rank <= args.top_k}]
        prompt, _ = build_prompt(hidden, short, do_redact=not args.no_redact, hide_message=args.no_message)
        print(prompt)
        print(f"\n[{len(prompt)} chars, roughly {len(prompt) // 4} tokens]")
        return 0

    llm = CachedLLM(GeminiClient(args.model, min_interval=args.min_interval))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    names = ("retrieval", "llm+retr", "llm only")
    res = {m: [] for m in names}
    bits = {m: [] for m in names}
    rows, failures = [], 0
    with open(out / "llm_answers.jsonl", "w", encoding="utf-8") as raw:
        for i, case in enumerate(cases, 1):
            hidden = case.for_localizer()
            cands = Gateway.for_case(hidden, fetcher=fetcher, cache=True).candidates_for(hidden)
            docs = build_commit_documents(cands)
            n = len(docs)
            ranking = rank(build_query(hidden), docs)
            kw = dict(top_k=args.top_k, eps=args.eps, do_redact=not args.no_redact, hide_message=args.no_message)
            priors = {
                "retrieval": prioritize(hidden, docs, ranking, None, w=0.0, **kw),
                "llm+retr": prioritize(hidden, docs, ranking, llm, w=args.w, **kw),
                "llm only": prioritize(hidden, docs, ranking, llm, w=1.0, **kw),
            }
            p = priors["llm+retr"]
            failures += not p.llm_ok
            row = {"case_id": case.case_id, "n": n, "llm_ok": p.llm_ok,
                   "retrieval_rank": next(r.rank for r in ranking if r.sha == case.culprit),
                   "culprit_llm_score": p.llm_scores.get(case.culprit)}
            for m in names:
                r = priors[m].rank_of(case.culprit)
                b = priors[m].bits(case.culprit)
                res[m].append(RetrievalResult(case.case_id, n, r))
                bits[m].append(b)
                row[f"{m}:rank"] = r
                row[f"{m}:bits"] = round(b, 3)
            rows.append(row)
            raw.write(json.dumps({"case_id": case.case_id, "culprit": case.culprit, "ok": p.llm_ok, "error": p.error,
                                  "scores": p.llm_scores, "reasons": p.reasons}) + "\n")
            print(f"[{i}/{len(cases)}] {case.case_id:<52} retr={row['retrieval:rank']:>2} "
                  f"llm+retr={row['llm+retr:rank']:>2} llm={row['llm only:rank']:>2} "
                  f"bits {row['retrieval:bits']:.2f} -> {row['llm+retr:bits']:.2f}" + ("" if p.llm_ok else f"  LLM FAILED: {p.error}"),
                  flush=True)

    uniform = statistics.mean(math.log2(r.n) for r in res["retrieval"])
    table = [{"method": "uniform", "recall@1": None, "mrr": None, "mean_bits": uniform}]
    for m in names:
        table.append({"method": m, **{f"recall@{k}": recall_at(res[m], k) for k in KS}, "mrr": mrr(res[m]),
                      "mean_bits": statistics.mean(bits[m])})
    wins = sum(a < b - 1e-9 for a, b in zip(bits["llm+retr"], bits["retrieval"]))
    losses = sum(a > b + 1e-9 for a, b in zip(bits["llm+retr"], bits["retrieval"]))
    usage = getattr(llm.inner, "usage", {})
    summary = {"cases": len(cases), "model": args.model, "retriever": args.retriever, "top_k": args.top_k,
               "w": args.w, "eps": args.eps, "redact": not args.no_redact, "hide_message": args.no_message, "llm_failures": failures,
               "llm+retr_vs_retrieval_bits": {"better": wins, "worse": losses, "same": len(cases) - wins - losses},
               "cache_hits": llm.hits, "api_calls": usage.get("calls"), "prompt_tokens": usage.get("prompt_tokens"),
               "output_tokens": usage.get("output_tokens"), "table": table}
    with open(out / "per_case.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(f"\n{len(cases)} cases | {args.model} | retriever={args.retriever} top-k={args.top_k} w={args.w} "
          f"{' | messages HIDDEN' if args.no_message else ''} | LLM failures: {failures}\n")
    print(f"{'method':<12}{'R@1':>7}{'R@3':>7}{'R@5':>7}{'MRR':>7}{'bits':>8}")
    for t in table:
        if t["recall@1"] is None:
            print(f"{t['method']:<12}{'':>28}{t['mean_bits']:>8.2f}")
        else:
            print(f"{t['method']:<12}" + "".join(f"{t[f'recall@{k}']:>7.2f}" for k in KS) + f"{t['mrr']:>7.2f}{t['mean_bits']:>8.2f}")
    print(f"\nllm+retr vs retrieval, per case bits: better {wins}, worse {losses}, same {len(cases) - wins - losses}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
