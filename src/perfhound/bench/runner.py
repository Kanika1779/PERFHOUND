"""Benchmark Runner: measure a case's benchmark at any commit.

One SAMPLE = one fresh Python process that runs the workload `inner_repeat` times;
its value is the median per-call time of those repetitions. Fresh processes make
samples close to independent (what SPRT assumes); the median inside a process
damps one-off hiccups (GC, a background app waking up).

    runner.measure_interleaved(a, b, rounds)   A B B A  A B B A ...
Interleaving cancels slow drift (thermal throttling, battery saver, other apps) that
would otherwise look like a performance change between two back-to-back blocks.

Each commit gets its own worktree (kept warm, up to max_worktrees), and one discarded
warm-up run (compiles .pyc files, fills OS caches). Threads for numeric libraries are
pinned to 1 by default (less noise; same setting for every commit).
"""

from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

from ..gateway.cases import RegressionCase
from ..gateway.worktree import WorktreeManager
from .env import project_name, workload_imports

HARNESS = Path(__file__).with_name("harness.py")
MARK = "@@PERFHOUND@@"
THREAD_VARS = ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS")


def default_memory_limit_mb() -> int | None:
    """40 % of physical RAM (needs psutil; without it there is no memory guard - only the timeout)."""
    try:
        import psutil

        return int(psutil.virtual_memory().total * 0.4 / 2**20)
    except ImportError:
        return None


def _tree_rss_mb(pid: int) -> float:
    try:
        import psutil

        p = psutil.Process(pid)
        return sum(q.memory_info().rss for q in [p, *p.children(recursive=True)]) / 2**20
    except Exception:
        return 0.0


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        import psutil

        for q in psutil.Process(proc.pid).children(recursive=True):
            q.kill()
    except Exception:
        pass
    proc.kill()
    proc.wait()


class BenchmarkError(RuntimeError):
    pass


@dataclass
class Sample:
    commit: str
    value: float                      # seconds per call (median of the process's repetitions)
    times: list[float] = field(default_factory=list)
    wall: float = 0.0                 # seconds the whole process took
    python: str = ""


