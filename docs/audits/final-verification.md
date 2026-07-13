# Open-source release verification

Verified on 2026-07-13 for branch `codex/open-source-readiness` before publication.

## Release contents

- Complete existing application, prediction models, browser interface, local launcher, update logic, deployment configuration, and tests are retained.
- The pre-existing local commit `851b7e2` is retained. It auto-enables checked-in precomputed responses on Render, adds date fallback behavior, and includes an API regression test.
- `data/worldcup.sqlite3`, `outputs/`, and `precomputed/api/` contain the compliant public snapshot.
- Raw licensed/provider payloads and source collections remain under ignored `.private_data/`.
- Apache License 2.0 applies to project-authored code. Third-party data remains governed separately by `DATA.md`.

## Public data snapshot

The committed SQLite snapshot retains 49,476 CC0 historical match rows, 866 fixture rows, and 106 project prediction rows. It contains zero rows in `raw_provider_payloads`, `sporttery_odds_snapshots`, `lyihub_players`, and `squad_players`. Fixture market columns are empty. Public JSON and CSV snapshots contain no raw provider, odds-market, roster, player, image, or provider-source fields matched by the snapshot policy.

The release keeps factual match identifiers and provider integration names where they are necessary application metadata. It does not grant rights to provider content. The complete source and redistribution review is in `docs/audits/data-redistribution-review.md`.

## Ignored local artifacts

| Pattern/path | Reason |
| --- | --- |
| `.env`, `.env.*` except `.env.example` | Credentials and local configuration |
| `.private_data/` | Restricted raw provider inputs retained locally for sanitization |
| `.venv/`, `.pytest_cache/`, `__pycache__/`, `*.pyc` | Reproducible environment and test/runtime caches |
| SQLite WAL, SHM, and journal files | Transient database state |
| `data/worldcup.db` | Empty obsolete local SQLite shell, superseded by the canonical snapshot |
| `.DS_Store`, `world_cup_prediction.egg-info/` | Local OS and packaging output |
| `dist/` | Reproducible static build |

## Security and privacy audit

- `.env` is ignored and has never been tracked on the release branch.
- Current-tree, staged-content, and reachable branch-history scans found no private keys, GitHub tokens, OpenAI keys, AWS access keys, or prohibited credential files.
- SQLite schema inspection found no credential-, password-, token-, email-, or phone-named columns.
- No email address or other obvious personal information was found in published working-tree files. Normal Git author names and email addresses remain in commit metadata.
- No Git history was rewritten. If a credential is later suspected, rotate it first and coordinate any history rewrite explicitly.

## File size and storage decision

The largest blob reachable from the release branch is `data/worldcup.sqlite3` at 18,673,664 bytes. All other release blobs are below 3 MiB. This is below the repository's 50 MiB warning threshold and GitHub's per-file limit, so Git LFS, Releases, and external storage are not required for this snapshot. Reassess if future database snapshots approach 50 MiB.

## Verification evidence

- Repository safety scanner: passed.
- SQLite `PRAGMA integrity_check`: `ok`.
- Public snapshot sanitizer tests: 3 passed.
- Full local test suite: 129 passed; one upstream Starlette/httpx deprecation warning.
- Clean-clone installation: succeeded from `python -m pip install -e ".[dev]"` in a new virtual environment.
- Clean-clone startup: `/healthz`, `/api/matches/available-dates`, and `/api/matches?date=2026-06-30` returned HTTP 200; the snapshot exposed 35 match dates and populated match cards.
- Existing runtime/algorithm paths after the approved design baseline were unchanged. Only deterministic tests were isolated from live network providers.

## Release gate

The branch is ready to push and open as a pull request. Merge remains a maintainer decision after reviewing the public data paths, data-rights boundary, CI results, and Git metadata identity.
