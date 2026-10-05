"""Perfhound command line.

    perfhound sources                                   list input sources
    perfhound cases yaml -o path=perfhound.yaml         list the cases a source produces
    perfhound candidates perfhound.yaml                 gateway output for every case in a spec
    perfhound localize perfhound.yaml                   find the culprit (the whole pipeline, live)
    perfhound github login | whoami | logout            GitHub token (optional: adds PR titles/descriptions)
    perfhound --version
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from perfhound import __version__


def _coerce(value: str) -> Any:
    """'20' -> 20, 'true' -> True, '[1,2]' -> [1, 2], anything else stays a string."""
    try:
        return json.loads(value)
    except ValueError:
        return value


def _options(pairs: list[str]) -> dict[str, Any]:
    out = {}
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"option {pair!r} must look like key=value")
        key, value = pair.split("=", 1)
        out[key.strip().replace("-", "_")] = _coerce(value)
    return out


def cmd_sources(args) -> int:
    from perfhound.gateway.sources import available_sources

    for name in available_sources():
        print(name)
    return 0


def cmd_cases(args) -> int:
    from perfhound.gateway.sources import open_source

    adapter = open_source(args.source, **_options(args.option))
    for case in adapter.cases(limit=args.limit):
        shown = case if args.show_truth else case.for_localizer()
        if args.json:
            print(shown.to_json())
        else:
            truth = f"  culprit={case.culprit[:10]} ({case.ground_truth})" if args.show_truth and case.culprit else ""
            print(f"{case.case_id}  {case.language}  {case.good[:10]}..{case.bad[:10]}  {case.direction}{truth}")
    for failure in getattr(adapter, "failures", []):
        print(f"SKIPPED {failure.instance_id}: {failure.reason} {failure.detail[:120]}", file=sys.stderr)
    return 0


def _github_provider(fields: str = "title+body"):
    """GitHub PR provider with the saved/env token (works without a token too: 60 requests/hour)."""
    from perfhound.gateway.cache import Cache
    from perfhound.gateway.github import GitHubClient, GitHubPRProvider, find_token

    token, _ = find_token()
    return GitHubPRProvider(GitHubClient(token), Cache(), fields=fields)


def cmd_github(args) -> int:
    from perfhound.gateway.github import GitHubClient, GitHubError, delete_token, find_token, mask, save_token

    if args.action == "login":
        import getpass

        token = (args.token or getpass.getpass("GitHub token (input hidden): ")).strip()
        if not token:
            raise SystemExit("no token given")
        client = GitHubClient(token)
        user = client.get_json("/user")                      # check BEFORE saving
        path = save_token(token)
        print(f"logged in as {user.get('login')} - token {mask(token)} saved to {path}")
        print(f"requests left this hour: {client.rate_remaining}/{client.rate_limit}")
        return 0
    if args.action == "logout":
        print("token removed" if delete_token() else "no saved token")
        token, source = find_token()
        if token:
            print(f"note: a token is still set by {source}")
        return 0
    token, source = find_token()
    client = GitHubClient(token)
    if not token:
        rate = client.get_json("/rate_limit").get("rate", {})
        print(f"no token (anonymous: {rate.get('remaining')}/{rate.get('limit')} requests left this hour) - "
              f"`perfhound github login` raises the limit to 5,000")
        return 0
    try:
        user = client.get_json("/user")
    except GitHubError as e:
        print(f"token {mask(token)} from {source}: {e}")
        return 1
    print(f"{user.get('login')}  (token {mask(token)} from {source})")
    print(f"requests left this hour: {client.rate_remaining}/{client.rate_limit}")
    return 0


def cmd_candidates(args) -> int:
    from perfhound.gateway import Gateway
    from perfhound.gateway.sources.yaml_spec import load_spec

    cases = load_spec(args.spec)
    if args.case:
        cases = [c for c in cases if c.case_id.endswith(":" + args.case)]
        if not cases:
            raise SystemExit(f"no case named {args.case!r} in {args.spec}")
    github = _github_provider(args.github_fields) if args.github else None
    for case in cases:
        with Gateway.for_case(case, github=github) as gw:
            cands = gw.candidates_for(case.for_localizer(), analyze=not args.no_analyze)
            for w in gw.last_stats.github_warnings:
                print(f"warning: {w}", file=sys.stderr)
            if args.json:
                for c in cands:
                    print(c.to_json())
                continue
            st = gw.last_stats
            gh = f", GitHub: {st.github_with_pr} with PR, {st.github_requests} requests" if args.github else ""
            print(f"== {case.case_id}: {len(cands)} candidates ({st.seconds:.1f} s, {st.commit_cache_hits} from cache{gh})")
            for c in cands:
                fns = list(c.changed_functions) + [f"+{f}" for f in c.added_functions] + [f"-{f}" for f in c.deleted_functions]
                more = f" (+{len(fns) - 3} more)" if len(fns) > 3 else ""
                pr = f"#{c.pr.number:<6}" if c.pr else " " * 7
                print(f"{c.position:>4}  {c.short_sha}  {pr} {c.subject[:50]:<50}  {', '.join(fns[:3])}{more}")
    return 0


def cmd_localize(args) -> int:
    from perfhound.gateway.sources.yaml_spec import load_spec
    from perfhound.localize import localize_case, write_report

    cases = load_spec(args.spec)
    if args.case:
        cases = [c for c in cases if c.case_id.endswith(":" + args.case)]
        if not cases:
            raise SystemExit(f"no case named {args.case!r} in {args.spec}")
    for case in cases:
        print(f"== {case.case_id}")
        report = localize_case(case, retriever=args.retriever, llm="none" if args.no_llm else "auto", model=args.model,
                               dry_run=args.dry_run, runner_options={"inner_repeat": args.repeat},
                               github=_github_provider(args.github_fields) if args.github else None)
        if args.report:
            write_report(report, args.report if len(cases) == 1 else f"{args.report}.{case.case_id.split(':')[-1]}.json")
        if case.culprit and report.get("culprit"):          # evaluation spec with a known answer
            print("  (known culprit: " + ("MATCH)" if report["culprit"].startswith(case.culprit) or case.culprit.startswith(report["culprit"]) else f"{case.culprit[:10]} - MISS)"))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="perfhound", description="Find the commit that changed your code's speed.")
    p.add_argument("--version", "-V", action="version", version=f"perfhound {__version__}")
    sub = p.add_subparsers(dest="command")

    s = sub.add_parser("sources", help="list input sources")
    s.set_defaults(func=cmd_sources)

    s = sub.add_parser("cases", help="list the cases a source produces")
    s.add_argument("source")
    s.add_argument("-o", "--option", action="append", default=[], metavar="KEY=VALUE",
                   help="adapter option, e.g. -o path=perfhound.yaml -o n=20")
    s.add_argument("--limit", type=int)
    s.add_argument("--json", action="store_true")
    s.add_argument("--show-truth", action="store_true", help="also print the culprit (evaluation only)")
    s.set_defaults(func=cmd_cases)

    s = sub.add_parser("candidates", help="run the gateway on the cases of a perfhound.yaml")
    s.add_argument("spec")
    s.add_argument("--case", help="only the case with this name")
    s.add_argument("--json", action="store_true")
    s.add_argument("--no-analyze", action="store_true", help="skip function-level analysis")
    s.add_argument("--github", action="store_true", help="attach the PR behind each commit (GitHub repos)")
    s.add_argument("--github-fields", choices=["title", "title+body"], default="title+body")
    s.set_defaults(func=cmd_candidates)

    s = sub.add_parser("localize", help="find the commit that changed performance (whole pipeline, live)")
    s.add_argument("spec")
    s.add_argument("--case", help="only the case with this name")
    s.add_argument("--dry-run", action="store_true", help="show the prior (suspects) without running benchmarks")
    s.add_argument("--no-llm", action="store_true", help="retrieval prior only")
    s.add_argument("--retriever", choices=["auto", "hybrid", "bm25"], default="auto")
    s.add_argument("--model", help="Gemini model (default: perfhound.llm.client.DEFAULT_MODEL)")
    s.add_argument("--repeat", type=int, default=5, help="repetitions inside one process (script workloads)")
    s.add_argument("--report", help="write a JSON report here")
    s.add_argument("--github", action="store_true", help="add PR titles/descriptions to what RAG + LLM read")
    s.add_argument("--github-fields", choices=["title", "title+body"], default="title+body",
                   help="'title' is safer for evaluations (descriptions can be edited after the merge)")
    s.set_defaults(func=cmd_localize)

    s = sub.add_parser("github", help="GitHub token: login / whoami / logout")
    s.add_argument("action", choices=["login", "whoami", "logout"])
    s.add_argument("--token", help="token for login (default: asked for, hidden)")
    s.set_defaults(func=cmd_github)
    return p


def main(argv: list[str] | None = None) -> int:
    # Windows consoles are often not UTF-8: never crash on a commit message with unusual characters.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    if not getattr(args, "func", None):
        build_parser().print_help()
        return 0
    try:
        return args.func(args)
    except Exception as exc:   # one clean line for the user; tracebacks only with PERFHOUND_DEBUG=1
        import os

        if os.environ.get("PERFHOUND_DEBUG"):
            raise
        print(f"perfhound: error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
