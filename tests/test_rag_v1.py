from datetime import datetime, timedelta, timezone

import pytest

from perfhound.gateway import BenchmarkSpec, Gateway, RegressionCase
from perfhound.rag import BM25, build_commit_documents, build_query, rank_candidates, tokenize
from perfhound.rag.metrics import RetrievalResult, mrr, random_mrr, random_recall_at, recall_at


def test_tokenizer_splits_identifiers_and_drops_noise():
    toks = tokenize("def get_all_relevant_facts(self): return HTTPServer.parseURL(x)")
    assert "get_all_relevant_facts" in toks and {"get", "relevant", "facts"} <= set(toks)
    assert {"http", "server", "parse", "url"} <= set(toks)
    assert not {"def", "self", "return", "x"} & set(toks)


def test_bm25_basics():
    docs = [["cache", "lookup", "fast"], ["parse", "html", "parse"], ["cache", "cache", "cache", "eviction"]]
    bm = BM25(docs)
    assert bm.scores(["zzz"]) == [0.0, 0.0, 0.0]                      # unknown term scores nothing
    s = bm.scores(["parse"])
    assert s[1] > 0 and s[0] == s[2] == 0
    s = bm.scores(["eviction", "cache"])                               # rare term wins over common
    assert s[2] > s[0] > s[1]
    assert bm.scores(["parse", "parse"])[1] > bm.scores(["parse"])[1]  # query multiplicity counts
    with pytest.raises(ValueError):
        BM25([])


def test_ranks_the_slow_commit_first_on_the_fixture(fixture_repo):
    cands = Gateway(fixture_repo.path, cache=False).get_candidates("v1.0", "v1.1")
    case = RegressionCase(case_id="t:1", source="t", repo=str(fixture_repo.path), language="python",
                          good="v1.0", bad="v1.1",
                          benchmark=BenchmarkSpec("bench_add", "script", workload="from mathops import add\nadd(1, 2)\n"))
    ranked = rank_candidates(build_query(case), build_commit_documents(cands))
    assert len(ranked) == 7 and [r.rank for r in ranked] == list(range(1, 8))
    top_two = {r.sha for r in ranked[:3]}
    assert fixture_repo.sha("slow_add") in top_two                    # touches mathops.add
    assert ranked[-1].score == 0.0                                     # every candidate still gets a score


def test_query_needs_something_to_search_with():
    case = RegressionCase(case_id="t:2", source="t", repo=".", language="python", good="a", bad="b")
    with pytest.raises(ValueError, match="nothing to search with"):
        build_query(case)
    case = RegressionCase(case_id="t:3", source="t", repo=".", language="python", good="a", bad="b",
                          metadata={"symptom": "course page loads slowly"})
    assert "course page" in build_query(case)


def test_query_reads_the_benchmark_script_named_in_the_command():
    case = RegressionCase(case_id="t:4", source="t", repo=".", language="python", good="a", bad="b",
                          benchmark=BenchmarkSpec("b", "command", command="python bench/load_page.py --n 5"))
    q = build_query(case, read_file=lambda p: "render_course_page()" if p == "bench/load_page.py" else None)
    assert "render_course_page" in q


def test_date_limit_keeps_future_commits_out(fixture_repo):
    cands = Gateway(fixture_repo.path, cache=False).get_candidates("v1.0", "v1.1")
    limit = cands[3].timestamp
    docs = build_commit_documents(cands, date_limit=limit)
    assert [d.sha for d in docs] == [c.sha for c in cands if c.timestamp <= limit]
    assert len(docs) < len(cands)


def test_metrics():
    res = [RetrievalResult("a", 20, 1), RetrievalResult("b", 20, 4), RetrievalResult("c", 20, 12)]
    assert recall_at(res, 1) == pytest.approx(1 / 3)
    assert recall_at(res, 5) == pytest.approx(2 / 3)
    assert mrr(res) == pytest.approx((1 + 1 / 4 + 1 / 12) / 3)
    assert random_recall_at(res, 5) == pytest.approx(0.25)
    assert random_mrr(res) == pytest.approx(sum(1 / i for i in range(1, 21)) / 20)
