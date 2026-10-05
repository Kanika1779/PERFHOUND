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
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..gateway.cases import RegressionCase

# Python versions with broad Windows/Linux wheel support at a given time (numpy, pandas).
# 3.8 is the floor: uv no longer ships 3.7 builds (seen on Windows, 2026-10).
_PY_BY_DATE = [("2021-07-01", "3.8"), ("2022-07-01", "3.9"),
               ("2023-07-01", "3.10"), ("2024-07-01", "3.11"), ("9999-12-31", "3.12")]
IMPORT_TO_DIST = {"tlz": "toolz", "yaml": "pyyaml", "sklearn": "scikit-learn", "PIL": "pillow",
                  "cv2": "opencv-python", "bs4": "beautifulsoup4", "dateutil": "python-dateutil"}
PROJECT_DIST = {"dask": "dask[array,dataframe]"}


class EnvError(RuntimeError):
    pass


# CPython patch releases (minor, version, release date). The PATCH matters too: later 3.11 / 3.12 patch
# releases changed inspect.signature for descriptors, which breaks 2023-era dask+pandas on import
# (seen on Windows 2026-10-05: "descriptor '__call__' for 'type' objects doesn't apply to a 'property'
# object"). So, like packages, Python is pinned to the newest patch that existed on the commit date.
_PATCHES = {
    "3.8": [("3.8.1", "2019-12-18"), ("3.8.2", "2020-02-24"), ("3.8.3", "2020-05-13"), ("3.8.5", "2020-07-20"),
            ("3.8.6", "2020-09-23"), ("3.8.7", "2020-12-21"), ("3.8.8", "2021-02-19"), ("3.8.10", "2021-05-03")],
    "3.9": [("3.9.6", "2021-06-28"), ("3.9.7", "2021-08-30"), ("3.9.9", "2021-11-15"), ("3.9.10", "2022-01-14"),
            ("3.9.12", "2022-03-23"), ("3.9.13", "2022-05-17")],
    "3.10": [("3.10.5", "2022-06-06"), ("3.10.6", "2022-08-02"), ("3.10.7", "2022-09-06"), ("3.10.8", "2022-10-11"),
             ("3.10.9", "2022-12-06"), ("3.10.10", "2023-02-08"), ("3.10.11", "2023-04-05")],
    "3.11": [("3.11.4", "2023-06-06"), ("3.11.5", "2023-08-24"), ("3.11.6", "2023-10-02"), ("3.11.7", "2023-12-04"),
             ("3.11.8", "2024-02-06"), ("3.11.9", "2024-04-02")],
    "3.12": [("3.12.4", "2024-06-06"), ("3.12.5", "2024-08-06"), ("3.12.6", "2024-09-06"), ("3.12.7", "2024-10-01"),
             ("3.12.8", "2024-12-03")],
}


def python_for_date(when: datetime) -> str:
    """Python minor with broad wheel support at that date, pinned to the patch release current then."""
    day = when.date().isoformat()
    minor = next(py for limit, py in _PY_BY_DATE if day < limit)
    released = [v for v, d in _PATCHES.get(minor, []) if d <= day]
    return released[-1] if released else minor


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
        t0 = time.perf_counter()
        parts = spec.python.split(".")
        minor = int(parts[1])
        candidates = [spec.python] + ([f"3.{minor}"] if len(parts) > 2 else []) + [f"3.{minor + 1}"]
        errors = []
        for py in candidates:              # exact patch -> same minor -> next minor (logged when used)
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
            if p.returncode != 0 and "exclude-newer" in p.stderr and "setuptools" in p.stderr:
                # 2016-2019 packages (e.g. mpmath) ship only sdists; building them needs setuptools>=40.8,
                # which did not exist on that date. Build tools are not the code under test: install a
                # current setuptools into the env and build without isolation.
                self._log("old sdist needs newer setuptools: retrying with --no-build-isolation")
                self._run([*uv, "pip", "install", "--python", str(_venv_python(d)), "setuptools<70", "wheel"])
                p = self._run([*cmd, "--no-build-isolation", *spec.requirements])
            if p.returncode != 0:
                errors.append(f"install on python {py}: {p.stderr.strip()[-1500:]}")
                self._log(f"install on python {py} failed: {_last_line(p.stderr)}")
                continue
            ready.write_text(json.dumps({**asdict(spec), "python_used": py}, indent=2), encoding="utf-8")
            self._log(f"env ready in {time.perf_counter() - t0:.0f} s")
            return _venv_python(d)
        shutil.rmtree(d, ignore_errors=True)
        raise EnvError("could not build benchmark environment:\n" + "\n---\n".join(errors))

    def _run(self, cmd: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")

    def _log(self, msg: str) -> None:
        if self.verbose:
            print("  [env] " + msg, flush=True)
