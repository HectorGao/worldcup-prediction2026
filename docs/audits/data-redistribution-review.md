# Third-Party Data Redistribution Review

Review date: 2026-07-13 (Asia/Shanghai)

This review records publication risk; it is not legal advice. It follows the maintainer's instruction not to delete questionable data without presenting a list and recommendation first.

## Method

The review compared provider names found in source code, SQLite tables, output metadata, and precomputed JSON with the providers' current official license or terms pages. Searches were limited to official provider pages where available. No provider credential was used or displayed.

## Summary decision table

| Source/category | Status | Evidence and effect | Affected repository content | Recommendation |
| --- | --- | --- | --- | --- |
| martj42 historical results | Cleared | Kaggle marks the dataset CC0/Public Domain | Most of the 49,476 `historical_matches` rows in `data/worldcup.sqlite3` | Include with provenance acknowledgement. |
| Wikipedia-derived fixture references | Cleared with conditions | Wikimedia text is generally CC BY-SA; attribution and share-alike obligations apply to copied expression | 41 `web_fixtures` rows identify `wikipedia_fifa_links`; source URLs also appear in snapshots | Include factual fields and links with attribution; do not bundle separately licensed media. |
| FIFA-origin platform content | Restricted | FIFA terms assert rights over platform information/data and generally limit content reuse to private non-commercial use or separately permitted editorial use | FIFA source links and labels appear in approximately 51 JSON files; future raw FIFA tables may populate the database | Do not publish raw FIFA responses without permission. Distinguish bare facts/source links from copied payloads and request permission or replace restricted payloads. |
| ESPN scoreboard data | Restricted | Disney terms prohibit unauthorized automated extraction for datasets/databases | 83 finished-result rows, 46 web-fixture rows, and approximately 68 JSON files contain ESPN attribution or derived fields | Do not publish raw ESPN-derived snapshots without permission. Replace with a permissive source or obtain written authorization. |
| Lyihub static JSON | Unclear | No public redistribution license or applicable terms were located | 49 raw-provider payloads, 49 finished results, 104 web fixtures, extensive player/team rows, and approximately 123 JSON files | Ask the publisher for permission/license. Until confirmed, do not push the raw or normalized snapshots. |
| Sportmonks API data | Conditional | Official terms allow distribution/storage of supplied data but prohibit resale; logo/photo rights remain separate | 100 raw provider payloads plus team/player/stat fields in approximately 67 JSON files | Include only under a clear data notice, without sublicensing or image binaries, and confirm the account/plan permits the repository's public domain use. |
| API-Football/API-Sports data | Restricted or unclear | Terms prohibit data resale but do not grant an open redistribution license | Team, lineup, roster, and provider-status fields in approximately 46 JSON files and derived database rows | Obtain written permission or regenerate without redistributing provider payloads. |
| football-data.org data | Restricted or unclear | Terms require attribution, single-application credentials, and continued subscription for referencing supplied data; raw redistribution is not expressly allowed | Seven raw-provider payloads and approximately 40–46 JSON files with football-data attribution or derived fields | Add required attribution and obtain confirmation before distributing raw responses. |
| The Odds API data | Restricted | Terms prohibit repackaging or redistribution through feeds or downloadable files | Provider/market structures appear in approximately 120 JSON files; explicit provider labels appear in a subset | Do not publish raw odds as downloadable data. Retain project-authored aggregates only after confirming they cannot reconstruct restricted raw data. |
| Betfair data | Restricted | Betfair terms limit use to personal/non-commercial purposes absent separate licensing | Betfair provider/status structures appear in approximately 39 JSON files; no fixture row currently identifies Betfair as its market source | Keep integration code and empty status metadata; do not distribute Betfair odds unless separately licensed. |
| China Sporttery odds | Restricted | Official service agreement restricts copying, redistribution, derivative access, and third-party tools without written permission | 347 SQLite odds snapshots and approximately 120 JSON files contain Sporttery source or market data | Obtain written permission or exclude/replace the odds data before public push. Do not delete local records without maintainer approval. |
| Project-generated predictions and reports | Conditional | Project-authored computations are distinct from inputs, but many serialized records embed provider fields, source URLs, odds, or raw-derived details | `outputs/`, `precomputed/api/predictions/`, match analysis files, team detail files, and the SQLite `predictions` table | Publish only after restricted embedded fields are removed through an approved data-export process or the underlying providers authorize redistribution. |

