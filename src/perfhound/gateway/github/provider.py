"""GitHub PR provider: the pull request behind each candidate commit -> CandidateCommit.pr.

Git stores commit messages; GitHub stores the PR title and description, which often explain
WHAT a change is for ("Speed up Matrix.atoms", "Validate inputs in ..."). RAG and the LLM read
pr.title + pr.body when present (rag/documents.py), so this adds signal for terse commits.

    GET /repos/{o}/{r}/commits/{sha}/pulls      one request per commit (cached, see below)
    choose: the PR whose merge commit IS this commit; else the earliest merged PR containing it

TIME RULE (leak guard): a PR merged AFTER the `bad` commit is never used. A later PR (revert,
"fix the slowdown from #123") would hand the localizer the answer.
Remaining risk, documented, not hidden: GitHub returns the CURRENT title/body; an author may
have edited them after the merge. fields="title" (titles are rarely edited) is the cautious
setting for evaluations; the default "title+body" is for real use.

Cost & caching: answers are cached per commit with their ETag (cache "volatile" store, 24 h).
A stale entry is re-validated with If-None-Match: a 304 answer costs nothing against the limit.
Never blocks localization: no network / rate limit / bad token -> stop asking, keep pr=None,
report a warning. Everything else works exactly as without GitHub.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Sequence

from ..models import CandidateCommit, PRInfo
from .client import GitHubClient
from .errors import GitHubError, GitHubNotFound
from .links import RepoRef, linked_issues

KIND = "github_pulls_v1"


@dataclass
class GitHubStats:
    requests: int = 0
    not_modified: int = 0
    cache_hits: int = 0
    with_pr: int = 0
    skipped_after_cutoff: int = 0
    warnings: list[str] = field(default_factory=list)


def _parse_time(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _slim(pr: dict) -> dict:
    return {"number": pr.get("number"), "title": pr.get("title") or "", "body": pr.get("body") or "",
            "labels": [l.get("name", "") for l in pr.get("labels") or [] if isinstance(l, dict)],
            "url": pr.get("html_url") or "", "merged_at": pr.get("merged_at"),
            "merge_commit_sha": pr.get("merge_commit_sha")}


class GitHubPRProvider:
    def __init__(self, client: GitHubClient, cache=None, *, fields: str = "title+body",
                 max_requests: int = 300) -> None:
        if fields not in ("title", "title+body"):
            raise ValueError("fields must be 'title' or 'title+body'")
        self.client = client
        self.cache = cache
        self.fields = fields
        self.max_requests = max_requests

    def enrich(self, repo: RepoRef, repo_id: str, commits: Sequence[CandidateCommit], *,
               cutoff: datetime | None = None) -> tuple[list[CandidateCommit], GitHubStats]:
        stats = GitHubStats()
        start_requests = self.client.requests
        stopped = False
        out: list[CandidateCommit] = []
        for c in commits:
            pulls = None
            cached, fresh = (self.cache.get_volatile(repo_id, c.sha, KIND) if self.cache else (None, False))
            if cached is not None and fresh:
                pulls = cached.payload
                stats.cache_hits += 1
            elif not stopped and self.client.requests - start_requests < self.max_requests:
                try:
                    resp = self.client.get(f"/repos/{repo.slug}/commits/{c.sha}/pulls",
                                           etag=cached.etag if cached else None)
                    if resp.not_modified and cached is not None:
                        pulls, etag = cached.payload, cached.etag
                        stats.not_modified += 1
                    else:
                        pulls, etag = [_slim(p) for p in resp.data or [] if isinstance(p, dict)], resp.etag
                    if self.cache:
                        self.cache.put_volatile(repo_id, c.sha, KIND, pulls, etag)
                except GitHubNotFound:
                    stopped = True
                    pulls = cached.payload if cached is not None else None
                    stats.warnings.append(f"GitHub does not know {repo.slug} or commit {c.short_sha} (private repo "
                                          "without access, or commits not pushed) - PR data skipped")
                except GitHubError as e:
                    stopped = True
                    pulls = cached.payload if cached is not None else None     # stale beats nothing
                    stats.warnings.append(f"GitHub PR data incomplete: {e}")
            elif cached is not None:
                pulls = cached.payload                       # stale but better than nothing
            if pulls is None and not stopped and self.client.requests - start_requests >= self.max_requests:
                stopped = True
                stats.warnings.append(f"stopped after {self.max_requests} GitHub requests (max_requests)")
            pr, late = self._choose(c, pulls or [], cutoff, repo)
            stats.skipped_after_cutoff += late
            if pr is not None:
                stats.with_pr += 1
                out.append(replace(c, pr=pr))
            else:
                out.append(c)
        stats.requests = self.client.requests - start_requests
        return out, stats

    def _choose(self, c: CandidateCommit, pulls: list[dict], cutoff: datetime | None,
                repo: RepoRef) -> tuple[PRInfo | None, int]:
        late = 0
        usable = []
        for p in pulls:
            merged = _parse_time(p.get("merged_at"))
            if merged is None:
                continue                                     # open/closed-unmerged PRs did not land this code
            if cutoff is not None and merged > cutoff:
                late += 1
                continue
            usable.append((p, merged))
        if not usable:
            return None, late
        exact = [u for u in usable if (u[0].get("merge_commit_sha") or "") == c.sha]
        p, _ = (exact or sorted(usable, key=lambda u: u[1]))[0]
        body = p.get("body", "") if self.fields == "title+body" else ""
        issues = linked_issues(p.get("body", ""), repo)
        if issues and self.fields == "title+body":
            body += "\n\nLinked issues: " + ", ".join(f"#{n}" for n in issues)
        return PRInfo(number=int(p["number"]), title=p.get("title", ""), body=body,
                      labels=tuple(p.get("labels") or ()), url=p.get("url", "")), late
