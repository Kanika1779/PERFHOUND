import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from perfhound.bench import (BenchmarkError, BenchmarkRunner, EnvManager, EnvSpec, infer_env, python_for_date,
                             ratio_ci, verdict)
from perfhound.bench.env import third_party, workload_imports
from perfhound.gateway.cases import BenchmarkSpec, RegressionCase
from perfhound.gateway.gitcmd import run_git

WORKLOAD = '''
import timeit
import statistics
from slowpkg.core import work

def setup():
    pass

def workload():
    """from a docstring: this line must not count as an import"""
    work()

runtimes = timeit.repeat(workload, number=2, repeat=10, setup=setup)
print("Mean:", statistics.mean(runtimes))
'''


def _commit(repo, delay, msg):
    (repo / "slowpkg").mkdir(exist_ok=True)
    (repo / "slowpkg" / "__init__.py").write_text("")
    (repo / "slowpkg" / "core.py").write_text(f"import time\n\ndef work():\n    time.sleep({delay})\n")
    run_git(repo, "add", "-A")
    run_git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", msg)
    return run_git(repo, "rev-parse", "HEAD").stdout.strip()


@pytest.fixture
def slow_fast_repo(tmp_path):
    repo = tmp_path / "proj"
    repo.mkdir()
    run_git(repo, "init", "-q")
    slow = _commit(repo, 0.03, "slow")
    fast = _commit(repo, 0.003, "Speed up work")
    return repo, slow, fast


def make_case(repo, good, bad, workload=WORKLOAD):
    return RegressionCase(case_id="t:1", source="t", repo=str(repo), language="python", good=good, bad=bad,
                          direction="faster",
                          benchmark=BenchmarkSpec(name="workload", framework="script", workload=workload))


def test_runner_measures_the_commit_in_the_worktree(slow_fast_repo, tmp_path):
    repo, slow, fast = slow_fast_repo
    case = make_case(repo, slow, fast)
    with BenchmarkRunner(case, repo, sys.executable, inner_repeat=3, worktree_dir=tmp_path / "wt") as r:
        s = r.measure_interleaved(slow, fast, rounds=1)
        assert [len(s[slow]), len(s[fast])] == [2, 2]
        assert all(len(x.times) == 3 for x in s[slow])          # our repeat count, not the workload's 10
        assert min(x.value for x in s[slow]) > 0.02 and max(x.value for x in s[fast]) < 0.015   # per call (number=2)
        assert r.runs == 4 + 2                                  # + one warm-up per commit
    ratio, lo, hi = ratio_ci([x.value for x in s[slow]], [x.value for x in s[fast]])
    assert verdict(ratio, lo, hi) == "faster"
    assert run_git(repo, "status", "--porcelain").stdout == ""   # user's checkout untouched


def test_wrong_code_under_test_is_caught(slow_fast_repo, tmp_path):
    """If the package is imported from anywhere but the worktree (e.g. site-packages), the sample must fail."""
    import os
    import subprocess

    from perfhound.bench.runner import HARNESS

    repo, slow, _ = slow_fast_repo
    elsewhere = tmp_path / "elsewhere" / "slowpkg"
    elsewhere.mkdir(parents=True)
    (elsewhere / "__init__.py").write_text("")
    (elsewhere / "core.py").write_text("def work():\n    pass\n")
    wl = tmp_path / "w.py"
    wl.write_text(WORKLOAD)
    p = subprocess.run([sys.executable, str(HARNESS), "--workload", str(wl), "--repeat", "2",
                        "--expect-module", "slowpkg", "--expect-root", str(repo)],
                       env=dict(os.environ, PYTHONPATH=str(elsewhere.parent)), capture_output=True, text=True)
    assert p.returncode == 5 and "WRONG CODE UNDER TEST" in p.stderr


