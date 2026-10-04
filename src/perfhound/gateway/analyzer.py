"""Code Analyzer (gateway part 6): which Python functions did a commit change?

Approach: parse the file BEFORE (first parent) and AFTER the commit with
Python's `ast` module and compare each function's normalized syntax tree.

Why AST comparison instead of mapping diff line numbers to functions:
* comment / whitespace / docstring-only edits are NOT reported (they
  cannot change performance), line-mapping would report them;
* a function that only moved inside the file is NOT reported;
* no diff parsing (git quotes unusual paths in patch headers).
Cost: we know WHICH function changed, not which line.

Naming follows Python's __qualname__, prefixed by the module:
    pkg.mod.func                 top-level function
    pkg.mod.Class.method         method
    pkg.mod.outer.<locals>.inner nested function
Two pseudo-entries can appear in changed_functions:
    pkg.mod.<module>             module-level code changed (imports, constants...)
    pkg.mod.Class                class-level code changed (attributes, bases, decorators)

A change inside a nested function also marks every enclosing function as
changed (their source really did change).

Files that fail to parse (e.g. syntax newer than the running Python) are
skipped and listed in FunctionChanges.skipped_files - never silently.
"""

from __future__ import annotations

import ast
import copy
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable, Sequence

from .gitcmd import read_blobs
from .models import CandidateCommit

DEFAULT_MAX_FILE_BYTES = 2_000_000
_FUNC_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)


@dataclass(frozen=True)
class FunctionChanges:
    changed: tuple[str, ...] = ()
    added: tuple[str, ...] = ()
    deleted: tuple[str, ...] = ()
    skipped_files: tuple[str, ...] = ()


# --------------------------------------------------------------------------
# pure helpers (no git) - easy to unit test
# --------------------------------------------------------------------------

def path_to_module(path: str) -> str:
    """'sympy/core/basic.py' -> 'sympy.core.basic'; 'pkg/__init__.py' -> 'pkg'.

    A leading 'src/' is dropped (src-layout), matching how the code is imported.
    """
    p = path.replace("\\", "/")
    if p.startswith("src/"):
        p = p[4:]
    if p.endswith(".py"):
        p = p[:-3]
    parts = [x for x in p.split("/") if x]
    if parts and parts[-1] == "__init__" and len(parts) > 1:
        parts = parts[:-1]
    return ".".join(parts)


class _StripDocstrings(ast.NodeTransformer):
    def _strip(self, node):
        self.generic_visit(node)
        body = node.body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            node.body = body[1:] or [ast.Pass()]
        return node

    visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = visit_Module = _strip


def _fingerprint(node: ast.AST) -> str:
    """Structure of the code, ignoring comments, formatting, docstrings, positions."""
    return ast.dump(node, annotate_fields=False, include_attributes=False)


@dataclass(frozen=True)
class Definitions:
    """Fingerprints of everything defined in one file.

    fingerprints keys: 'func', 'Class.method', 'outer.<locals>.inner',
    'Class' (class-level code only) and '<module>' (module-level code only).
    """

    fingerprints: dict[str, str]
    classes: frozenset[str]


def _child_statements(node: ast.stmt) -> list[ast.stmt]:
    """Statements nested in if / for / while / with / try / match blocks."""
    out: list[ast.stmt] = []
    for field in ("body", "orelse", "finalbody"):
        out.extend(s for s in getattr(node, field, None) or [] if isinstance(s, ast.stmt))
    for handler in getattr(node, "handlers", None) or []:
        out.extend(handler.body)
    for case in getattr(node, "cases", None) or []:   # match statement
        out.extend(case.body)
    return out


def _residual(stmts: list[ast.stmt]) -> list[ast.stmt]:
    return [s for s in stmts if not isinstance(s, (*_FUNC_TYPES, ast.ClassDef))]


def extract_definitions(source: bytes) -> Definitions:
    """Parse a file and fingerprint every function / class body / module body.

    Raises SyntaxError / ValueError when the source cannot be parsed.
    """
    tree = _StripDocstrings().visit(ast.parse(source))
    fps: dict[str, str] = {}
    classes: set[str] = set()

    def walk(stmts: list[ast.stmt], prefix: str) -> None:
        for node in stmts:
            if isinstance(node, _FUNC_TYPES):
                name = prefix + node.name
                fps[name] = _fingerprint(node)
                walk(node.body, name + ".<locals>.")
            elif isinstance(node, ast.ClassDef):
                name = prefix + node.name
                classes.add(name)
                shell = copy.copy(node)
                shell.body = _residual(node.body)
                fps[name] = _fingerprint(shell)
                walk(node.body, name + ".")
            else:
                walk(_child_statements(node), prefix)

    walk(tree.body, "")
    fps["<module>"] = _fingerprint(ast.Module(body=_residual(tree.body), type_ignores=[]))
    return Definitions(fps, frozenset(classes))