## Current local data evidence

At review time the SQLite database contained:

- 83 ESPN and 49 Lyihub finished-match rows;
- 46 ESPN, 104 Lyihub, and 41 Wikipedia/FIFA-link web-fixture rows;
- seven FootballData.io, 50 Sportmonks player, and 50 Sportmonks team raw-provider payloads;
- 347 China Sporttery odds snapshots;
- 49,476 historical match rows, primarily sourced from the CC0 martj42 dataset.

Text searches found provider labels across the precomputed snapshots and outputs. Counts are discovery aids, not proof that every matching file contains a complete raw response.

## Blocking release risks

The original local `data/worldcup.sqlite3`, `outputs/`, and `precomputed/api/` collection was **not cleared for public push**. The maintainer selected a sanitized public snapshot on 2026-07-13 rather than publishing restricted raw provider data.

The original versions of the following paths are preserved under ignored `.private_data/` and must remain unstaged:

- `.private_data/data/worldcup.sqlite3`;
- `.private_data/precomputed/api/`;
- `.private_data/outputs/`.

The canonical public paths are generated with `scripts/build_public_data_snapshot.py`. The generated database retains CC0 historical matches, factual match fields, and sanitized project predictions while clearing restricted raw payload, odds, roster, lineup, and player-source tables. JSON/CSV snapshots remove restricted provider, market, source, image, player, roster, and raw-payload fields. No private source file is deleted or modified by the builder.

## Maintainer decision

The maintainer approved option 2 below: publish a sanitized data export while retaining restricted raw provider inputs locally. This clears the generated public snapshot for technical publication subject to final scanner, integrity, JSON, application, and staged-diff validation. It is not a legal opinion or permission from any provider.

## Maintainer decision options

1. Obtain written redistribution permission from FIFA, ESPN/Disney, Lyihub, API-Sports, football-data.org, The Odds API, Betfair, and China Sporttery as applicable, and confirm Sportmonks plan/domain coverage.
2. Approve a sanitized public-data export containing CC0 history, project-authored model parameters/predictions, factual tournament fields from permitted sources, and attribution, while leaving restricted raw provider payloads in a private/local store.
3. Replace restricted sources with providers or datasets that publish an explicit open-data license, then regenerate the SQLite and precomputed snapshots.

Publishing the current raw collection merely with a disclaimer is not recommended because a notice does not override provider terms.

## Primary terms reviewed

- [FIFA Terms of Service](https://inside.fifa.com/terms-of-service)
- [Wikimedia Foundation Terms of Use](https://foundation.wikimedia.org/wiki/Policy:Terms_of_Use/en)
- [martj42 dataset page and CC0 designation](https://www.kaggle.com/datasets/martj42/international-football-results-from-1872-to-2017)
- [Disney/ESPN Terms of Use](https://disneytermsofuse.com/english/)
- [Sportmonks Terms of Service](https://www.sportmonks.com/terms-of-service/)
- [API-Sports Terms of Service](https://api-sports.io/terms)
- [football-data.org terms](https://www.football-data.org/about)
- [The Odds API Terms and Conditions](https://the-odds-api.com/terms-and-conditions.html)
- [Betfair General Terms and Conditions](https://www.betfair.com/en/aboutUs/Terms.and.Conditions/)
- [China Sporttery user service agreement](https://www.sporttery.cn/bzzx/20260410/10053082.html?gid=10)
- [Lyihub World Cup site](https://worldcup.lyihub.com/), for which no public redistribution terms were located
