"""Turn gateway output into searchable documents, and a case into a query.

A commit document has named fields so experiments can compare what helps:
    message    - commit message (+ PR title/body when the GitHub provider adds it)
    paths      - changed file paths
    functions  - changed / added / deleted function names (from the Code Analyzer)
    diff       - added and removed code lines (not context lines, not headers)

Leak guard: only commits dated at or before `bad` may enter the knowledge base
(`date_limit`). Candidates always satisfy this; history documents added later
must too - a later "fix the slowdown from #123" commit would hand over the answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Sequence

from ..gateway.cases import RegressionCase
from ..gateway.models import CandidateCommit
from .tokenize import tokenize

FIELDS = ("message", "paths", "functions", "diff")


@dataclass(frozen=True)
class CommitDocument:
    sha: str
    position: int
    timestamp: datetime
    fields: dict[str, str]

    def text(self, fields: Iterable[str] = FIELDS) -> str:
        return "\n".join(self.fields[f] for f in fields if self.fields.get(f))

    def tokens(self, fields: Iterable[str] = FIELDS) -> list[str]:
        return tokenize(self.text(fields))


def _diff_code_lines(diff: str) -> str:
    keep = []
    for line in diff.split("\n"):
        if line.startswith(("+++", "---")):
            continue
        if line.startswith(("+", "-")):
            keep.append(line[1:])
    return "\n".join(keep)


def commit_document(c: CandidateCommit) -> CommitDocument:
    message = c.message
    if c.pr is not None:
        message += "\n" + c.pr.title + "\n" + c.pr.body
    functions = " ".join([*c.changed_functions, *c.added_functions, *c.deleted_functions])
    paths = " ".join(f.path + (" " + f.old_path if f.old_path else "") for f in c.files)
    return CommitDocument(
        sha=c.sha, position=c.position, timestamp=c.timestamp,
        fields={"message": message, "paths": paths, "functions": functions, "diff": _diff_code_lines(c.diff)},
    )


def build_commit_documents(candidates: Sequence[CandidateCommit], *, date_limit: datetime | None = None) -> list[CommitDocument]:
    docs = [commit_document(c) for c in candidates]
    if date_limit is not None:
        docs = [d for d in docs if d.timestamp <= date_limit]
    return docs


_PY_FILE = re.compile(r"[\w./\\-]+\.(?:py|java)\b")


def build_query(case: RegressionCase, *, read_file=None) -> str:
    """Text describing the symptom.

    Situation 1 (CI): the benchmark - its inline workload, or its command plus the
    content of a script the command names (read_file(path) -> str | None, read at `bad`).
    Situation 2 (complaint): metadata["symptom"], free text.
    """
    parts: list[str] = []
    b = case.benchmark
    if b is not None:
        parts.append(b.name)
        if b.workload:
            parts.append(b.workload)
        if b.command:
            parts.append(b.command)
            if read_file is not None:
                for path in _PY_FILE.findall(b.command):
                    content = read_file(path.replace("\\", "/"))
                    if content:
                        parts.append(content)
    symptom = case.metadata.get("symptom")
    if symptom:
        parts.append(str(symptom))
    if not parts:
        raise ValueError(f"case {case.case_id} has nothing to search with: add a benchmark or metadata.symptom")
    return "\n".join(parts)
