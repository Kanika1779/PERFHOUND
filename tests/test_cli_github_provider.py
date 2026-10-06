"""`perfhound candidates/localize --github`: GraphQL (50 commits per request) with a token, REST without."""
import perfhound.gateway.github as gh
from perfhound.__main__ import _github_provider


def test_token_means_graphql(monkeypatch, tmp_path):
    monkeypatch.setenv("PERFHOUND_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(gh, "find_token", lambda: ("ghp_test", "test"))
    p = _github_provider("title")
    assert isinstance(p, gh.GitHubGraphQLProvider) and p.fields == "title"


def test_no_token_means_rest(monkeypatch, tmp_path):
    monkeypatch.setenv("PERFHOUND_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(gh, "find_token", lambda: (None, None))
    assert isinstance(_github_provider(), gh.GitHubPRProvider)
