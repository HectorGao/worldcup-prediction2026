# Data Guide and Rights Notice

This document describes the data used by World Cup Prediction 2026. It is an operational inventory and attribution guide, not legal advice.

## Code license versus data rights

Project-authored source code is licensed under the Apache License 2.0. That license does **not** grant rights to third-party match data, team data, player data, odds, provider responses, trademarks, photographs, logos, or other third-party material.

Files in `data/`, `outputs/`, and `precomputed/` are not automatically covered by Apache-2.0. Each source remains subject to its own license and terms. Generated predictions and reports are project outputs, but their embedded source fields and factual inputs may remain subject to third-party restrictions.

## Repository data layout

| Path | Purpose | Update pattern |
| --- | --- | --- |
| `data/worldcup.sqlite3` | Public SQLite snapshot with raw provenance plus the active 104-match canonical-result build | Rebuilt after reviewed result, prediction, and snapshot updates |
| `outputs/` | Prediction exports, evaluation reports, team-strength data, synchronization reports, and model diagnostics | Regenerated after results or model updates |
| `precomputed/api/` | Read-only JSON API snapshots used by hosted/static operation | Exported from the local service and database |
| `dist/` | Reproducible static-site build | Generated locally and intentionally ignored |

The local private source database may include fixtures, historical results, predictions, team and player records, odds snapshots, model records, provider payloads, and source URLs. The committed public SQLite snapshot is a sanitized derivative with restricted raw payload, odds, roster, player, image, and provider-source fields removed. Both must be audited as data collections rather than treated as project-authored binaries.

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
| [China Sporttery](https://www.sporttery.cn/bzzx/20260410/10053082.html?gid=10) | Reviewed historical odds snapshots used as project inputs | The repository releases only a maintainer-reviewed, field-whitelisted snapshot: match number/date/teams, observed timestamp, H/D/A, handicap and totals values. It does not transfer any Sporttery intellectual-property rights, publish credentials, or authorize reuse beyond the applicable source terms. |

Provider names and links are for attribution and provenance. No provider sponsors or endorses this project.

## Included data snapshots

The repository includes a compliant SQLite database, public prediction outputs, and precomputed API snapshots so that local cards and the hosted read-only site remain populated. Restricted source snapshots are retained locally under ignored `.private_data/` and are not committed.

The public snapshot retains CC0 historical match data, necessary factual match fields, the active 104-match canonical result table, project-authored prediction/model outputs, and the reviewed Sporttery historical snapshot described above. It removes raw provider payload rows, unlicensed roster/player tables, image fields, provider URLs, credentials, and restricted embedded market/source fields. Provider integration names may remain in capability metadata and project-authored model feature names; they do not include provider payloads or credentials.

## Canonical 104-match result set

`finished_match_results` is retained as raw-source provenance. The application and report generator use `canonical_match_results`, whose active build is exactly the 104 unique tournament matches: 72 group matches, 16 round-of-32 matches, 8 round-of-16 matches, 4 quarter-finals, 2 semi-finals, a third-place match, and a final.

Each canonical record stores these separate fields:

- `home_score_90`, `away_score_90`
- `home_score_extra_time`, `away_score_extra_time`
- `home_score_penalties`, `away_score_penalties`
- `result_display`

The primary result and all hit metrics use the 90-minute score. Extra time and penalties are supplementary display and advancement information. Source records that do not explicitly distinguish an extension period from a shootout are retained with their source provenance rather than guessed.

The canonical build uses final tournament records as its coverage baseline and joins compatible raw-source IDs by normalized team aliases (for example, `Czechia`/`Czech Republic`, `Curaçao`/`Curacao`, and `Congo DR`/`DR Congo`). Rebuilding does not delete raw source rows.

Create the private source tree once, before sanitizing a new local collection:

```bash
mkdir -p .private_data/data .private_data/outputs .private_data/precomputed/api
cp data/worldcup.sqlite3 .private_data/data/worldcup.sqlite3
cp -R outputs/. .private_data/outputs/
cp -R precomputed/api/. .private_data/precomputed/api/
```

Then build the public snapshot:

```bash
.venv/bin/python scripts/build_public_data_snapshot.py
```

The builder does not delete private inputs. It writes the canonical public database, outputs, precomputed API files, and `data/public_snapshot_manifest.json`.

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

The default database path is `data/worldcup.sqlite3`. It supplies CC0 historical training data, current factual match state, the canonical 104-match result set, sanitized predictions, reviewed historical Sporttery snapshots, team profiles, and other cleared project outputs. Restricted live-provider roster and raw-payload details are intentionally unavailable in a clean public clone until a user configures and runs an authorized local provider sync.

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
  --all-finished-results \
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

## Preserving regression runs

The top-level files in `outputs/` represent the latest reviewed run and may be
replaced by a later synchronization or retraining operation. Before publishing
an update, preserve each significant regression under:

```text
outputs/regressions/YYYY-MM-DD-short-label/
```

Copy the reviewed synchronization log, retraining report, aggregate regression
evaluation, specialized error analyses, weighting comparison, updated
predictions, and bracket output into that directory. Add a short `README.md`
that records the effective match dates, important aggregate metrics, snapshot
command, and data-rights boundary. Run the public snapshot builder before
creating the archive so restricted provider fields are not copied into Git.

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

After updating private local data, rerun `scripts/build_public_data_snapshot.py`, then use `scripts/review_daily_update.sh` before staging an update. Stage reviewed paths explicitly; never use an unattended commit-and-push workflow.

Regenerate the public final report and README assets from the reviewed database:

```bash
PYTHONPATH=backend .venv/bin/python tools/generate_report_assets.py \
  --database .private_data/reviewed-worldcup.sqlite3 \
  --output docs/assets
```

The generator writes aggregate metrics and SVGs only. It evaluates saved prediction records against canonical 90-minute results and labels records created after the fixture date as reconstructed audits, not valid pre-match evaluation.

## Reporting data-rights concerns

Open an issue that identifies the affected path and source without reproducing disputed content. For a confidential concern, follow `SECURITY.md` once it is present. Maintainers will preserve evidence, pause redistribution when appropriate, and avoid rewriting history without an explicit remediation decision.
