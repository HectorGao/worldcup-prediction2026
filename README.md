# World Cup Prediction 2026

An open-source, local-first dashboard for exploring 2026 World Cup fixtures, team strength, match probabilities, knockout paths, and daily prediction reports.

The project combines Dixon-Coles and Poisson score models, Elo ratings, Monte Carlo simulation, calibrated ensembles, and optional XGBoost signals behind a FastAPI service and a dependency-free browser interface. A sanitized SQLite snapshot and precomputed API responses are included so a clean clone starts with populated match cards.

> Predictions are experimental statistical estimates. They are not betting, financial, or professional advice and do not guarantee outcomes.

## What is included

- Complete Python prediction and data-processing source code
- FastAPI API and the existing HTML/CSS/JavaScript dashboard
- Sanitized `data/worldcup.sqlite3` with CC0 history, necessary match facts, and project predictions
- Sanitized prediction exports in `outputs/`
- Sanitized read-only API snapshots in `precomputed/api/`
- Tests, static export, Render configuration, and maintenance tools

Restricted raw provider payloads, credentials, licensed odds feeds, and unlicensed player/roster datasets are not published. See [DATA.md](DATA.md) for the exact boundary and update workflow.

## Quick start

Requirements: Python 3.10 or newer and Git. SQLite CLI is useful for integrity checks but is not required by the application.

```bash
git clone https://github.com/HectorGao/worldcup-prediction2026.git
cd worldcup-prediction2026
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
cp .env.example .env
uvicorn worldcup_predictor.api:app --app-dir backend --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>. The checked-in public snapshot works without API keys. On macOS, `start_local_server.command` provides the same local startup flow.

## Configuration

All optional configuration is listed in `.env.example`. Keep real values only in the ignored `.env` file. Provider keys are needed only when you intentionally refresh data from a provider under your own account and terms.

Important runtime modes:

| Variable | Purpose | Default |
| --- | --- | --- |
| `WORLDCUP_DB_PATH` | SQLite database used by the service | `data/worldcup.sqlite3` |
| `WORLDCUP_USE_PRECOMPUTED` | Serve checked-in API snapshots where supported | `0` |
| `WORLDCUP_READ_ONLY` | Disable write-oriented operation for hosting | `0` |
| `WORLDCUP_PRECOMPUTED_DIR` | Precomputed snapshot root | `precomputed` |
| `SPORTTERY_ENABLE_LIVE` | Enable the optional live Sporttery path | `0` |

Never commit `.env`, tokens, passwords, private database exports, or raw provider responses.

## Test and verify

```bash
.venv/bin/pytest -q
scripts/check_repository_safety.sh
sqlite3 data/worldcup.sqlite3 'PRAGMA integrity_check;'
PYTHONPATH=backend .venv/bin/python scripts/export_static_site.py
```

The static export is written to ignored `dist/`. Repository CI runs the safety check, test suite, database integrity check, and static export on supported Python versions.

## Data refresh workflow

Raw authorized provider data belongs in ignored `.private_data/`. Generate a publishable snapshot with:

```bash
.venv/bin/python scripts/build_public_data_snapshot.py
scripts/review_daily_update.sh
```

The review script is read-only: it displays code and data changes, checks for sensitive content and large files, validates the database, runs tests, and builds the static site. It never stages, commits, or pushes changes. Review and stage individual paths yourself.

Detailed sources, data rights, private-input setup, snapshot contents, update frequency, and reproduction commands are documented in [DATA.md](DATA.md).

## Deployment

- Dynamic read-only service: use `render.yaml`; the start command is `uvicorn worldcup_predictor.api:app --app-dir backend --host 0.0.0.0 --port $PORT`.
- Static site: run `scripts/export_static_site.py` and deploy `dist/`; see [docs/static-deployment.md](docs/static-deployment.md).
- GitHub Actions: `.github/workflows/deploy-static-site.yml` can build a static artifact and deploy when the required Cloudflare secrets are configured.

No production deployment requires publishing provider credentials.

## Project layout

```text
backend/worldcup_predictor/   API, database, providers, and prediction models
src/                          Browser application JavaScript and styles
data/                         Sanitized public SQLite snapshot and manifest
outputs/                      Sanitized project prediction and evaluation outputs
precomputed/api/              Sanitized read-only API snapshots
scripts/                      Export, update, safety, and maintenance tools
tests/                        Unit, API, data, frontend, and maintenance tests
```

## Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md) before proposing changes. Please report vulnerabilities through [SECURITY.md](SECURITY.md), not a public issue. Community participation is governed by [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## Licenses

**Code license:** Project-authored source code is licensed under the [Apache License 2.0](LICENSE).

**Data license:** The Apache License does not relicense third-party data, match facts, provider responses, trademarks, images, or other third-party material. Data in this repository has source-specific rights and a deliberately sanitized release boundary described in [DATA.md](DATA.md). CC0 historical data retains its CC0 status; project-authored predictions remain separate outputs; restricted raw provider payloads are local-only.

Provider and tournament names are used for identification and attribution. No provider or tournament organizer sponsors or endorses this project.
