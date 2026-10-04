"""Dense + hybrid retrieval, tested with a deterministic fake embedder (no model download)."""

import hashlib
import math

import pytest

from perfhound.gateway import BenchmarkSpec, Gateway, RegressionCase
from perfhound.rag import build_commit_documents, build_query, rank_candidates, rank_dense, rank_hybrid, tokenize
from perfhound.rag.embeddings import CachedEmbedder, cosine
from perfhound.rag.fusion import reciprocal_rank_fusion
from perfhound.rag.retriever import RankedCandidate


class FakeEmbedder:
    """Hashed bag-of-words vectors: similar token sets -> similar vectors. Counts calls."""

    name = "fake-bow-64"

    def __init__(self):
        self.doc_calls = self.query_calls = 0

    def _vec(self, text):
        v = [0.0] * 64
        for t in tokenize(text):
            v[int(hashlib.md5(t.encode()).hexdigest(), 16) % 64] += 1.0
        return v

    def embed_documents(self, texts):
        self.doc_calls += len(texts)
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        self.query_calls += 1
        return self._vec(text)


def rc(sha, rank, pos=0):
    return RankedCandidate(sha, pos, 0.0, rank)


def test_cosine():
    assert cosine([1, 0], [1, 0]) == pytest.approx(1.0)
    assert cosine([1, 0], [0, 1]) == pytest.approx(0.0)
    assert cosine([0, 0], [1, 1]) == 0.0


def test_rrf_rewards_agreement_and_uses_only_ranks():
    a = [rc("x", 1), rc("y", 2), rc("z", 3)]
    b = [rc("y", 1), rc("x", 2), rc("z", 3)]
    fused = reciprocal_rank_fusion([a, b])
    assert {fused[0].sha, fused[1].sha} == {"x", "y"} and fused[2].sha == "z"
    assert fused[0].score == pytest.approx(1 / 61 + 1 / 62)
    # symmetric disagreement -> equal fused scores (ties then broken by position, deterministically)
    c = [rc("p", 1, pos=0), rc("q", 2, pos=1), rc("r", 3, pos=2)]
    d = [rc("r", 1, pos=2), rc("q", 2, pos=1), rc("p", 3, pos=0)]
    fused = {f.sha: f.score for f in reciprocal_rank_fusion([c, d])}
    assert fused["p"] == pytest.approx(fused["r"])
    assert [f.sha for f in reciprocal_rank_fusion([c, d])] == [f.sha for f in reciprocal_rank_fusion([c, d])]
    assert reciprocal_rank_fusion([]) == []


@pytest.fixture
def setup(fixture_repo):
    cands = Gateway(fixture_repo.path, cache=False).get_candidates("v1.0", "v1.1")
    case = RegressionCase(case_id="t", source="t", repo=str(fixture_repo.path), language="python",
                          good="v1.0", bad="v1.1",
                          benchmark=BenchmarkSpec("bench_add", "script", workload="from mathops import add\nadd(1, 2)\n"))
    return fixture_repo, build_query(case), build_commit_documents(cands)


def test_dense_and_hybrid_rank_every_candidate(setup):
    repo, query, docs = setup
    emb = FakeEmbedder()
    dense = rank_dense(query, docs, emb)
    hybrid = rank_hybrid(query, docs, emb)
    for ranking in (dense, hybrid):
        assert sorted(r.sha for r in ranking) == sorted(d.sha for d in docs)      # nobody filtered out
        assert [r.rank for r in ranking] == list(range(1, len(docs) + 1))
    assert repo.sha("slow_add") in {r.sha for r in hybrid[:3]}


def test_embedding_cache_avoids_recomputation(setup, tmp_path):
    repo, query, docs = setup
    inner = FakeEmbedder()
    emb = CachedEmbedder(inner, tmp_path / "emb.db")
    first = rank_dense(query, docs, emb)
    assert inner.doc_calls == len(docs) and inner.query_calls == 1
    again = CachedEmbedder(inner, tmp_path / "emb.db")          # new process, same file
    second = rank_dense(query, docs, again)
    assert inner.doc_calls == len(docs) and inner.query_calls == 1 # nothing recomputed
    assert [r.sha for r in first] == [r.sha for r in second]
    assert [r.score for r in first] == pytest.approx([r.score for r in second], abs=1e-6)
    assert again.hits == len(docs) + 1 and again.misses == 0


def test_cache_separates_models(tmp_path):
    a, b = FakeEmbedder(), FakeEmbedder()
    b.name = "other-model"
    CachedEmbedder(a, tmp_path / "e.db").embed_documents(["hello world"])
    CachedEmbedder(b, tmp_path / "e.db").embed_documents(["hello world"])
    assert a.doc_calls == 1 and b.doc_calls == 1


def test_missing_fastembed_gives_install_hint(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "fastembed":
            raise ImportError("no fastembed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    from perfhound.rag.embeddings import FastEmbedEmbedder

    with pytest.raises(RuntimeError, match=r"pip install -e \"\.\[embed\]\""):
        FastEmbedEmbedder()
