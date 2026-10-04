"""The ONE place where Perfhound starts git processes.

Every gateway part calls git through `run_git`, so encoding, environment
and error handling are identical everywhere (and easy to mock in tests).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .errors import GitCommandError, GitNotFoundError

# Environment applied to every git call.
_GIT_ENV_OVERRIDES = {
    "GIT_TERMINAL_PROMPT": "0",   # never block waiting for a password
    "GIT_OPTIONAL_LOCKS": "0",    # read-only commands must not take index.lock in the user's repo
    "LC_ALL": "C",                # stable, English error messages
}


def git_executable() -> str:
    exe = shutil.which("git")
    if exe is None:
        raise GitNotFoundError("git is not installed or not on PATH; Perfhound needs git >= 2.25")
    return exe


def run_git(
    repo: str | Path,
    *args: str,
    check: bool = True,
    ok_codes: tuple[int, ...] = (0,),
) -> subprocess.CompletedProcess[str]:
    """Run `git <args>` inside `repo` and return the completed process.

    Arguments are passed as a list (no shell), so refs with spaces or
    special characters cannot inject commands. Output is decoded as UTF-8
    with replacement, so a commit with broken encoding cannot crash us.
    """
    cmd = [git_executable(), *args]
    env = dict(os.environ)
    env.update(_GIT_ENV_OVERRIDES)
    proc = subprocess.run(
        cmd,
        cwd=str(repo),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and proc.returncode not in ok_codes:
        raise GitCommandError(list(args), proc.returncode, proc.stderr)
    return proc


def git_out(repo: str | Path, *args: str) -> str:
    """Run git and return stdout with surrounding whitespace stripped."""
    return run_git(repo, *args).stdout.strip()
