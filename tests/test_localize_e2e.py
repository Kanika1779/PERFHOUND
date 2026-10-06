"""End to end: perfhound.yaml -> perfhound localize -> the right culprit, on a real (tiny) git repo.

No network, no LLM (retrieval prior only), real benchmark processes."""

import json
import sys

from perfhound.__main__ import main
from perfhound.gateway.gitcmd import run_git

BENCH = '''import time
from slowpkg.core import work
t = time.perf_counter()
for _ in range(3):
    work()
print(f"took {(time.perf_counter() - t) / 3:.6f} s")
'''


def commit(repo, files, msg):
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
    run_git(repo, "add", "-A")
    run_git(repo, "-c", "user.name=dev", "-c", "user.email=dev@x", "commit", "-q", "-m", msg)
    return run_git(repo, "rev-parse", "HEAD").stdout.strip()


def core(delay, extra=""):
    return f"import time\n\n\ndef work():\n    time.sleep({delay}){extra}\n\n\ndef helper():\n    return 1\n"


def test_localize_finds_the_regression(tmp_path, capsys):
    repo = tmp_path / "proj"
    repo.mkdir()
    run_git(repo, "init", "-q")
    commit(repo, {"slowpkg/__init__.py": "", "slowpkg/core.py": core(0.02), "bench.py": BENCH,
                  "README.md": "proj\n"}, "initial")
    run_git(repo, "tag", "v1")
    history = [({"README.md": "proj\n\nusage\n"}, "Docs: usage section"),
               ({"slowpkg/core.py": core(0.02).replace("return 1", "return 2")}, "Refactor helper"),
               ({"tests/test_core.py": "def test_x():\n    pass\n"}, "Add tests"),
               ({"slowpkg/core.py": core(0.045).replace("return 1", "return 2")}, "Validate input in work"),  # culprit
               ({"setup.cfg": "[metadata]\nname = proj\n"}, "Packaging metadata"),
               ({"README.md": "proj\n\nusage\n\nfaq\n"}, "Docs: FAQ"),
               ({"tests/test_more.py": "def test_y():\n    pass\n"}, "More tests"),
               ({"CHANGELOG.md": "1.1\n"}, "Changelog")]
    shas = [commit(repo, files, msg) for files, msg in history]
    culprit = shas[3]
    spec = tmp_path / "perfhound.yaml"
    spec.write_text(f"""repo: {repo.as_posix()}
good: v1
bad: {shas[-1]}
direction: slower
culprit: {culprit}
benchmark:
  name: work_loop
  command: '"{sys.executable}" bench.py'
  metric: {{type: regex, pattern: "took ([0-9.]+) s"}}
""")
    report = tmp_path / "report.json"
    assert main(["localize", str(spec), "--no-llm", "--retriever", "bm25", "--report", str(report)]) == 0
    out = capsys.readouterr().out
    r = json.loads(report.read_text())
    assert r["culprit"] == culprit, out
    assert r["stopped"] == "confident" and r["confidence"] >= 0.95
    assert "MATCH" in out and "change confirmed" in out
    assert run_git(repo, "status", "--porcelain").stdout == ""          # user's checkout untouched


def test_dry_run_shows_suspects_without_benchmarking(tmp_path, capsys):
    repo = tmp_path / "p"
    repo.mkdir()
    run_git(repo, "init", "-q")
    commit(repo, {"slowpkg/__init__.py": "", "slowpkg/core.py": core(0.01), "bench.py": BENCH}, "init")
    run_git(repo, "tag", "v1")
    commit(repo, {"README.md": "x\n"}, "docs")
    last = commit(repo, {"slowpkg/core.py": core(0.03)}, "Rewrite work loop")
    spec = tmp_path / "s.yaml"
    spec.write_text(f"repo: {repo.as_posix()}\ngood: v1\nbad: {last}\nbenchmark: python bench.py\n")
    assert main(["localize", str(spec), "--no-llm", "--retriever", "bm25", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "most suspicious commits" in out and "Rewrite work loop" in out and "calibration" not in out


def test_read_metric():
    import pytest

    from perfhound.bench.runner import BenchmarkError, read_metric

    assert read_metric({"type": "wall_time"}, "", 1.5) == 1.5
    assert read_metric({"type": "regex", "pattern": r"took ([0-9.]+)"}, "took 1\ntook 2.5 s", 9) == 2.5
    assert read_metric({"type": "json", "key": "stats.mean"}, 'noise\n{"stats": {"mean": 0.25}}\n', 9) == 0.25
    with pytest.raises(BenchmarkError):
        read_metric({"type": "regex", "pattern": r"took ([0-9.]+)"}, "nothing", 9)


def test_commit_date_of_an_annotated_tag(tmp_path):
    """Release tags are often annotated/signed (networkx-3.3): the date must be the commit's, not a crash."""
    from perfhound.localize import commit_date

    repo = tmp_path / "r"
    repo.mkdir()
    run_git(repo, "init", "-q")
    sha = commit(repo, {"a.py": "x = 1\n"}, "one")
    run_git(repo, "-c", "user.name=rel", "-c", "user.email=rel@x", "tag", "-a", "v1", "-m", "release v1\n\nnotes")
    want = run_git(repo, "show", "-s", "--format=%ct", sha).stdout.strip()
    assert int(commit_date(repo, "v1").timestamp()) == int(want) == int(commit_date(repo, sha).timestamp())
