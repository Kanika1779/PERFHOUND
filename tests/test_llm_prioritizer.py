import math
from datetime import datetime, timezone

import pytest

from perfhound.gateway.cases import BenchmarkSpec, RegressionCase
from perfhound.llm import CachedLLM, GeminiClient, LLMError, build_prompt, prioritize, redact, retrieval_prior
from perfhound.llm.client import _retry_after
from perfhound.rag.documents import CommitDocument
from perfhound.rag.retriever import RankedCandidate

T = datetime(2024, 1, 1, tzinfo=timezone.utc)
SHAS = [f"{i:x}" * 40 for i in range(1, 7)]           # 6 commits, positions 0..5


def doc(i, message, functions="", diff=""):
    return CommitDocument(SHAS[i][:40], i, T, {"message": message, "paths": f"pkg/m{i}.py",
                                              "functions": functions, "diff": diff})


DOCS = [doc(0, "Docs typo"), doc(1, "Merge pull request #4242 from alice/fast-sum\n\nSpeed up sum", "pkg.core.sum"),
        doc(2, "CI: bump action"), doc(3, "Refactor parser (#77)", "pkg.parse.parse"),
        doc(4, "Add tests"), doc(5, f"Revert {SHAS[1][:12]}")]
CASE = RegressionCase(case_id="swefficiency-real:org__pkg-4242__real_n6_k02", source="t", repo="https://github.com/org/pkg",
                      language="python", good="0" * 40, bad=SHAS[5], culprit=SHAS[1], ground_truth="reported",
                      direction="faster", benchmark=BenchmarkSpec(name="workload", framework="script",
                                                                    workload="from pkg.core import sum\nsum(range(10))"))
HIDDEN = CASE.for_localizer()
# retrieval puts the culprit (position 1) at rank 2
RANKING = [RankedCandidate(SHAS[p], p, 1.0 / r, r) for r, p in enumerate([3, 1, 0, 5, 2, 4], start=1)]


class FakeLLM:
    name = "fake"

    def __init__(self, answer=None, error=None):
        self.answer, self.error, self.calls, self.prompts = answer, error, 0, []

    def complete_json(self, prompt, schema=None):
        self.calls += 1
        self.prompts.append(prompt)
        if self.error:
            raise LLMError(self.error)
        return self.answer


def alias_of(prompt_aliases, sha):
    return next(a for a, s in prompt_aliases.items() if s == sha)


def test_prompt_is_chronological_and_leak_free():
    short = [DOCS[3], DOCS[1], DOCS[0]]                       # retrieval order
    prompt, aliases = build_prompt(HIDDEN, short)
    assert list(aliases.values()) == [SHAS[0], SHAS[1], SHAS[3]]     # chronological, not retrieval order
    assert prompt.index("### C1") < prompt.index("Docs typo") < prompt.index("### C2")
    for s in SHAS:
        assert s[:12] not in prompt
    assert "4242" not in prompt and "#77" not in prompt and "alice/fast-sum" not in prompt
    assert CASE.case_id not in prompt and "got FASTER" in prompt and "from pkg.core import sum" in prompt


def test_no_redact_ablation_keeps_numbers():
    prompt, _ = build_prompt(HIDDEN, [DOCS[1]], do_redact=False)
    assert "#4242" in prompt


def test_redact():
    assert redact("Fix (#123) see gh-9 and #45") == "Fix  see #N and #N"
    assert "pull/12" not in redact("https://github.com/a/b/pull/12")


def test_retrieval_prior_monotone_and_normalized():
    R = retrieval_prior(RANKING)
    assert math.isclose(sum(R.values()), 1.0)
    vals = [R[r.sha] for r in RANKING]
    assert vals == sorted(vals, reverse=True)


def test_llm_moves_mass_to_its_pick_and_never_zero():
    _, aliases = build_prompt(HIDDEN, [DOCS[p] for p in (3, 1, 0, 5)])
    answer = {"candidates": [{"id": alias_of(aliases, SHAS[1]), "score": 0.9, "reason": "changes sum"},
                             {"id": alias_of(aliases, SHAS[3]), "score": 0.1, "reason": "parser"}]}
    llm = FakeLLM(answer)
    base = prioritize(HIDDEN, DOCS, RANKING, None, w=0.0, top_k=4)
    p = prioritize(HIDDEN, DOCS, RANKING, llm, top_k=4)
    assert p.llm_ok and llm.calls == 1
    assert math.isclose(sum(p.probs.values()), 1.0) and min(p.probs.values()) > 0
    assert p.rank_of(SHAS[1]) == 1 and base.rank_of(SHAS[1]) == 2
    assert p.bits(SHAS[1]) < base.bits(SHAS[1])
    assert p.probs[SHAS[4]] > 0                               # outside top-k, still reachable


def test_llm_failure_falls_back_to_retrieval():
    p = prioritize(HIDDEN, DOCS, RANKING, FakeLLM(error="HTTP 429"), top_k=4)
    base = prioritize(HIDDEN, DOCS, RANKING, None, w=0.0, top_k=4)
    assert not p.llm_ok and "429" in p.error
    assert p.probs == pytest.approx(base.probs)


def test_garbage_and_hallucinated_ids_ignored():
    answer = {"candidates": [{"id": "C99", "score": 1.0, "reason": ""}, {"id": "C1", "score": "abc"},
                             {"id": "C2", "score": 7, "reason": "x"}, "junk"]}
    p = prioritize(HIDDEN, DOCS, RANKING, FakeLLM(answer), top_k=4)
    assert p.llm_ok and list(p.llm_scores.values()) == [1.0]        # clipped to 1, others dropped
    p2 = prioritize(HIDDEN, DOCS, RANKING, FakeLLM({"nothing": 1}), top_k=4)
    assert not p2.llm_ok and p2.error


def test_cached_llm(tmp_path):
    inner = FakeLLM({"candidates": []})
    a = CachedLLM(inner, tmp_path / "l.db")
    a.complete_json("p", {"s": 1})
    a.complete_json("p", {"s": 1})
    CachedLLM(inner, tmp_path / "l.db").complete_json("p", {"s": 1})
    assert inner.calls == 1
    a.complete_json("other", {"s": 1})
    assert inner.calls == 2


def test_gemini_needs_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(LLMError, match="GEMINI_API_KEY"):
        GeminiClient()


def test_retry_after_parses_gemini_429():
    assert _retry_after('{"retryDelay": "17s"}') == 18.0
    assert _retry_after("nothing") is None
