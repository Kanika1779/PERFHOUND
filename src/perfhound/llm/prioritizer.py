"""Combine retrieval and the LLM into a prior over EVERY candidate commit.

    R_i  = (1 / (rank_i + c)) / sum                      retrieval prior, all n commits
    L_i  = score_i / sum over the top-k                  LLM, top-k only (0 outside)
    p_i  = eps / n + (1 - eps) * (w * L_i + (1 - w) * R_i)

eps > 0 guarantees every commit keeps probability: the scheduler can still find a
culprit the retriever and LLM both missed - it just costs more benchmark runs.
If the LLM call fails, w = 0 for that case (retrieval prior) and the failure is recorded.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

from ..gateway.cases import RegressionCase
from ..rag.documents import CommitDocument
from ..rag.retriever import RankedCandidate
from .client import LLMError
from .prompt import SCHEMA, build_prompt

MIN_SCORE = 0.01   # an LLM "0" means "unlikely", not "impossible"


@dataclass
class Prior:
    probs: dict[str, float]                       # sha -> probability, sums to 1, every candidate > 0
    positions: dict[str, int]
    llm_scores: dict[str, float] = field(default_factory=dict)   # sha -> raw LLM score (top-k only)
    reasons: dict[str, str] = field(default_factory=dict)
    llm_ok: bool = False
    error: str | None = None

    def ranking(self) -> list[str]:
        """Most probable first; ties -> later commit first (same rule as retrieval)."""
        return sorted(self.probs, key=lambda s: (-self.probs[s], -self.positions[s]))

    def rank_of(self, sha: str) -> int:
        return self.ranking().index(sha) + 1

    def bits(self, sha: str) -> float:
        """-log2 p(culprit): information-theoretic cost of locating it (uniform over n = log2 n)."""
        return -math.log2(self.probs[sha])


def retrieval_prior(ranking: Sequence[RankedCandidate], *, c: float = 1.0) -> dict[str, float]:
    w = {r.sha: 1.0 / (r.rank + c) for r in ranking}
    total = sum(w.values())
    return {s: v / total for s, v in w.items()}


def prioritize(case: RegressionCase, docs: Sequence[CommitDocument], ranking: Sequence[RankedCandidate], llm, *,
               top_k: int = 10, w: float = 0.7, eps: float = 0.05, c: float = 1.0,
               do_redact: bool = True) -> Prior:
    """case must be case.for_localizer(); ranking covers every doc (from rag.retriever)."""
    n = len(ranking)
    positions = {r.sha: r.position for r in ranking}
    R = retrieval_prior(ranking, c=c)
    by_sha = {d.sha: d for d in docs}
    short = [by_sha[r.sha] for r in sorted(ranking, key=lambda r: r.rank)[:top_k]]

    llm_scores: dict[str, float] = {}
    reasons: dict[str, str] = {}
    error = None
    if llm is not None and w > 0 and short:
        prompt, aliases = build_prompt(case, short, do_redact=do_redact)
        try:
            answer = llm.complete_json(prompt, SCHEMA)
            llm_scores, reasons = _parse(answer, aliases)
        except LLMError as e:
            error = str(e)
    ok = bool(llm_scores)
    if not ok and error is None and llm is not None and w > 0:
        error = "LLM answer had no usable scores"

    L: dict[str, float] = {}
    if ok:
        vals = {s: max(llm_scores.get(s, MIN_SCORE), MIN_SCORE) for s in (d.sha for d in short)}
        total = sum(vals.values())
        L = {s: v / total for s, v in vals.items()}
    weight = w if ok else 0.0
    probs = {s: eps / n + (1 - eps) * (weight * L.get(s, 0.0) + (1 - weight) * R[s]) for s in R}
    total = sum(probs.values())
    probs = {s: p / total for s, p in probs.items()}
    return Prior(probs, positions, llm_scores, reasons, ok, error)


def _parse(answer, aliases: dict[str, str]) -> tuple[dict[str, float], dict[str, str]]:
    scores: dict[str, float] = {}
    reasons: dict[str, str] = {}
    items = answer.get("candidates", []) if isinstance(answer, dict) else []
    for it in items:
        if not isinstance(it, dict):
            continue
        sha = aliases.get(str(it.get("id", "")).strip())
        try:
            score = float(it.get("score"))
        except (TypeError, ValueError):
            continue
        if sha is None or math.isnan(score):
            continue                               # hallucinated id / garbage: ignored
        scores[sha] = min(max(score, 0.0), 1.0)
        reasons[sha] = str(it.get("reason", ""))[:300]
    return scores, reasons
