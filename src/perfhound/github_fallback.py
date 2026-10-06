"""GitHub provider for `perfhound candidates/localize --github`: GraphQL first, REST if GraphQL fails.

GraphQL gets 50 commits per request but needs a token and can be refused (e.g. a fine-grained
token without access to the repository). REST is 1 request per commit but works more often.
Same rule as the gateway's `range` command: GraphQL found no PR AND reported a problem -> ask REST once.
Lives outside perfhound.gateway on purpose: the gateway is the teammate's package, kept identical.
"""

from __future__ import annotations

from dataclasses import replace


class GraphQLWithRestFallback:
    def __init__(self, graphql, rest) -> None:
        self.graphql = graphql
        self.rest = rest
        self.used_rest = False

    @property
    def client(self):
        return self.graphql.client

    @property
    def fields(self):
        return self.graphql.fields

    def enrich(self, repo, repo_id, commits, *, cutoff=None):
        out, stats = self.graphql.enrich(repo, repo_id, commits, cutoff=cutoff)
        if stats.with_pr or not stats.warnings:
            return out, stats
        self.used_rest = True
        out2, stats2 = self.rest.enrich(repo, repo_id, [replace(c, pr=None) for c in commits], cutoff=cutoff)
        stats2.requests += stats.requests
        stats2.warnings = [f"GitHub GraphQL failed ({'; '.join(stats.warnings)}) - used REST instead",
                           *stats2.warnings]
        return out2, stats2
