"""GitHub part of the gateway, against a FAKE GitHub (no network, no token, no rate limit).

Fake data is invented (an "acme/shop" repo)."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from perfhound.gateway import Gateway
from perfhound.gateway.cache import Cache
from perfhound.gateway.github import (GitHubAuthError, GitHubClient, GitHubNotFound, GitHubPRProvider,
                                      GitHubRateLimited, GitHubRequestFailed, InvalidGitHubLink, RepoRef,
                                      delete_token, find_token, linked_issues, mask, parse_commit, parse_compare,
                                      parse_pr, parse_repo, save_token)
from perfhound.gateway.gitcmd import run_git
from perfhound.gateway.models import CandidateCommit
from perfhound.rag.documents import commit_document

SHOP = RepoRef("acme", "shop")


class FakeGitHub:
    """transport(method, url, headers, timeout) -> (status, headers, body). Routes by path."""

    def __init__(self):
        self.routes = {}          # path (no query) -> list of (status, headers, payload); last one repeats
        self.calls = []

    def add(self, path, payload, status=200, headers=None):
        self.routes.setdefault(path, []).append((status, headers or {}, payload))

    def __call__(self, method, url, headers, timeout):
        self.calls.append((url, dict(headers)))
        path = url.replace("https://api.github.com", "").split("?")[0]
        if path not in self.routes:
            return 404, {}, b'{"message": "Not Found"}'
        queue = self.routes[path]
        status, h, payload = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(payload, Exception):
            raise payload
        h = {"X-RateLimit-Remaining": "4999", "X-RateLimit-Limit": "5000", "X-RateLimit-Reset": "1900000000", **h}
        etag = h.get("ETag")
        if etag and headers.get("If-None-Match") == etag:
            return 304, h, b""
        return status, h, json.dumps(payload).encode() if payload is not None else b""


def client(fake, token="ghp_testtoken1234"):
    return GitHubClient(token, transport=fake, sleep=lambda s: None)


# -- links ------------------------------------------------------------------------------------

def test_links():
    for text in ("https://github.com/acme/shop", "https://github.com/acme/shop.git", "git@github.com:acme/shop.git",
                 "acme/shop", "https://github.com/acme/shop/tree/main/src"):
        assert parse_repo(text) == SHOP, text
    assert parse_pr("https://github.com/acme/shop/pull/12/files") == (SHOP, 12)
    assert parse_pr("acme/shop#12") == (SHOP, 12)
    assert parse_commit("https://github.com/acme/shop/commit/9CCB32E9a18f") == (SHOP, "9ccb32e9a18f")
    assert parse_commit("acme/shop@9ccb32e") == (SHOP, "9ccb32e")
    assert parse_compare("https://github.com/acme/shop/compare/v1.2...main") == (SHOP, "v1.2", "main")
    for bad in ("https://gitlab.com/a/b", "not a link", "https://github.com/acme"):
        with pytest.raises(InvalidGitHubLink):
            parse_repo(bad)
    with pytest.raises(InvalidGitHubLink):
        parse_pr("https://github.com/acme/shop/issues/3")


def test_linked_issues_same_repo_only():
    text = ("Fixes #45. Also closes acme/shop#7 and resolves https://github.com/acme/shop/issues/9; "
            "fixes other/repo#1, mentions #99 without a keyword")
    assert linked_issues(text, SHOP) == [45, 7, 9]


# -- token ------------------------------------------------------------------------------------

def test_token_sources_and_storage(monkeypatch):
    for var in ("PERFHOUND_GITHUB_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    assert find_token() == (None, None)
    path = save_token("ghp_saved000000005678")
    assert find_token() == ("ghp_saved000000005678", str(path))
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_env2")
    assert find_token()[0] == "ghp_env2"
    monkeypatch.setenv("PERFHOUND_GITHUB_TOKEN", "ghp_env1")
    assert find_token() == ("ghp_env1", "environment variable PERFHOUND_GITHUB_TOKEN")
    assert mask("ghp_saved000000005678") == "ghp_...5678" and mask(None) == "(none)" and mask("short") == "***"
    assert delete_token() and not delete_token()


# -- client -----------------------------------------------------------------------------------

def test_client_headers_counting_and_rate():
    fake = FakeGitHub()
    fake.add("/user", {"login": "kanika"})
    c = client(fake)
    assert c.get_json("/user")["login"] == "kanika"
    url, headers = fake.calls[0]
    assert headers["Authorization"] == "Bearer ghp_testtoken1234"
    assert headers["X-GitHub-Api-Version"] == "2022-11-28"
    assert c.requests == 1 and c.rate_remaining == 4999 and c.rate_limit == 5000


@pytest.mark.parametrize("status,headers,error,match", [
    (401, {}, GitHubAuthError, "perfhound github login"),
    (404, {}, GitHubNotFound, "private repository"),
    (403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1900000000"}, GitHubRateLimited, "limit reached"),
    (422, {}, GitHubRequestFailed, "422"),
])
def test_client_named_errors(status, headers, error, match):
    fake = FakeGitHub()
    fake.add("/x", {"message": "nope"}, status=status, headers=headers)
    with pytest.raises(error, match=match):
        client(fake).get("/x")
    assert len(fake.calls) == 1                     # 4xx are never retried


def test_client_retries_server_errors_and_network():
    fake = FakeGitHub()
    fake.add("/x", None, status=502)
    fake.add("/x", None, status=503)
    fake.add("/x", {"ok": True})
    assert client(fake).get_json("/x") == {"ok": True} and len(fake.calls) == 3
    fake = FakeGitHub()
    fake.add("/y", ConnectionResetError("dropped"))
    with pytest.raises(GitHubRequestFailed, match="cannot reach GitHub"):
        client(fake).get("/y")
    assert len(fake.calls) == 4                     # 1 + 3 retries


def test_client_pagination():
    fake = FakeGitHub()
    fake.add("/repos/acme/shop/pulls", [{"n": 1}, {"n": 2}],
             headers={"Link": '<https://api.github.com/repos/acme/shop/pulls2?page=2>; rel="next"'})
    fake.add("/repos/acme/shop/pulls2", [{"n": 3}])
    assert [p["n"] for p in client(fake).paginate("/repos/acme/shop/pulls")] == [1, 2, 3]


# -- provider ---------------------------------------------------------------------------------

T0 = datetime(2024, 3, 1, tzinfo=timezone.utc)


def commit(sha, hours):
    return CandidateCommit(sha=sha, parent="0" * 40, position=hours, message="Merge pull request", author="dev",
                           timestamp=T0 + timedelta(hours=hours))


def pr(number, title, merged, merge_sha=None, body=""):
    return {"number": number, "title": title, "body": body, "labels": [{"name": "performance"}],
            "html_url": f"https://github.com/acme/shop/pull/{number}",
            "merged_at": merged.isoformat().replace("+00:00", "Z") if merged else None, "merge_commit_sha": merge_sha}


def test_provider_picks_the_right_pr_and_applies_the_time_rule(tmp_path):
    a, b, c, d = ("a" * 40, "b" * 40, "c" * 40, "d" * 40)
    commits = [commit(a, 1), commit(b, 2), commit(c, 3), commit(d, 4)]
    fake = FakeGitHub()
    fake.add(f"/repos/acme/shop/commits/{a}/pulls",
             [pr(9, "Older PR containing a", T0), pr(10, "Speed up checkout", T0 + timedelta(hours=1), merge_sha=a,
                                                     body="Fixes #45")])
    fake.add(f"/repos/acme/shop/commits/{b}/pulls", [pr(11, "Draft, never merged", None)])
    fake.add(f"/repos/acme/shop/commits/{c}/pulls", [pr(12, "Revert: fix the slowdown from #10", T0 + timedelta(days=30))])
    fake.add(f"/repos/acme/shop/commits/{d}/pulls", [])
    prov = GitHubPRProvider(client(fake), Cache(tmp_path / "c.db"))
    out, st = prov.enrich(SHOP, "repo1", commits, cutoff=T0 + timedelta(hours=4))
    assert out[0].pr.number == 10                               # merge commit of THIS commit wins
    assert out[0].pr.labels == ("performance",) and "Linked issues: #45" in out[0].pr.body
    assert out[1].pr is None                                    # unmerged PR did not land the code
    assert out[2].pr is None and st.skipped_after_cutoff == 1   # merged after `bad`: never used (leak)
    assert out[3].pr is None
    assert st.requests == 4 and st.with_pr == 1
    assert "Speed up checkout" in commit_document(out[0]).fields["message"]      # RAG / LLM now read it


def test_provider_cache_etag_and_title_only(tmp_path):
    a = "a" * 40
    fake = FakeGitHub()
    fake.add(f"/repos/acme/shop/commits/{a}/pulls", [pr(10, "Speed up", T0, merge_sha=a, body="secret details")],
             headers={"ETag": '"v1"'})
    cache = Cache(tmp_path / "c.db")
    prov = GitHubPRProvider(client(fake), cache, fields="title")
    out, st = prov.enrich(SHOP, "r", [commit(a, 1)])
    assert out[0].pr.title == "Speed up" and out[0].pr.body == ""          # title-only: no description
    out, st = prov.enrich(SHOP, "r", [commit(a, 1)])
    assert st.requests == 0 and st.cache_hits == 1                         # fresh cache: no request at all
    cache.pr_ttl = 0                                                       # now stale -> revalidate with ETag
    out, st = prov.enrich(SHOP, "r", [commit(a, 1)])
    assert st.not_modified == 1 and out[0].pr.number == 10
    assert fake.calls[-1][1]["If-None-Match"] == '"v1"'


def test_provider_never_blocks_on_rate_limit(tmp_path):
    a, b = "a" * 40, "b" * 40
    fake = FakeGitHub()
    fake.add(f"/repos/acme/shop/commits/{a}/pulls", [pr(1, "x", T0, merge_sha=a)])
    fake.add(f"/repos/acme/shop/commits/{b}/pulls", {"message": "API rate limit exceeded"}, status=403,
             headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1900000000"})
    out, st = GitHubPRProvider(client(fake)).enrich(SHOP, "r", [commit(a, 1), commit(b, 2), commit("c" * 40, 3)])
    assert out[0].pr is not None and out[1].pr is None and out[2].pr is None
    assert len(fake.calls) == 2 and any("limit reached" in w for w in st.warnings)


# -- through the gateway ----------------------------------------------------------------------

def test_gateway_attaches_prs_for_a_github_repo(tmp_path):
    repo = tmp_path / "shop"
    repo.mkdir()
    run_git(repo, "init", "-q")
    shas = []
    for i, msg in enumerate(["init", "Merge pull request #10 from dev/fast", "docs"]):
        (repo / f"f{i}.py").write_text(f"def f{i}():\n    return {i}\n")
        run_git(repo, "add", "-A")
        run_git(repo, "-c", "user.name=d", "-c", "user.email=d@x", "commit", "-q", "-m", msg)
        shas.append(run_git(repo, "rev-parse", "HEAD").stdout.strip())
    fake = FakeGitHub()
    fake.add(f"/repos/acme/shop/commits/{shas[1]}/pulls", [pr(10, "Cache price lookups", T0, merge_sha=shas[1])])
    fake.add(f"/repos/acme/shop/commits/{shas[2]}/pulls", [])
    prov = GitHubPRProvider(client(fake), Cache(tmp_path / "c.db"))

    run_git(repo, "remote", "add", "origin", "https://github.com/acme/shop.git")
    with Gateway(repo, cache=Cache(tmp_path / "g.db"), github=prov) as gw:
        cands = gw.get_candidates(shas[0], shas[2])
        assert [c.pr.number if c.pr else None for c in cands] == [10, None]
        assert gw.last_stats.github_with_pr == 1 and gw.last_stats.github_requests == 2

    run_git(repo, "remote", "set-url", "origin", "https://gitlab.com/acme/shop.git")
    with Gateway(repo, cache=Cache(tmp_path / "g2.db"), github=prov) as gw:
        cands = gw.get_candidates(shas[0], shas[2])
        assert all(c.pr is None for c in cands)
        assert "not a GitHub repository" in gw.last_stats.github_warnings[0]

    with Gateway(repo, cache=Cache(tmp_path / "g3.db")) as gw:      # GitHub off: exactly as before
        gw.get_candidates(shas[0], shas[2])
        assert gw.last_stats.github_requests == 0 and gw.last_stats.github_warnings == ()
