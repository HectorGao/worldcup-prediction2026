# Open Source Readiness Baseline

Audit date: 2026-07-13 (Asia/Shanghai)

## Branch and commit baseline

- Working branch: `codex/open-source-readiness`
- Original local project commit: `851b7e240f2a9c9a7f6bd71d490fa78886c76ea0`
- Approved design commit: `5853ae8`
- Implementation plan commit: `9e04c4c`
- Remote base at inspection: `origin/main` at `c2e7ff3`

Commit `851b7e2` is a normal application change titled `fix: auto-enable precomputed data on render`. It changes `backend/worldcup_predictor/api.py` and `tests/test_service_api.py` to enable and test precomputed data automatically in the Render environment. It remains part of the branch history.

## Application paths protected from maintenance edits

The open-source readiness work must not modify existing files under these paths except for explicitly approved test isolation changes:

- `backend/`
- `src/`
- `index.html`
- existing files under `scripts/`
- `pyproject.toml`
- `package.json`
- `Procfile`
- `render.yaml`
- `runtime.txt`
- `start_local_server.command`

The baseline test run completed with 116 passing tests and three failures caused by live ESPN and Lyihub fallback requests escaping test isolation. The maintainer approved test-only changes in `tests/test_providers.py`, `tests/test_public_data_sources.py`, and `tests/test_result_sync.py`. The three focused tests pass after the isolation changes. No runtime file was changed for this correction.

## Data intended for publication

The maintainer selected a full-data Git repository. The current publication set is:

| Path | Approximate size | Files/status | Role |
| --- | ---: | --- | --- |
| `data/worldcup.sqlite3` | 41 MB | one currently ignored database | Local dynamic application data, including fixtures, historical matches, predictions, team/player records, odds snapshots, and model records |
| `outputs/` | 7.4 MB | 16 files; 14 modified at baseline | Current predictions, model evaluation, update reports, strength data, and reproducibility outputs |
| `precomputed/api/` | 14 MB | 198 files; 141 modified and four untracked files at baseline | Read-only API snapshots used by hosted/static operation |

Across `data/`, `outputs/`, and `precomputed/`, 209 files were already tracked, 155 tracked files were modified, and four files in two new percent-encoded team directories were untracked. The untracked files are two `squad.json` files and two `world-cup-detail.json` files.

The SQLite database passed `PRAGMA integrity_check` at baseline. Its principal content includes 49,476 historical matches, 866 fixtures, 106 predictions, 1,248 Lyihub player rows, 347 Sporttery odds snapshots, and related model, team, and provider records.

Publication remains subject to the secret, personal-information, and third-party redistribution audits. A risky finding will pause staging or pushing; it will not trigger automatic deletion.

## Files intended to remain ignored

- `.env` and local environment variants, except `.env.example`: secrets and machine-specific configuration.
- `.venv/`: locally installed dependencies.
- `.pytest_cache/`, `__pycache__/`, and compiled Python files: reproducible caches.
- `.DS_Store` and editor state: operating-system or editor metadata.
- `dist/`: reproducible static export output.
- SQLite `-wal`, `-shm`, and journal sidecars: transient database state.
- Python packaging metadata: reproducible build/install output.

The canonical `data/worldcup.sqlite3` snapshot will be explicitly versioned rather than covered by a broad `*.sqlite3` ignore rule.

## Large-file assessment

No intended repository file exceeded 50 MiB at baseline. The 41 MB SQLite database is below GitHub's 100 MB per-file hard limit. Git LFS is not installed locally and is not required for the initial publication. The maintenance workflow will warn at 50 MiB and fail before 95 MiB so migration can be considered before GitHub rejects a file.

Normal Git storage for a frequently updated binary database can increase repository history faster than its working-tree size. This is an accepted initial trade-off for a self-contained repository and must be reviewed as the database grows.

## Local-only commit 851b7e2

`851b7e2` contains 85 insertions and two deletions across the FastAPI entry point and an API test. It recognizes the Render environment, selects precomputed data automatically when appropriate, and verifies the behavior. The change is a normal runtime/deployment fix, contains no data file, and remains ahead of the original remote base.

## Open audit questions

- Whether every raw or derived payload in SQLite, `outputs/`, and `precomputed/api/` may be redistributed under the applicable third-party terms.
- Whether reachable Git history contains a real credential even though `.env` is currently untracked.
- Whether raw provider payloads contain personal information or credential-bearing request metadata.
- Whether any debug-named output is necessary for reproducibility or is a disposable intermediate artifact. Under the approved full-data policy, it remains in scope unless the maintainer decides otherwise after audit.
- Whether the repository's current SSH authentication can push the branch and create a pull request.