def test_unsupported_workload_format(slow_fast_repo, tmp_path):
    repo, slow, fast = slow_fast_repo
    case = make_case(repo, slow, fast, workload="from slowpkg.core import work\nwork()\n")
    with BenchmarkRunner(case, repo, sys.executable, worktree_dir=tmp_path / "wt") as r:
        with pytest.raises(BenchmarkError, match="never called timeit.repeat"):
            r.run_once(slow)


def test_case_without_workload_is_rejected(slow_fast_repo):
    repo, slow, fast = slow_fast_repo
    case = make_case(repo, slow, fast, workload=None)
    with pytest.raises(BenchmarkError, match="no workload"):
        BenchmarkRunner(case, repo, sys.executable)


def test_env_inference():
    assert python_for_date(datetime(2016, 5, 1, tzinfo=timezone.utc)) == "3.8"
    assert python_for_date(datetime(2022, 8, 1, tzinfo=timezone.utc)) == "3.10"
    assert python_for_date(datetime(2026, 1, 1, tzinfo=timezone.utc)) == "3.12"
    mods = workload_imports(WORKLOAD + "\nimport numpy as np\nimport tlz\nfrom dask.array import ones\n")
    assert mods == ["timeit", "statistics", "slowpkg", "numpy", "tlz", "dask"]      # no "a" from the docstring
    assert third_party(mods, project="dask") == ["slowpkg", "numpy", "toolz"]
    case = RegressionCase(case_id="c", source="t", repo="https://github.com/dask/dask", language="python",
                          good="a" * 40, bad="b" * 40, metadata={"upstream_repo": "dask/dask"},
                          benchmark=BenchmarkSpec(name="workload", framework="script",
                                                  workload="import dask.bag as db\nimport numpy as np\nimport timeit\n"))
    spec = infer_env(case, datetime(2019, 10, 2, 12, 30, tzinfo=timezone.utc))
    assert spec.python == "3.8" and spec.requirements == ("dask[array,dataframe]", "numpy")
    assert spec.exclude_newer == "2019-10-02T12:30:00Z"


def test_env_override_and_existing_interpreter(tmp_path):
    case = RegressionCase(case_id="c", source="t", repo="r", language="python", good="a" * 40, bad="b" * 40,
                          benchmark=BenchmarkSpec(name="w", framework="script", workload="import x",
                                                  params={"python": sys.executable, "requirements": ["x==1"]}))
    spec = infer_env(case, datetime(2020, 1, 1, tzinfo=timezone.utc))
    assert spec.requirements == ("x==1",)
    assert EnvManager(tmp_path).python_for(spec) == Path(sys.executable)        # no venv built
    assert EnvSpec("3.9", ("a",), "x").key() != EnvSpec("3.9", ("b",), "x").key()


def test_ratio_ci_and_verdict():
    before = [1.0, 1.02, 0.98, 1.01, 0.99, 1.0]
    r, lo, hi = ratio_ci(before, [0.5, 0.51, 0.49, 0.5, 0.52, 0.5])
    assert verdict(r, lo, hi) == "faster" and lo <= r <= hi
    r, lo, hi = ratio_ci(before, [1.0, 1.01, 0.99, 1.02, 0.98, 1.0])
    assert verdict(r, lo, hi) == "unclear"
    r, lo, hi = ratio_ci(before, [1.3, 1.31, 1.29, 1.3, 1.32, 1.3])
    assert verdict(r, lo, hi) == "slower"


def test_workload_audit():
    from perfhound.bench import audit_workload

    assert audit_workload(WORKLOAD) == ("clean", [])
    assert audit_workload(None)[0] == "no_workload"
    assert audit_workload("def f(:\n")[0] == "syntax_error"
    body = "\n".join(f"    x{i} = {i}" for i in range(15))
    copied = f"# _primepi copied directly from the post-edit source file\ndef _primepi(n):\n{body}\n    return n\n" + WORKLOAD
    status, why = audit_workload(copied)
    assert status == "inlines_code" and "copied" in why and any("_primepi" in w for w in why)
    assert audit_workload("# a copy of the data\n" + WORKLOAD)[0] == "mentions_copy"
