"""Benchmark environments: the right Python and dependency versions for an OLD commit.

A 2019 dask commit does not import with 2026 numpy/pandas. So each case gets a venv
built with uv:
    python        chosen by commit date (a version that had wheels then), or params["python"]
    requirements  the project's PyPI distribution + third-party modules the workload imports
    --exclude-newer <commit date>   uv resolves every package AS IT EXISTED THAT DAY
The project's code under test is NOT the installed copy: the runner puts the commit's
worktree first on PYTHONPATH, and the harness verifies the import came from there.
Limitation: works for pure-Python projects (sympy, dask, xarray). Projects with C
extensions that change per commit (pandas) need a per-commit build - not supported yet.

Envs live in ~/.perfhound/envs/<hash>; uv's cache hard-links packages, so many envs are cheap.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..gateway.cases import RegressionCase

# Python versions with broad Windows/Linux wheel support at a given time (numpy, pandas).
_PY_BY_DATE = [("2020-07-01", "3.7"), ("2021-07-01", "3.8"), ("2022-07-01", "3.9"),
               ("2023-07-01", "3.10"), ("2024-07-01", "3.11"), ("9999-12-31", "3.12")]
IMPORT_TO_DIST = {"tlz": "toolz", "yaml": "pyyaml", "sklearn": "scikit-learn", "PIL": "pillow",
                  "cv2": "opencv-python", "bs4": "beautifulsoup4", "dateutil": "python-dateutil"}
PROJECT_DIST = {"dask": "dask[array,dataframe]"}


class EnvError(RuntimeError):
    pass


def python_for_date(when: datetime) -> str:
    day = when.date().isoformat()
    return next(py for limit, py in _PY_BY_DATE if day < limit)


def project_name(case: RegressionCase) -> str:
    upstream = case.metadata.get("upstream_repo") or case.repo
    return upstream.rstrip("/").split("/")[-1].removesuffix(".git").lower()


def workload_imports(source: str) -> list[str]:
    """Top-level module names the workload really imports (ast, not regex: docstrings say 'from a ...')."""
    names: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names += [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.append(node.module.split(".")[0])
    return list(dict.fromkeys(names))


def third_party(modules: list[str], *, project: str) -> list[str]:
    std = set(sys.stdlib_module_names)
    out = []
    for m in modules:
        if m in std or m == project or m.startswith("_"):
            continue
        out.append(IMPORT_TO_DIST.get(m, m))
    return list(dict.fromkeys(out))


@dataclass(frozen=True)
class EnvSpec:
    python: str                       # "3.9", or the path of an existing interpreter (no venv built)
    requirements: tuple[str, ...] = ()
    exclude_newer: str | None = None  # RFC 3339 timestamp

    def key(self) -> str:
        return hashlib.sha1(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()[:16]


def infer_env(case: RegressionCase, commit_date: datetime) -> EnvSpec:
    params = case.benchmark.params if case.benchmark else {}
    project = project_name(case)
    python = params.get("python") or python_for_date(commit_date)
    if params.get("requirements"):
        reqs = list(params["requirements"])
    else:
        reqs = [PROJECT_DIST.get(project, project)]
        if case.benchmark and case.benchmark.workload:
            reqs += third_party(workload_imports(case.benchmark.workload), project=project)
    stamp = commit_date.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return EnvSpec(python=python, requirements=tuple(reqs), exclude_newer=stamp)


def _venv_python(d: Path) -> Path:
    return d / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _uv() -> list[str]:
    try:
        from uv import find_uv_bin  # type: ignore

        return [find_uv_bin()]
    except Exception:
        exe = shutil.which("uv")
        if exe:
            return [exe]
    raise EnvError("uv is not installed:  pip install uv   (or pip install -e \".[bench]\")")


def _last_line(text: str) -> str:
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    return " | ".join(lines[-3:])[:400] if lines else "(no error text)"


def default_envs_dir() -> Path:
    base = os.environ.get("PERFHOUND_CACHE_DIR")
    return (Path(base) if base else Path.home() / ".perfhound") / "envs"


@dataclass
class EnvManager:
    base_dir: Path = field(default_factory=default_envs_dir)
    verbose: bool = True

    def python_for(self, spec: EnvSpec) -> Path:
        if os.path.isfile(spec.python):                       # existing interpreter (tests, power users)
            return Path(spec.python)
        d = Path(self.base_dir) / spec.key()
        ready = d / "perfhound-env.json"
        if ready.exists():
            return _venv_python(d)
        uv = _uv()
        minor = int(spec.python.split(".")[1])
        errors = []
        for py in (spec.python, f"3.{minor + 1}"):           # e.g. no 3.7 build for this platform -> 3.8
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)
            self._log(f"creating env {d.name}: python {py}, {len(spec.requirements)} requirements as of {spec.exclude_newer}")
            p = self._run([*uv, "venv", "--python", py, str(d)])
            if p.returncode != 0:
                errors.append(f"venv python {py}: {p.stderr.strip()[-600:]}")
                self._log(f"python {py} failed: {_last_line(p.stderr)}")
                continue
            cmd = [*uv, "pip", "install", "--python", str(_venv_python(d))]
            if spec.exclude_newer:
                cmd += ["--exclude-newer", spec.exclude_newer]
            p = self._run([*cmd, *spec.requirements])
            if p.returncode != 0:
                errors.append(f"install on python {py}: {p.stderr.strip()[-1500:]}")
                self._log(f"install on python {py} failed: {_last_line(p.stderr)}")
                continue
            ready.write_text(json.dumps({**asdict(spec), "python_used": py}, indent=2), encoding="utf-8")
            return _venv_python(d)
        shutil.rmtree(d, ignore_errors=True)
        raise EnvError("could not build benchmark environment:\n" + "\n---\n".join(errors))

    def _run(self, cmd: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")

    def _log(self, msg: str) -> None:
        if self.verbose:
            print("  [env] " + msg, flush=True)
