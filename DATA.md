# Data Guide and Rights Notice

This document describes the data used by World Cup Prediction 2026. It is an operational inventory and attribution guide, not legal advice.

## Code license versus data rights

Project-authored source code is licensed under the Apache License 2.0. That license does **not** grant rights to third-party match data, team data, player data, odds, provider responses, trademarks, photographs, logos, or other third-party material.

Files in `data/`, `outputs/`, and `precomputed/` are not automatically covered by Apache-2.0. Each source remains subject to its own license and terms. Generated predictions and reports are project outputs, but their embedded source fields and factual inputs may remain subject to third-party restrictions.

## Repository data layout

| Path | Purpose | Update pattern |
| --- | --- | --- |
| `data/worldcup.sqlite3` | Canonical local SQLite snapshot used by the dynamic application | Updated by local synchronization, prediction, roster, and result workflows |
| `outputs/` | Prediction exports, evaluation reports, team-strength data, synchronization reports, and model diagnostics | Regenerated after results or model updates |
| `precomputed/api/` | Read-only JSON API snapshots used by hosted/static operation | Exported from the local service and database |
| `dist/` | Reproducible static-site build | Generated locally and intentionally ignored |

The SQLite snapshot includes fixtures, historical results, predictions, team and player records, odds snapshots, model records, provider payloads, and source URLs. It must be audited as a data collection rather than treated as a project-authored binary.

## Data-source register

