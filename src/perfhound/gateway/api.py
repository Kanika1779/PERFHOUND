"""Gateway Facade (gateway part 1): the ONLY door other modules use.

    from perfhound.gateway import Gateway
    with Gateway("path/to/repo") as gw:
        candidates = gw.get_candidates("v1.2", "HEAD")
        with gw.worktree() as wt:
            folder = wt.checkout(candidates[0].sha)

Parts behind this door (range resolver, local git, analyzer, cache, later
GitHub and snapshots) can change freely as long as this API stays the same.
"""

from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone
from dataclasses import dataclass, replace
from pathlib import Path

from .analyzer import ANALYZER_VERSION, CodeAnalyzer, FunctionChanges
from .cache import Cache
from .cases import RegressionCase
from .errors import GatewayError
from .fetcher import RepoFetcher
from .gitcmd import run_git
from .local_git import DEFAULT_MAX_DIFF_CHARS, DEFAULT_MAX_DIFF_LINES, LocalGitProvider
from .models import SCHEMA_VERSION, CandidateCommit
from .range import CommitRange, find_repo_root, resolve_range
from .snapshot import Snapshot, build_snapshot
from .worktree import WorktreeManager


@dataclass(frozen=True)
class RequestStats:
    """What the last get_candidates() call cost (for the evaluation section)."""

    commits: int
    commit_cache_hits: int
    function_cache_hits: int
    seconds: float


def repo_identity(repo: Path) -> str:
    """Stable id for a repository: its root commit(s), not its folder.

    Survives moving / re-cloning the repo. Falls back to the folder path
    for a repo without commits.
    """
    proc = run_git(repo, "rev-list", "--max-parents=0", "HEAD", check=False)
    roots = sorted(proc.stdout.split()) if proc.returncode == 0 else []
    basis = ",".join(roots) if roots else "path:" + str(repo.resolve())
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]


