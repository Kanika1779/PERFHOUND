"""Repo Fetcher: turn a repository URL from a dataset into a local repo.

    fetcher = RepoFetcher()
    path = fetcher.fetch("https://github.com/pandas-dev/pandas")
    fetcher.ensure_commits(path, [good, bad])

* Clones are PARTIAL (--filter=blob:none) and without a checkout: commits
  and trees only (pandas: a fraction of a full clone). File contents are
  downloaded later in batches by the gateway (local_git.prefetch_blobs).
* One clone per repo URL under $PERFHOUND_REPOS_DIR (default
  ~/.perfhound/repos), reused across cases and runs.
* A local path is used as-is (never cloned, never modified).
* Only https / http / ssh / file and scp-style (git@host:path) URLs are
  accepted: git's ext:: and fd:: transports can run arbitrary commands, so
  a URL from a dataset file must never reach `git clone` unchecked.
* Cloning goes to a temporary folder that is renamed into place, so a
  crash never leaves a half-cloned repo that looks complete.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

from .errors import GatewayError
from .gitcmd import run_git
from .worktree import _rmtree

_SCP_RE = re.compile(r"^[\w.-]+@[\w.-]+:[\w./~-]+$")
_ALLOWED_SCHEMES = {"https", "http", "ssh", "file"}


class FetchError(GatewayError):
    """Repository could not be cloned / updated, or the URL is not allowed."""


def default_repos_dir() -> Path:
    base = os.environ.get("PERFHOUND_REPOS_DIR")
    return Path(base) if base else Path.home() / ".perfhound" / "repos"


def is_remote(repo: str) -> bool:
    return "://" in repo or bool(_SCP_RE.match(repo))


def validate_url(url: str) -> None:
    if url.startswith("-"):
        raise FetchError(f"refusing repository URL {url!r}")
    if _SCP_RE.match(url):
        return
    scheme = urlparse(url).scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise FetchError(f"repository URL scheme {scheme!r} is not allowed ({url!r})")


def local_dir_for(url: str, base: Path) -> Path:
    """github.com/pandas-dev/pandas -> <base>/github.com/pandas-dev/pandas"""
    if _SCP_RE.match(url):
        host, path = url.split("@", 1)[1].split(":", 1)
    else:
        p = urlparse(url)
        host, path = p.hostname or "local", p.path
    parts = [re.sub(r"[^\w.-]", "_", x) for x in path.strip("/").removesuffix(".git").split("/") if x]
    if host == "local" or not parts:
        parts = ["_" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]]
    return base.joinpath(re.sub(r"[^\w.-]", "_", host), *parts)


class RepoFetcher:
    def __init__(self, base_dir: str | Path | None = None, *, filter_blobs: bool = True) -> None:
        # absolute: git runs with other working directories, a relative path would be resolved twice
        self.base_dir = (Path(base_dir) if base_dir else default_repos_dir()).resolve()
        self.filter_blobs = filter_blobs

    def fetch(self, repo: str) -> Path:
        """Local path of `repo`, cloning it first if it is a URL seen for the first time."""
        if not is_remote(repo):
            path = Path(repo)
            if not path.exists():
                raise FetchError(f"local repository {repo!r} does not exist")
            return path
        validate_url(repo)
        dest = local_dir_for(repo, self.base_dir)
        if (dest / ".git").exists():
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        self._remove_stale_tmp(dest)
        tmp = dest.parent / f".{dest.name}.tmp-{uuid.uuid4().hex[:8]}"
        args = ["clone", "--quiet", "--no-checkout"]
        if self.filter_blobs:
            args.append("--filter=blob:none")
        try:
            proc = run_git(self.base_dir, *args, "--", repo, str(tmp), check=False)
            if proc.returncode != 0:
                raise FetchError(f"could not clone {repo}: {proc.stderr.strip()}")
            try:
                os.replace(tmp, dest)
            except OSError:
                if (dest / ".git").exists():     # another process won the race; use its clone
                    return dest
                raise
        finally:
            if tmp.exists():
                _rmtree(tmp)
        return dest

    STALE_TMP_SECONDS = 6 * 3600

    def _remove_stale_tmp(self, dest: Path) -> None:
        """Half-finished clones of killed processes (older than 6 h; a younger one may still be running)."""
        for p in dest.parent.glob(f".{dest.name}.tmp-*"):
            try:
                if time.time() - p.stat().st_mtime > self.STALE_TMP_SECONDS:
                    _rmtree(p)
            except OSError:
                pass

    def ensure_commits(self, path: str | Path, refs: list[str]) -> list[str]:
        """Make sure `refs` exist locally, fetching from origin if needed.
        Returns the refs that are still missing afterwards."""
        def missing() -> list[str]:
            out = []
            for r in refs:
                if r.startswith("-") or run_git(path, "rev-parse", "--verify", "--quiet",
                                                f"{r}^{{commit}}", check=False).returncode != 0:
                    out.append(r)
            return out

        todo = missing()
        if not todo:
            return []
        has_origin = run_git(path, "remote", "get-url", "origin", check=False).returncode == 0
        if not has_origin:
            return todo
        run_git(path, "fetch", "--quiet", "--tags", "origin", check=False)
        todo = missing()
        for r in todo:   # e.g. commits only reachable from a PR ref - GitHub serves them by SHA
            if re.fullmatch(r"[0-9a-f]{40}", r):
                run_git(path, "fetch", "--quiet", "origin", r, check=False)
        return missing()

    def remove(self, repo: str) -> None:
        """Delete the local clone of a URL (frees disk)."""
        if is_remote(repo):
            dest = local_dir_for(repo, self.base_dir)
            if dest.exists():
                _rmtree(dest)
