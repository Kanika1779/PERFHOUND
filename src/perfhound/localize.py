"""The whole pipeline for ONE case, live:  perfhound localize perfhound.yaml

    gateway -> RAG ranking -> LLM prior -> calibrate good/bad -> probabilistic bisection + SPRT -> culprit

Degrades gracefully instead of failing:
    no fastembed installed      -> BM25 ranking instead of hybrid
    no GEMINI_API_KEY / --no-llm -> retrieval prior only
    good and bad not different   -> "no change on this machine", no culprit is invented
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from .bench import BenchmarkRunner, EnvManager, infer_env
from .gateway import Gateway, RegressionCase
from .gateway.fetcher import RepoFetcher
from .gateway.gitcmd import run_git
from .llm import CachedLLM, GeminiClient, prioritize
from .rag.documents import build_commit_documents, build_query
from .rag.retriever import rank_candidates, rank_hybrid
from .search import LiveSource, localize
from .search.sprt import Calibration

META = ("message", "paths", "functions")


def _ranker(choice: str, log):
    if choice in ("auto", "hybrid"):
        try:
            from .rag.embeddings import CachedEmbedder, FastEmbedEmbedder

            emb = CachedEmbedder(FastEmbedEmbedder("jinaai/jina-embeddings-v2-base-code"))
            log("retrieval: hybrid (BM25 + jina-embeddings-v2-base-code)")
            return lambda q, docs: rank_hybrid(q, docs, emb)
        except Exception as e:          # fastembed missing, model download blocked ...
            if choice == "hybrid":
                raise
            log(f"retrieval: BM25 (dense model unavailable: {str(e)[:80]})")
    else:
        log("retrieval: BM25")
    return lambda q, docs: rank_candidates(q, docs, fields=META)


def _llm(choice: str, model: str | None, log):
    if choice == "none":
        log("LLM: off (--no-llm)")
        return None
    if not os.environ.get("GEMINI_API_KEY"):
        log("LLM: off (GEMINI_API_KEY not set) - retrieval prior only")
        return None
    client = GeminiClient(model) if model else GeminiClient()
    log(f"LLM: {client.model}")
    return CachedLLM(client)


def localize_case(case: RegressionCase, *, retriever: str = "auto", llm: str = "auto", model: str | None = None,
                  top_k: int = 10, w: float = 0.7, eps: float = 0.05, dry_run: bool = False, python: str | None = None,
                  runner_options: dict | None = None, log=print, github=None) -> dict:
    t_start = time.perf_counter()
    hidden = case.for_localizer()
    fetcher = RepoFetcher()
    repo = fetcher.fetch(case.repo)
    with Gateway.for_case(hidden, fetcher=fetcher, github=github) as gw:
        cands = sorted(gw.candidates_for(hidden), key=lambda c: c.position)
        if github is not None:
            st = gw.last_stats
            log(f"GitHub: PR found for {st.github_with_pr}/{len(cands)} commits ({st.github_requests} requests)")
            for w in st.github_warnings:
                log(f"  warning: {w}")
    if not cands:
        raise ValueError("no commits between good and bad")
    good = run_git(repo, "rev-parse", "--verify", case.good + "^{commit}").stdout.strip()
    log(f"{len(cands)} candidate commits between {case.good} and {case.bad}")

    def read_at_bad(path: str):
        p = run_git(repo, "show", f"{case.bad}:{path}", check=False)
        return p.stdout if p.returncode == 0 else None

    docs = build_commit_documents(cands)
    ranking = _ranker(retriever, log)(build_query(hidden, read_file=read_at_bad), docs)
    prior = prioritize(hidden, docs, ranking, _llm(llm, model, log), top_k=top_k, w=w, eps=eps)
    if prior.error:
        log(f"LLM failed, using the retrieval prior: {prior.error[:200]}")
    by_sha = {c.sha: c for c in cands}
    log("\nmost suspicious commits (prior):")
    for sha in prior.ranking()[:5]:
        c = by_sha[sha]
        why = prior.reasons.get(sha, "")
        log(f"  {prior.probs[sha]:6.1%}  {c.short_sha}  {c.subject[:60]}" + (f"\n          {why[:110]}" if why else ""))
    report = {"case": case.case_id, "repo": case.repo, "good": case.good, "bad": case.bad, "candidates": len(cands),
              "prior": {by_sha[s].sha: round(p, 5) for s, p in prior.probs.items()}, "llm_used": prior.llm_ok}
    if dry_run:
        return report

    b = case.benchmark
    if b is None:
        raise ValueError("the case has no benchmark: add one to perfhound.yaml")
    if python is None:
        if b.framework == "script" or b.params.get("python") or b.params.get("requirements"):
            date = datetime.fromisoformat(run_git(repo, "show", "-s", "--format=%cI", case.bad).stdout.strip())
            python = str(EnvManager().python_for(infer_env(case, date)))
        else:
            python = sys.executable        # command benchmarks: your environment (setup: can install into it)
    commits = [good] + [c.sha for c in cands]
    order = [prior.probs[c.sha] for c in cands]

    def on_step(event, post):
        if isinstance(event, Calibration):
            lv = event.levels
            log(f"\ncalibration: good {math.exp(lv.good):.4g} -> bad {math.exp(lv.bad):.4g} {b.unit} "
                f"(ratio {lv.ratio:.3f}, {len(event.good_samples)} runs each) "
                + ("- change confirmed" if event.changed else "- NO significant change"))
            return
        c = by_sha.get(event.commit)
        log(f"  test {event.index:>3} {c.short_sha if c else event.commit[:7]}  -> {event.verdict:<4} "
            f"({event.samples} runs{', forced' if event.forced else ''})  top suspect now {max(post):.0%}")

    with BenchmarkRunner(case, repo, python, log=lambda m: None, **(runner_options or {})) as runner:
        src = LiveSource(runner)
        res = localize(src, commits, order, on_step=on_step)
    report.update({"stopped": res.stopped, "culprit": res.culprit, "confidence": round(res.confidence, 4),
                   "runs": res.runs, "calibration_runs": res.calibration_runs, "tests": res.tests,
                   "seconds": round(time.perf_counter() - t_start, 1)})
    if res.culprit:
        c = by_sha[res.culprit]
        report["culprit_subject"] = c.subject
        log(f"\nCULPRIT: {c.sha}\n  {c.subject}\n  confidence {res.confidence:.1%}, {res.runs} benchmark runs "
            f"({res.calibration_runs} for calibration), {res.tests} tests")
        if res.stopped == "budget":
            log("  (test budget reached before the target confidence - treat as a lead, not a verdict)")
    else:
        log("\nNo culprit: good and bad do not differ significantly on this machine.")
    return report


def write_report(report: dict, path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(report, indent=2), encoding="utf-8")
