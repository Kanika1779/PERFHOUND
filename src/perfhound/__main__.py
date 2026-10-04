"""Perfhound command line.

    perfhound sources                                   list input sources
    perfhound cases yaml -o path=perfhound.yaml         list the cases a source produces
    perfhound candidates perfhound.yaml                 gateway output for every case in a spec
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


def cmd_candidates(args) -> int:
    from perfhound.gateway import Gateway
    from perfhound.gateway.sources.yaml_spec import load_spec

    cases = load_spec(args.spec)
    if args.case:
        cases = [c for c in cases if c.case_id.endswith(":" + args.case)]
        if not cases:
            raise SystemExit(f"no case named {args.case!r} in {args.spec}")
    for case in cases:
        with Gateway.for_case(case) as gw:
            cands = gw.candidates_for(case.for_localizer(), analyze=not args.no_analyze)
            if args.json:
                for c in cands:
                    print(c.to_json())
                continue
            print(f"== {case.case_id}: {len(cands)} candidates ({gw.last_stats.seconds:.1f} s, "
                  f"{gw.last_stats.commit_cache_hits} from cache)")
            for c in cands:
                fns = list(c.changed_functions) + [f"+{f}" for f in c.added_functions] + [f"-{f}" for f in c.deleted_functions]
                more = f" (+{len(fns) - 3} more)" if len(fns) > 3 else ""
                print(f"{c.position:>4}  {c.short_sha}  {c.subject[:50]:<50}  {', '.join(fns[:3])}{more}")
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
    s.set_defaults(func=cmd_candidates)
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