| Source | Data used | Rights and repository treatment |
| --- | --- | --- |
| [martj42 International Football Results](https://www.kaggle.com/datasets/martj42/international-football-results-from-1872-to-2017) | Historical men's international results used for training and profiles | Dataset page identifies the data as CC0/Public Domain. It may be redistributed, with source acknowledgement retained for provenance. |
| [Wikipedia and Wikimedia projects](https://foundation.wikimedia.org/wiki/Policy:Terms_of_Use/en) | World Cup fixture references and links | Wikipedia text is generally CC BY-SA and requires attribution/share-alike compliance. This project records facts and source links rather than copying article prose; attribution is retained. Media may have separate licenses and is not bundled. |
| [FIFA digital platforms](https://inside.fifa.com/terms-of-service) | Official tournament, fixture, match-centre, roster, and context references | FIFA asserts rights over its platform data and limits reuse. Raw or substantial FIFA-origin content must not be treated as Apache-licensed. Redistribution requires a separate permission analysis. |
| [ESPN](https://disneytermsofuse.com/english/) | Public scoreboard fixtures and completed-match results | Disney/ESPN terms restrict automated extraction and dataset construction. Stored ESPN-derived rows are a redistribution risk and require maintainer action before publication. |
| [worldcup.lyihub.com](https://worldcup.lyihub.com/) | Static match, team, player, lineup, and prediction JSON | No applicable public redistribution license was located during the 2026-07-13 review. Treat redistribution as unapproved until the publisher confirms terms or permission. |
| [Sportmonks](https://www.sportmonks.com/terms-of-service/) | Team, player, lineup, standings, statistics, odds, and provider payloads | Terms allow storage/distribution of supplied data but prohibit resale; logos and profile photos require separate rights. This repository does not sublicense Sportmonks data and does not bundle image binaries. Users must comply with their own plan and domain terms. |
| [API-Sports / API-Football](https://api-sports.io/terms) | Fixtures, teams, lineups, rosters, and related enrichment | Terms prohibit resale without permission but do not provide an open-data license. Treat raw redistribution as restricted or unclear; users obtain their own key. |
| [football-data.org](https://www.football-data.org/about) | World Cup matches and supplemental fixture data | Terms require visible attribution and tie continued use to a subscription. Raw redistribution is not expressly granted. Treat bundled responses as requiring provider confirmation. |
| [The Odds API](https://the-odds-api.com/terms-and-conditions.html) | Market odds and derived market inputs | Terms prohibit redistribution as an API, feed, or downloadable raw-data source. Do not treat embedded raw odds as open data. Users obtain their own key. |
| [Betfair](https://www.betfair.com/en/aboutUs/Terms.and.Conditions/) | Optional exchange odds | General terms limit data to personal, non-commercial use unless separately licensed. No Betfair credential is distributed; users obtain their own application key and session token. |
| [China Sporttery](https://www.sporttery.cn/bzzx/20260410/10053082.html?gid=10) | Current and historical lottery odds snapshots | Published service terms restrict copying, redistribution, and third-party access without written permission. Stored Sporttery snapshots require a maintainer decision before public release. |

Provider names and links are for attribution and provenance. No provider sponsors or endorses this project.

## Included data snapshots

The maintainer intends the repository to include the current SQLite database, public prediction outputs, and precomputed API snapshots so that local cards and the hosted read-only site are populated. Inclusion is subject to the release gate in `docs/audits/data-redistribution-review.md`.

At the current gate, CC0 historical match data and project-authored code/structure are cleared. Several live-provider payload categories are not cleared for open redistribution. They remain present locally while the maintainer chooses a compliant treatment; they must not be inferred to be open-licensed merely because a file is visible in a Git checkout.

## Data users must obtain themselves

The repository never distributes API keys, session tokens, passwords, or account credentials. Copy `.env.example` to `.env` and supply only the providers you are authorized to use:

- Sportmonks API token;
- API-Football/API-Sports key;
- football-data.org key;
- The Odds API key;
- Betfair application key and session token;
- Kaggle credentials if downloading through the Kaggle API.

Do not commit `.env`.

## SQLite snapshot

The default database path is `data/worldcup.sqlite3`. It is required for the full local dynamic experience and supplies historical training data, current match state, predictions, team/player cards, and other locally synchronized content.

Validate it before committing:

```bash
sqlite3 data/worldcup.sqlite3 'PRAGMA integrity_check;'
```

SQLite transient files such as `*.sqlite3-wal`, `*.sqlite3-shm`, and journals are not release artifacts and remain ignored.

## Updating data

Install the project and configure authorized providers first. A full result update uses the existing script; enable only provider flags for which you have credentials and redistribution rights:

```bash
SPORTTERY_ENABLE_LIVE=1 .venv/bin/python scripts/update_after_results.py \
  --fetch-online-results \
  --use-xgboost \
  --recalculate \
  --sync-fifa \
  --sync-fifa-rosters \
  --sync-sportmonks \
  --use-sportmonks \
  --sync-footballdata-io \
  --sync-sporttery \
  --sync-sporttery-history \
  --backfill-historical \
  --train-over25 \
  --date YYYY-MM-DD
```

Review provider terms each time the source or subscription changes. The script updates the local database and output files; it does not grant redistribution rights.

## Reproducing precomputed API data

Export read-only API snapshots from the local database:

```bash
PYTHONPATH=backend .venv/bin/python scripts/export_static_site.py \
  --precomputed \
  --all-dates \
  --simulations 10000
```

The existing static-site export is:

```bash
PYTHONPATH=backend .venv/bin/python scripts/export_static_site.py
```

Generated `dist/` content is ignored because it can be recreated from the versioned application and reviewed data.

## Update frequency and provenance

The repository does not promise a fixed update schedule. Maintainers may update data after match results, provider corrections, roster changes, or model recalibration. Each data commit should state:

- the effective match date;
- providers contacted;
- commands used;
- affected data roots;
- validation results;
- any provider-term or attribution change.

Use `scripts/review_daily_update.sh` before staging an update. Stage reviewed paths explicitly; never use an unattended commit-and-push workflow.

## Reporting data-rights concerns

Open an issue that identifies the affected path and source without reproducing disputed content. For a confidential concern, follow `SECURITY.md` once it is present. Maintainers will preserve evidence, pause redistribution when appropriate, and avoid rewriting history without an explicit remediation decision.