class BenchmarkRunner:
    def __init__(self, case: RegressionCase, repo: str | Path, python: str | Path, *, inner_repeat: int = 5,
                 timeout: float = 900, warmup: int = 1, max_worktrees: int = 4, pin_threads: bool = True,
                 worktree_dir: str | Path | None = None, log=None, max_memory_mb: int | None = -1) -> None:
        b = case.benchmark
        if b is None or not b.workload:
            raise BenchmarkError(f"case has no workload to run ({case.case_id})")
        if b.framework != "script":
            raise BenchmarkError(f"framework {b.framework!r} not supported yet (only 'script' workloads)")
        self.case = case
        self.repo = Path(repo)
        self.python = str(python)
        self.inner_repeat = inner_repeat
        self.timeout = timeout
        self.warmup = warmup
        self.max_worktrees = max_worktrees
        self.pin_threads = pin_threads
        self.worktree_dir = worktree_dir
        self.log = log or (lambda msg: None)
        self.max_memory_mb = default_memory_limit_mb() if max_memory_mb == -1 else max_memory_mb
        self._tmp = tempfile.TemporaryDirectory(prefix="perfhound-bench-")
        self.workload_path = Path(self._tmp.name) / "workload.py"
        self.workload_path.write_text(b.workload, encoding="utf-8")
        self.module = b.params.get("module")      # else: found per worktree by _import_root
        self._trees: OrderedDict[str, WorktreeManager] = OrderedDict()
        self._warm: set[str] = set()
        self.runs = 0
        self.seconds = {"checkout": 0.0, "warmup": 0.0, "samples": 0.0}

    # -- worktrees --------------------------------------------------------------------------
    def _tree(self, commit: str) -> Path:
        if commit in self._trees:
            self._trees.move_to_end(commit)
            return self._trees[commit].path
        if len(self._trees) >= self.max_worktrees:
            evicted, old = self._trees.popitem(last=False)
            old.close()
            self._warm.discard(evicted)      # a fresh checkout has no .pyc files: warm it up again
        t0 = time.perf_counter()
        wt = WorktreeManager(self.repo, base_dir=self.worktree_dir)
        path = wt.checkout(commit)
        dt = time.perf_counter() - t0
        self.seconds["checkout"] += dt
        self.log(f"    checkout {commit[:10]}: {dt:.1f} s")
        self._trees[commit] = wt
        return path

    def _import_root(self, tree: Path) -> tuple[Path, str | None]:
        """Folder to put on PYTHONPATH, and the project package the workload must import from it."""
        candidates = [self.module] if self.module else workload_imports(self.case.benchmark.workload)
        candidates = [c for c in candidates if c] + [project_name(self.case)]
        for base in (tree, tree / "src"):
            for mod in candidates:
                if (base / mod / "__init__.py").exists() or (base / f"{mod}.py").exists():
                    return base, mod
        return tree, None

    # -- running ----------------------------------------------------------------------------
    def run_once(self, commit: str) -> Sample:
        tree = self._tree(commit)
        root, module = self._import_root(tree)
        if commit not in self._warm:
            t0 = time.perf_counter()
            for _ in range(self.warmup):
                self._spawn(commit, root, module)
            self.seconds["warmup"] += time.perf_counter() - t0
            self._warm.add(commit)
        t0 = time.perf_counter()
        sample = self._spawn(commit, root, module)
        self.seconds["samples"] += time.perf_counter() - t0
        return sample

    def _spawn(self, commit: str, root: Path, module: str | None) -> Sample:
        env = dict(os.environ)
        env.pop("PYTHONHOME", None)
        env["PYTHONPATH"] = str(root)
        env["PYTHONHASHSEED"] = "0"
        env["PYTHONWARNINGS"] = "ignore"
        env["PYTHONIOENCODING"] = "utf-8"
        if self.pin_threads:
            env.update({v: "1" for v in THREAD_VARS})
        cmd = [self.python, str(HARNESS), "--workload", str(self.workload_path), "--repeat", str(self.inner_repeat)]
        if module:
            cmd += ["--expect-module", module, "--expect-root", str(root)]
        start = time.perf_counter()
        returncode, stdout, stderr = self._run_guarded(cmd, env, commit)
        wall = time.perf_counter() - start
        self.runs += 1
        line = next((l for l in stdout.splitlines() if l.startswith(MARK)), None)
        if returncode != 0 or line is None:
            raise BenchmarkError(f"{commit[:10]}: benchmark failed (exit {returncode}):\n{stderr.strip()[-1500:]}")
        data = json.loads(line[len(MARK):])
        number = data["number"] or 1
        per_call = [t / number for t in data["times"]]
        return Sample(commit, statistics.median(per_call), per_call, wall, data.get("python", ""))

    def _run_guarded(self, cmd: list[str], env: dict, commit: str) -> tuple[int, str, str]:
        """Run one sample with a TIME and a MEMORY limit (process tree killed when exceeded).

        Why memory: some symbolic workloads (sympy-26057 charpoly of a 10x10 symbol matrix,
        sympy-26063 gauss_jordan_solve) blow up exponentially at some commits; on a laptop the
        machine starts swapping and freezes (VS Code included) long before a timeout fires.
        Output goes to files, not pipes, so a chatty workload can never deadlock us.
        """
        out_path = Path(self._tmp.name) / "stdout.txt"
        err_path = Path(self._tmp.name) / "stderr.txt"
        with open(out_path, "wb") as fo, open(err_path, "wb") as fe:
            proc = subprocess.Popen(cmd, cwd=self._tmp.name, env=env, stdout=fo, stderr=fe)
            start = time.monotonic()
            killed = None
            while proc.poll() is None:
                time.sleep(0.25)
                if time.monotonic() - start > self.timeout:
                    killed = f"timed out after {self.timeout:.0f} s"
                elif self.max_memory_mb and _tree_rss_mb(proc.pid) > self.max_memory_mb:
                    killed = f"used more than {self.max_memory_mb} MB of memory (workload blows up at this commit)"
                if killed:
                    _kill_tree(proc)
                    raise BenchmarkError(f"{commit[:10]}: benchmark {killed}")
        read = lambda p: p.read_text(encoding="utf-8", errors="replace")
        return proc.returncode, read(out_path), read(err_path)

    def measure(self, commit: str, n: int) -> list[Sample]:
        return [self.run_once(commit) for _ in range(n)]

    def measure_interleaved(self, a: str, b: str, rounds: int) -> dict[str, list[Sample]]:
        """ABBA ABBA ...: 2 samples of each commit per round."""
        out: dict[str, list[Sample]] = {a: [], b: []}
        for r in range(rounds):
            for c in (a, b, b, a):
                s = self.run_once(c)
                out[c].append(s)
                self.log(f"    round {r + 1}/{rounds} {c[:10]} {s.value * 1000:9.2f} ms   (process {s.wall:.1f} s)")
        return out

    def close(self) -> None:
        for wt in self._trees.values():
            wt.close()
        self._trees.clear()
        self._tmp.cleanup()

    def __enter__(self) -> "BenchmarkRunner":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
