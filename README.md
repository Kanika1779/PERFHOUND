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
| 7 | Cache (SQLite) | - |
| 8 | Snapshot Store | - |
| 9 | GitHub Provider | - |
| 10 | Validation on a real repo | - |
