"""Gateway Facade (gateway part 1): the ONLY door other modules use.

    from perfhound.gateway import Gateway
    gw = Gateway("path/to/repo")
    candidates = gw.get_candidates("v1.2", "HEAD")

Parts behind this door (range resolver, local git, later analyzer, cache,
GitHub, worktrees) can change freely as long as this API stays the same.
"""

from __future__ import annotations

from pathlib import Path

from .analyzer import CodeAnalyzer
from .local_git import DEFAULT_MAX_DIFF_CHARS, DEFAULT_MAX_DIFF_LINES, LocalGitProvider
from .models import CandidateCommit
from .range import CommitRange, find_repo_root, resolve_range
from .worktree import WorktreeManager


class Gateway:
    def __init__(
        self,
        repo: str | Path,
        *,
        max_diff_lines: int = DEFAULT_MAX_DIFF_LINES,
        max_diff_chars: int = DEFAULT_MAX_DIFF_CHARS,
    ) -> None:
        self.repo = find_repo_root(repo)
        self._local = LocalGitProvider(self.repo, max_diff_lines=max_diff_lines, max_diff_chars=max_diff_chars)
        self._analyzer = CodeAnalyzer(self.repo)

    def resolve(self, good: str, bad: str, **range_options) -> CommitRange:
        """good..bad -> ordered SHAs (see range.resolve_range for options)."""
        return resolve_range(self.repo, good, bad, **range_options)

    def get_candidates(self, good: str, bad: str, *, analyze: bool = True, **range_options) -> list[CandidateCommit]:
        """All commits after `good` up to `bad` (first-parent), oldest first.

        analyze=True fills changed/added/deleted_functions (one extra git process).
        """
        commit_range = self.resolve(good, bad, **range_options)
        commits = self._local.get_commits(commit_range)
        return self._analyzer.enrich(commits) if analyze else commits

    def worktree(self, **options) -> WorktreeManager:
        """A private checkout area for benchmarking; never touches the user's folder.

            with gw.worktree() as wt:
                path = wt.checkout(sha)
        """
        return WorktreeManager(self.repo, **options)
