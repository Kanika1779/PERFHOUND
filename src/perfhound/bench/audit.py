"""Static audit of SWE-fficiency-style workloads, BEFORE spending benchmark time.

Found 2026-10-05 (sympy-26710 measured ratio 1.02 vs expected 0.47): many synthetic
workloads paste a copy of the function under test ("copied directly from the post-edit
source file") into the script. The timed code is then the SAME at every commit - the
change cannot be measured, and the copy also hands retrieval/LLM the culprit's code text.

    clean            nothing suspicious
    inlines_code     says it copies/replicates repo code AND defines a sizable function/class
    mentions_copy    says so, but defines nothing big (may still exercise the repo - measure it)
    syntax_error     not valid Python
    no_workload      nothing to run
"""

from __future__ import annotations

import ast
import re

_COPY_WORDS = re.compile(r"copied|post-edit|pre-edit|verbatim|replicat\w*|reimplement\w*|simplified version|copy of", re.I)
BIG_DEF_LINES = 12


def audit_workload(source: str | None) -> tuple[str, list[str]]:
    if not source:
        return "no_workload", []
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return "syntax_error", [f"line {e.lineno}: {e.msg}"]
    words = sorted({w.lower() for w in _COPY_WORDS.findall(source)})
    big = [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
           and n.name not in ("setup", "workload") and (n.end_lineno - n.lineno) >= BIG_DEF_LINES]
    if words and big:
        return "inlines_code", words + [f"defines {', '.join(big[:4])}"]
    if words:
        return "mentions_copy", words
    return "clean", []
