"""Measure gateway cold vs warm (cached) time on a synthetic repo.

    python scripts/bench_cache.py --commits 300

Builds a repo whose commits each edit a few functions across 20 Python
modules, then runs get_candidates() twice with a fresh cache. Real-repo
numbers (sympy) come in Step 10.
"""

from __future__ import annotations

import argparse
import os
import random
import subprocess
import tempfile
import time
from pathlib import Path

from perfhound.gateway import Gateway
from perfhound.gateway.cache import Cache


def git(cwd, *args, env=None):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=env)


def build_repo(root: Path, n_commits: int, n_modules: int = 20, funcs_per_module: int = 40) -> None:
    rng = random.Random(42)
    env = dict(os.environ, GIT_AUTHOR_NAME="b", GIT_AUTHOR_EMAIL="b@b", GIT_COMMITTER_NAME="b",
               GIT_COMMITTER_EMAIL="b@b", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    git(root, "init", "-q", "-b", "main", env=env)
    bodies = {(m, f): 0 for m in range(n_modules) for f in range(funcs_per_module)}

    def write(m):
        lines = [f"def f{f}(x):\n    return x + {bodies[(m, f)]}\n\n\n" for f in range(funcs_per_module)]
        (root / "pkg").mkdir(exist_ok=True)
        (root / "pkg" / f"mod{m}.py").write_text("".join(lines), encoding="utf-8")

    for m in range(n_modules):
        write(m)
    git(root, "add", "-A", env=env)
    git(root, "commit", "-qm", "base", env=env)
    git(root, "tag", "good", env=env)
    for i in range(n_commits):
        for _ in range(3):
            m, f = rng.randrange(n_modules), rng.randrange(funcs_per_module)
            bodies[(m, f)] += 1
            write(m)
        git(root, "commit", "-qam", f"change {i}", env=env)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--commits", type=int, default=300)
    ap.add_argument("--runs", type=int, default=3)
    args = ap.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        repo.mkdir()
        print(f"building synthetic repo with {args.commits} commits ...")
        build_repo(repo, args.commits)
        cold, warm = [], []
        for run in range(args.runs):
            db = Path(tmp) / f"cache{run}.db"
            with Cache(db) as cache:
                gw = Gateway(repo, cache=cache)
                gw.get_candidates("good", "HEAD")
                cold.append(gw.last_stats.seconds)
                gw2 = Gateway(repo, cache=cache)
                gw2.get_candidates("good", "HEAD")
                warm.append(gw2.last_stats.seconds)
                assert gw2.last_stats.commit_cache_hits == args.commits
        c, w = sorted(cold)[len(cold) // 2], sorted(warm)[len(warm) // 2]
        print(f"commits={args.commits}  cold={c*1000:.0f} ms  warm={w*1000:.0f} ms  speedup={c/w:.1f}x  "
              f"(median of {args.runs})")


if __name__ == "__main__":
    main()