def diff_definitions(old_source: bytes | None, new_source: bytes | None, module: str) -> FunctionChanges:
    """Compare one file before/after. None = file did not exist on that side.

    Raises SyntaxError / ValueError if either side cannot be parsed.
    """
    empty = Definitions({}, frozenset())
    old = extract_definitions(old_source) if old_source is not None else empty
    new = extract_definitions(new_source) if new_source is not None else empty
    classes = old.classes | new.classes

    def q(key: str) -> str:
        return f"{module}.{key}" if module else key

    changed, added, deleted = [], [], []
    for key in sorted(set(old.fingerprints) | set(new.fingerprints)):
        in_old, in_new = key in old.fingerprints, key in new.fingerprints
        if in_old and in_new:
            if old.fingerprints[key] != new.fingerprints[key]:
                changed.append(q(key))
        elif key in classes or key == "<module>":
            continue  # a new / removed class or file shows up through its functions
        elif in_new:
            added.append(q(key))
        else:
            deleted.append(q(key))
    return FunctionChanges(tuple(changed), tuple(added), tuple(deleted))


# --------------------------------------------------------------------------
# git-backed analyzer
# --------------------------------------------------------------------------

class CodeAnalyzer:
    """Fills changed/added/deleted_functions for CandidateCommits.

    All needed file versions for all commits are fetched with ONE
    `git cat-file --batch` process.
    """

    def __init__(self, repo: str | Path, *, max_file_bytes: int = DEFAULT_MAX_FILE_BYTES) -> None:
        self.repo = Path(repo)
        self.max_file_bytes = max_file_bytes

    @staticmethod
    def _sides(c: CandidateCommit):
        """(old_spec | None, new_spec | None, module, display_path) per Python file."""
        for f in c.files:
            old_path = f.old_path or f.path
            if not (f.path.endswith(".py") or old_path.endswith(".py")) or f.is_binary:
                continue
            old_spec = None if f.status == "A" else f"{c.parent}:{old_path}"
            new_spec = None if f.status == "D" else f"{c.sha}:{f.path}"
            # renamed files are compared under the NEW module name, so a pure
            # rename reports no function changes
            module = path_to_module(f.path if f.path.endswith(".py") else old_path)
            yield old_spec, new_spec, module, f.path

    def analyze(self, commits: Sequence[CandidateCommit]) -> dict[str, FunctionChanges]:
        specs = [s for c in commits for o, n, _, _ in self._sides(c) for s in (o, n) if s]
        blobs = read_blobs(self.repo, specs)

        result: dict[str, FunctionChanges] = {}
        for c in commits:
            changed: set[str] = set()
            added: set[str] = set()
            deleted: set[str] = set()
            skipped: list[str] = []
            for old_spec, new_spec, module, path in self._sides(c):
                old_src = blobs.get(old_spec) if old_spec else None
                new_src = blobs.get(new_spec) if new_spec else None
                if (old_spec and old_src is None) or (new_spec and new_src is None):
                    skipped.append(path)
                    continue
                if any(s is not None and len(s) > self.max_file_bytes for s in (old_src, new_src)):
                    skipped.append(path)
                    continue
                try:
                    fc = diff_definitions(old_src, new_src, module)
                except (SyntaxError, ValueError, RecursionError):
                    skipped.append(path)
                    continue
                changed.update(fc.changed)
                added.update(fc.added)
                deleted.update(fc.deleted)
            result[c.sha] = FunctionChanges(
                tuple(sorted(changed)), tuple(sorted(added)), tuple(sorted(deleted)), tuple(skipped)
            )
        return result

    def enrich(self, commits: Sequence[CandidateCommit]) -> list[CandidateCommit]:
        changes = self.analyze(commits)
        return [
            replace(
                c,
                changed_functions=changes[c.sha].changed,
                added_functions=changes[c.sha].added,
                deleted_functions=changes[c.sha].deleted,
                unanalyzed_files=changes[c.sha].skipped_files,
            )
            for c in commits
        ]
