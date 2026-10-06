"""`perfhound candidates/localize --github`: GraphQL (50 commits per request) with a token, REST without,
and REST once more if GraphQL fails."""
from dataclasses import dataclass, field, replace

import perfhound.gateway.github as gh
from perfhound.__main__ import _github_provider
from perfhound.gateway.github.provider import GitHubStats
from perfhound.github_fallback import GraphQLWithRestFallback


def test_token_means_graphql_with_rest_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv("PERFHOUND_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(gh, "find_token", lambda: ("ghp_test", "test"))
    p = _github_provider("title")
    assert isinstance(p, GraphQLWithRestFallback)
    assert isinstance(p.graphql, gh.GitHubGraphQLProvider) and isinstance(p.rest, gh.GitHubPRProvider)
    assert p.fields == "title"


def test_no_token_means_rest(monkeypatch, tmp_path):
    monkeypatch.setenv("PERFHOUND_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(gh, "find_token", lambda: (None, None))
    assert isinstance(_github_provider(), gh.GitHubPRProvider)


@dataclass(frozen=True)
class C:
    sha: str
    pr: object = None


@dataclass
class Fake:
    stats: GitHubStats
    pr: object = None
    calls: list = field(default_factory=list)

    def enrich(self, repo, repo_id, commits, *, cutoff=None):
        self.calls.append(cutoff)
        return [replace(c, pr=self.pr) for c in commits], self.stats


def test_graphql_failure_falls_back_to_rest_once():
    gql = Fake(GitHubStats(requests=1, warnings=["GraphQL: Resource not accessible by integration"]))
    rest = Fake(GitHubStats(requests=2, with_pr=2), pr="PR#1")
    out, st = GraphQLWithRestFallback(gql, rest).enrich("r", "id", [C("a"), C("b")], cutoff="T")
    assert [c.pr for c in out] == ["PR#1", "PR#1"] and rest.calls == ["T"]       # same cut-off (time rule)
    assert st.requests == 3 and "used REST instead" in st.warnings[0]


def test_graphql_success_or_simply_no_prs_does_not_call_rest():
    rest = Fake(GitHubStats())
    GraphQLWithRestFallback(Fake(GitHubStats(requests=1, with_pr=2)), rest).enrich("r", "id", [C("a")])
    GraphQLWithRestFallback(Fake(GitHubStats(requests=1)), rest).enrich("r", "id", [C("a")])  # repo w/o PRs
    assert rest.calls == []
