# Perfhound

Finds the commit that made your code slow. Local-first CLI + VS Code extension.

## Requirements

- Python >= 3.10
- git >= 2.31 (uses `--diff-merges=first-parent`)

## Usage (so far)

```python
from perfhound.gateway import Gateway

gw = Gateway("path/to/repo")
candidates = gw.get_candidates("v1.2", "HEAD")   # offline, incl. changed functions

with gw.worktree() as wt:                         # private checkout in the temp dir
    folder = wt.checkout(candidates[3].sha)       # your folder is never touched
```

Fetch any GitHub range from the terminal (also published as a GitHub Action:
[Kanika1779/PERFHOUND_gateway](https://github.com/Kanika1779/PERFHOUND_gateway)):

```
python -m perfhound.gateway login                      # GitHub token, once
python -m perfhound.gateway range https://github.com/psf/requests --good v2.32.3 --bad v2.32.4 --summary report.md
```

## Dev setup

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  (Linux/macOS: source .venv/bin/activate)
pip install -e ".[dev]"
pytest
```

## Build status (Gateway module)

| Step | What | Status |
|---|---|---|
| 0 | Project setup | done |
| 1 | Data model (`CandidateCommit`) | done |
| 2 | Test fixture repo | done |
| 3 | Range Resolver | done |
| 4 | Local Git Provider | done |
| 5 | Code Analyzer | done |
| 6 | Worktree Manager | done |
| 7 | Cache (SQLite) | done |
| 8 | Snapshot Store | done |
| 9 | GitHub Provider (REST + GraphQL, linked issues, time rule) | done |
| 10 | Validation on a real repo (psf/requests v2.32.3..v2.32.4: 29/29 PRs) | done |

## Phase 2: any source, Python + Java

| Step | What | Status |
|---|---|---|
| A | Analyzer plug-ins + Java (tree-sitter) | done |
| 0 | perfhound.yaml + CLI + SWE-fficiency real-change cases | done |
| 1 | RAG v1: commit documents + BM25 + recall@k | done (results/retrieval_v1) |
| 2a | RAG v2: dense embeddings (fastembed) + BM25/dense Reciprocal Rank Fusion | code done, real-model eval pending (run on a machine with Hugging Face access) |
| B | RegressionCase + Source Adapters + Repo Fetcher | done |
| C | Adapters: SWE-fficiency (done), pandas asv-runner, ICPE JMH, Zenodo | in progress |
| D | Document builder for RAG | - |
