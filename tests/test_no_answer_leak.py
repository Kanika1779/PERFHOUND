"""The localizer must not be able to read the answer from the case it is given.

Found 2026-10-05: the SWE-fficiency benchmark name was the instance id
("dask__dask-10356") = the culprit PR's number, which the culprit's merge /
squash message also contains ("#10356"). BM25 drops digits, but dense models
and the LLM see raw text.
"""

import json
import re
from pathlib import Path

import pytest

from perfhound.gateway import RegressionCase
from perfhound.rag.documents import build_query

CASES = Path(__file__).resolve().parents[1] / "data" / "cases_real_n20_seed0.jsonl"


def _cases():
    if not CASES.exists():
        pytest.skip("cases file not present")
    return [RegressionCase.from_json(l) for l in CASES.read_text(encoding="utf-8").splitlines() if l.strip()]


def test_hidden_case_reveals_no_answer():
    for case in _cases():
        hidden = case.for_localizer()
        # `bad` is always given and may itself be the culprit (last commit of the window)
        text = hidden.to_json().replace(hidden.bad, "<bad>").replace(hidden.good, "<good>")
        task = case.metadata["truth_task"]
        pr = str(case.metadata["truth_pr_number"])
        assert case.culprit not in text and case.culprit[:10] not in text
        assert task not in text, case.case_id
        assert "truth_" not in text
        assert not re.search(rf"(?<![0-9]){pr}(?![0-9])", json.dumps(hidden.to_dict()["metadata"]) + hidden.benchmark.name)
        assert hidden.case_id.startswith("case-")


def test_query_has_no_dataset_id():
    for case in _cases():
        q = build_query(case.for_localizer())
        assert case.metadata["truth_task"] not in q
