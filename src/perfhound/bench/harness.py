"""Runs ONE benchmark sample inside the benchmark environment (a separate Python).

Standalone on purpose: it runs under the case's own interpreter (possibly Python 3.7),
so it must not import perfhound and must stay Python 3.7 compatible.

    python harness.py --workload W.py --repeat 5 --expect-module dask --expect-root <worktree>

Executes the workload script with timeit.repeat patched so the raw timings are
captured (and the repeat count is ours), then checks that the code under test
was imported from the worktree - not from site-packages. Prints one line:
    @@PERFHOUND@@{"times": [...], "number": 1, "module_file": "..."}
"""

import argparse
import io
import json
import os
import sys
import timeit

MARK = "@@PERFHOUND@@"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workload", required=True)
    ap.add_argument("--repeat", type=int, default=5)
    ap.add_argument("--expect-module", default=None)
    ap.add_argument("--expect-root", default=None)
    args = ap.parse_args()

    captured = []
    original = timeit.repeat

    def patched(*a, **kw):
        a = list(a)
        if len(a) >= 4:
            a[3] = args.repeat
        else:
            kw["repeat"] = args.repeat
        number = kw.get("number", a[4] if len(a) >= 5 else 1000000)
        times = original(*a, **kw)
        captured.append({"number": number, "times": list(times)})
        return times

    timeit.repeat = patched
    with open(args.workload, "r", encoding="utf-8") as fh:
        source = fh.read()
    glb = {"__name__": "__main__", "__file__": args.workload}
    real_stdout = sys.stdout
    sys.stdout = io.StringIO()          # the workload's own prints are not our output
    try:
        exec(compile(source, args.workload, "exec"), glb)
    finally:
        sys.stdout = real_stdout

    if not captured:
        sys.stderr.write("workload never called timeit.repeat - unsupported workload format\n")
        return 3
    module_file = None
    if args.expect_module:
        mod = sys.modules.get(args.expect_module)
        module_file = getattr(mod, "__file__", None) if mod else None
        if not module_file:
            sys.stderr.write("module %r was not imported by the workload\n" % args.expect_module)
            return 4
        root = os.path.normcase(os.path.realpath(args.expect_root))
        got = os.path.normcase(os.path.realpath(module_file))
        if not got.startswith(root + os.sep):
            sys.stderr.write("WRONG CODE UNDER TEST: %s imported from %s, expected under %s\n"
                             % (args.expect_module, module_file, args.expect_root))
            return 5
    last = captured[-1]
    print(MARK + json.dumps({"times": last["times"], "number": last["number"], "module_file": module_file,
                             "python": sys.version.split()[0]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