class Gateway:
    def __init__(
        self,
        repo: str | Path,
        *,
        cache: Cache | str | Path | bool = True,
        max_diff_lines: int = DEFAULT_MAX_DIFF_LINES,
        max_diff_chars: int = DEFAULT_MAX_DIFF_CHARS,
    ) -> None:
        self.repo = find_repo_root(repo)
        self.max_diff_lines = max_diff_lines
        self.max_diff_chars = max_diff_chars
        self._local = LocalGitProvider(self.repo, max_diff_lines=max_diff_lines, max_diff_chars=max_diff_chars)
        self._analyzer = CodeAnalyzer(self.repo)
        if cache is True:
            self.cache: Cache | None = Cache()
        elif cache is False or cache is None:
            self.cache = None
        elif isinstance(cache, Cache):
            self.cache = cache
        else:
            self.cache = Cache(cache)
        self._repo_id: str | None = None
        self.last_stats: RequestStats | None = None

    @property
    def repo_id(self) -> str:
        if self._repo_id is None:
            self._repo_id = repo_identity(self.repo)
        return self._repo_id

    @classmethod
    def for_case(cls, case: RegressionCase, *, fetcher: RepoFetcher | None = None, **options) -> "Gateway":
        """Gateway on the case's repository (cloned on first use), with good/bad present locally."""
        fetcher = fetcher or RepoFetcher()
        path = fetcher.fetch(case.repo)
        missing = fetcher.ensure_commits(path, [case.good, case.bad])
        if missing:
            raise GatewayError(f"case {case.case_id}: commits not found in {case.repo}: {missing}")
        return cls(path, **options)

    def candidates_for(self, case: RegressionCase, **options) -> list[CandidateCommit]:
        """Candidates for a case. Never uses the case's ground truth."""
        return self.get_candidates(case.good, case.bad, **options)

    # -- main API --------------------------------------------------------------

    def resolve(self, good: str, bad: str, **range_options) -> CommitRange:
        """good..bad -> ordered SHAs (see range.resolve_range for options)."""
        return resolve_range(self.repo, good, bad, **range_options)

    def get_candidates(self, good: str, bad: str, *, analyze: bool = True, **range_options) -> list[CandidateCommit]:
        """All commits after `good` up to `bad` (first-parent), oldest first.

        Commits already in the cache are not read from git again; only the
        missing ones are (still in one batched call).
        """
        start = time.perf_counter()
        commit_range = self.resolve(good, bad, **range_options)
        commits, commit_hits = self._load_commits(commit_range)
        function_hits = 0
        if analyze:
            commits, function_hits = self._add_functions(commits)
        self.last_stats = RequestStats(len(commits), commit_hits, function_hits, time.perf_counter() - start)
        return commits

    def snapshot(self, path: str | Path, good: str, bad: str, *, sanitizer=None, **range_options) -> Snapshot:
        """Freeze good..bad into a verified JSON file for reproducible experiments."""
        from perfhound import __version__

        commit_range = self.resolve(good, bad, **range_options)
        candidates = self.get_candidates(commit_range.good, commit_range.bad, **range_options)
        meta = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "perfhound_version": __version__,
            "repo_id": self.repo_id,
            "good_ref": good, "bad_ref": bad,
            "good": commit_range.good, "bad": commit_range.bad,
            "settings": {"max_diff_lines": self.max_diff_lines, "max_diff_chars": self.max_diff_chars,
                         "analyzer_version": ANALYZER_VERSION},
        }
        snap = build_snapshot(candidates, meta, sanitizer)
        snap.save(path)
        return snap

    def worktree(self, **options) -> WorktreeManager:
        """A private checkout area for benchmarking; never touches the user's folder."""
        return WorktreeManager(self.repo, **options)

    def close(self) -> None:
        if self.cache is not None:
            self.cache.close()

    def __enter__(self) -> "Gateway":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- internals -----------------------------------------------------------

    @property
    def _commit_kind(self) -> str:
        return f"commit:s{SCHEMA_VERSION}:l{self.max_diff_lines}:c{self.max_diff_chars}"

    _FUNCTION_KIND = f"functions:a{ANALYZER_VERSION}"

    def _load_commits(self, commit_range: CommitRange) -> tuple[list[CandidateCommit], int]:
        by_sha: dict[str, CandidateCommit] = {}
        if self.cache is not None:
            for sha, payload in self.cache.get_many(self.repo_id, commit_range.shas, self._commit_kind).items():
                try:
                    by_sha[sha] = CandidateCommit.from_dict(payload)
                except (KeyError, TypeError, ValueError):
                    pass   # damaged entry -> treat as a miss, it will be overwritten
        hits = len(by_sha)

        missing = [s for s in commit_range.shas if s not in by_sha]
        if missing:
            fresh = self._local.get_commits(commit_range, missing)
            by_sha.update((c.sha, c) for c in fresh)
            if self.cache is not None:
                self.cache.put_many(
                    self.repo_id,
                    {c.sha: replace(c, position=0).to_dict() for c in fresh},   # position is range-specific
                    self._commit_kind,
                )
        commits = [replace(by_sha[sha], position=i) for i, sha in enumerate(commit_range.shas)]
        return commits, hits

    def _add_functions(self, commits: list[CandidateCommit]) -> tuple[list[CandidateCommit], int]:
        changes: dict[str, FunctionChanges] = {}
        if self.cache is not None:
            for sha, p in self.cache.get_many(self.repo_id, [c.sha for c in commits], self._FUNCTION_KIND).items():
                try:
                    changes[sha] = FunctionChanges(
                        tuple(p["changed"]), tuple(p["added"]), tuple(p["deleted"]), tuple(p["skipped"])
                    )
                except (KeyError, TypeError):
                    pass
        hits = len(changes)

        need = [c for c in commits if c.sha not in changes]
        if need:
            fresh = self._analyzer.analyze(need)
            changes.update(fresh)
            if self.cache is not None:
                self.cache.put_many(
                    self.repo_id,
                    {
                        sha: {"changed": list(fc.changed), "added": list(fc.added),
                              "deleted": list(fc.deleted), "skipped": list(fc.skipped_files)}
                        for sha, fc in fresh.items()
                    },
                    self._FUNCTION_KIND,
                )
        out = [
            replace(
                c,
                changed_functions=changes[c.sha].changed,
                added_functions=changes[c.sha].added,
                deleted_functions=changes[c.sha].deleted,
                unanalyzed_files=changes[c.sha].skipped_files,
            )
            for c in commits
        ]
        return out, hits
