# Perfhound

Finds the commit that made your code slow. Local-first CLI + VS Code extension.

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
| 1 | Data model (`CandidateCommit`) | - |
| 2 | Test fixture repo | - |
| 3 | Range Resolver | - |
| 4 | Local Git Provider | - |
| 5 | Code Analyzer | - |
| 6 | Worktree Manager | - |
| 7 | Cache (SQLite) | - |
| 8 | Snapshot Store | - |
| 9 | GitHub Provider | - |
| 10 | Validation on a real repo | - |
