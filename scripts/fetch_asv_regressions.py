"""Snapshot pandas asv-runner regression issues to a JSON file (the asv-runner cases' input).

    python scripts/fetch_asv_regressions.py                      # -> data/asv_pandas_regressions.json

Uses the gateway's GitHub client (token from `perfhound github login` if present; anonymous
works too: ~2 requests). The file is committed with the date it was taken, so the cases built
from it are reproducible without GitHub.
"""

import argparse
import sys
from collections import Counter
from datetime import datetime, timezone

from perfhound.gateway.github import GitHubClient, find_token
from perfhound.gateway.sources.asv_runner import parse_issue, save_regressions


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="pandas-dev/asv-runner")
    ap.add_argument("--out", default="data/asv_pandas_regressions.json")
    args = ap.parse_args()
    client = GitHubClient(find_token()[0])
    issues = client.paginate(f"/repos/{args.repo}/issues", {"state": "all"}, max_pages=20)
    regs = [r for r in (parse_issue(i) for i in issues) if r is not None]
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    save_regressions(regs, args.out, source=f"https://github.com/{args.repo}/issues (taken {stamp})")
    other = Counter("pull request" if "pull_request" in i else "regression" if parse_issue(i) else "other"
                    for i in issues)
    print(f"{len(issues)} issues/PRs read ({dict(other)}), {len(regs)} regressions -> {args.out} "
          f"({client.requests} GitHub requests)")
    pcts = sorted(r.max_pct for r in regs)
    if pcts:
        print(f"worst regression per issue: median {pcts[len(pcts) // 2]:.1f} %, max {pcts[-1]:.1f} %; "
              f"with PR link: {sum(r.pr is not None for r in regs)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
