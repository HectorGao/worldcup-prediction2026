# Contributing

Thank you for helping improve World Cup Prediction 2026. Contributions to models, tests, documentation, data provenance, accessibility, and reproducible maintenance are welcome.

## Before opening a change

1. Search existing issues and pull requests.
2. For a substantial algorithm, schema, provider, or user-facing change, open an issue first and describe the intended behavior and data rights.
3. Never attach credentials, private provider payloads, personal information, or data you are not allowed to redistribute.

## Development setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
cp .env.example .env
.venv/bin/pytest -q
```

Start the service with:

```bash
.venv/bin/uvicorn worldcup_predictor.api:app --app-dir backend --host 127.0.0.1 --port 8000
```

## Contribution requirements

- Preserve existing runtime behavior unless the change explicitly proposes and tests a behavior change.
- Add or update focused tests for code changes.
- Keep provider requests out of deterministic unit tests; mock network boundaries.
- Document model assumptions and avoid presenting predictions as guaranteed results or betting advice.
- Follow [DATA.md](DATA.md) for every data change. Include source, rights, effective date, update command, and validation evidence.
- Store restricted source material only in ignored `.private_data/`; publish only the sanitizer output.
- Keep `.env` local and use safe placeholders in `.env.example`.

## Required checks

```bash
scripts/check_repository_safety.sh
.venv/bin/pytest -q
sqlite3 data/worldcup.sqlite3 'PRAGMA integrity_check;'
PYTHONPATH=backend .venv/bin/python scripts/export_static_site.py
```

For routine model or data updates, run `scripts/review_daily_update.sh`. It performs review checks without changing Git state.

## Commits and pull requests

Keep commits focused. Separate community documentation, safety configuration, data snapshots, and CI/maintenance tooling when practical. In the pull request:

- explain the problem and solution;
- identify runtime and model behavior changes;
- list data paths and source-rights decisions;
- include tests and manual verification;
- call out deployment or compatibility effects.

By contributing project-authored code, you agree that it may be distributed under Apache License 2.0. Do not contribute third-party data under that license unless you have authority to do so.
