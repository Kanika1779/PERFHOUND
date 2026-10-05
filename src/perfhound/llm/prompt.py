"""Prompt for the prioritizer.

Design choices (each one is an experiment knob, not a guess we hide):
- Candidates are shown in CHRONOLOGICAL order with neutral ids C1..Ck - never in
  retrieval order, so the LLM cannot simply copy the retriever's ranking and
  position bias is not aligned with the retriever.
- Only what a real user would have: the benchmark (workload / command / metric /
  direction) and the commits. Never case ids, dataset ids or SHAs.
- redact(): PR / issue numbers removed from commit text by default. They carry no
  information about code, but let a model that memorized the project's history
  "recognize" a famous optimization PR. --no-redact is the ablation.
"""

from __future__ import annotations

import re
from typing import Sequence

from ..gateway.cases import RegressionCase
from ..rag.documents import CommitDocument

MAX_WORKLOAD = 4000
MAX_MESSAGE = 700
MAX_FUNCTIONS = 40
MAX_PATHS = 20
MAX_DIFF = 1500

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "candidates": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "id": {"type": "STRING"},
                    "score": {"type": "NUMBER"},
                    "reason": {"type": "STRING"},
                },
                "required": ["id", "score", "reason"],
            },
        },
    },
    "required": ["candidates"],
}

_PR_REFS = [
    (re.compile(r"Merge pull request #\d+ from \S+"), "Merge pull request"),
    (re.compile(r"\(\s*(?:gh-|#)\d+\s*\)"), ""),
    (re.compile(r"(?<![\w/])(?:gh-|GH-|#)\d+\b"), "#N"),
    (re.compile(r"https?://github\.com/\S+/(?:pull|issues)/\d+\S*"), "<link>"),
]
_SHA = re.compile(r"\b[0-9a-f]{12,40}\b")


def redact(text: str) -> str:
    for pat, rep in _PR_REFS:
        text = pat.sub(rep, text)
    return _SHA.sub("<sha>", text)


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + "\n[... truncated]"


def describe_symptom(case: RegressionCase) -> str:
    lines = []
    b = case.benchmark
    change = "got SLOWER (a performance regression)" if case.direction == "slower" else "got FASTER (a speed-up)"
    if b is not None:
        metric = b.metric.get("type", "wall_time")
        better = "higher is better" if b.higher_is_better else "lower is better"
        lines.append(f"Benchmark metric: {metric} in {b.unit} ({better}). Between the good and the bad commit it {change}.")
        if b.command:
            lines.append(f"Benchmark command: {b.command}")
        if b.workload:
            lines.append("Benchmark workload (the code that is timed):\n```python\n" + _clip(b.workload, MAX_WORKLOAD) + "\n```")
    else:
        lines.append(f"The program {change}.")
    symptom = case.metadata.get("symptom")
    if symptom:
        lines.append(f"Reported symptom: {symptom}")
    return "\n".join(lines)


def describe_commit(alias: str, doc: CommitDocument, *, do_redact: bool) -> str:
    f = doc.fields
    msg = _clip(f.get("message", ""), MAX_MESSAGE)
    paths = f.get("paths", "").split()
    funcs = f.get("functions", "").split()
    diff = _clip(f.get("diff", ""), MAX_DIFF)
    if do_redact:
        msg, diff = redact(msg), redact(diff)
    out = [f"### {alias}", f"Message: {msg or '(empty)'}"]
    out.append("Files: " + (", ".join(paths[:MAX_PATHS]) + (f" (+{len(paths) - MAX_PATHS} more)" if len(paths) > MAX_PATHS else "")
                             if paths else "(none)"))
    out.append("Changed functions: " + (", ".join(funcs[:MAX_FUNCTIONS]) + (" ..." if len(funcs) > MAX_FUNCTIONS else "")
                                         if funcs else "(none detected)"))
    if diff:
        out.append("Code lines added/removed (excerpt):\n```\n" + diff + "\n```")
    return "\n".join(out)


def build_prompt(case: RegressionCase, docs: Sequence[CommitDocument], *, do_redact: bool = True) -> tuple[str, dict[str, str]]:
    """Return (prompt, alias -> sha). `case` must be case.for_localizer()."""
    ordered = sorted(docs, key=lambda d: d.position)          # chronological, never retrieval order
    aliases = {f"C{i}": d.sha for i, d in enumerate(ordered, start=1)}
    project = case.repo.rstrip("/").split("/")[-1].removesuffix(".git")
    commits = "\n\n".join(describe_commit(a, d, do_redact=do_redact) for a, d in zip(aliases, ordered))
    prompt = f"""You are a performance engineer localizing which commit changed the performance of a {case.language} project ({project}).

{describe_symptom(case)}

Below are {len(ordered)} candidate commits from the range between the good and the bad commit, oldest first.
Exactly one commit in the full range caused the change, and it may not be among these candidates.
For EACH candidate give a score from 0 to 1: how likely it is that this commit caused the change,
judged by whether it modifies code that the benchmark actually executes, in a way that plausibly
changes its cost (algorithm, data structure, caching, vectorization, extra work, I/O, allocation).
Commits that only touch docs, tests, CI, typing, formatting or unrelated modules should get low scores.
Do not give high scores to several commits unless the evidence really is ambiguous.

{commits}

Return JSON: {{"candidates": [{{"id": "C1", "score": 0.0-1.0, "reason": "one short sentence"}}, ...]}} with one entry per candidate id."""
    return prompt, aliases
