# Sporttery historical snapshot audit — 2026-07-20

This is an operational preservation record, not a license grant or betting recommendation.

## Protected private backup

The full local snapshot is preserved outside Git at:

```text
.private_data/odds-archive/2026-07-20/worldcup-with-odds.sqlite3
```

- Size: 42,188,800 bytes
- SHA-256: `3191b821faa9a8e04a09c14cfb72882393072fdf2ed1ab0282ce3bbbf291ac1d`
- `PRAGMA integrity_check`: `ok`
- Historical Git database copies: four files under `git-history/`

No reset, rebase, filter-repo, filter-branch, force push, or deletion was used for this release.

## Snapshot inventory

The reviewed source database contains 20 `sporttery_odds_snapshots` rows for 20 distinct date/home/away fixtures. The observed fixture-date range is 2026-07-18 through 2026-07-21, with four fixture dates represented.

The public release retains only the vetted fields: `match_num`, `date`, `home_team`, `away_team`, `source`, `updated_at`, `h2h`, `handicap`, `handicap_line`, and `totals`. The snapshot is a historical point-in-time record for research, model reproduction, and prediction evaluation; it is not current odds and is not betting advice.

## Credential and personal-data check

The reviewed payload-key inventory is limited to `away_team`, `date`, `h2h`, `handicap`, `handicap_line`, `home_team`, `league`, `match_num`, `match_num_date`, `source`, `totals`, and `updated_at`. A case-insensitive scan for API keys, tokens, cookies, authorization headers, passwords, secrets, signatures, sessions, email addresses, and phone numbers found no matching payload keys. The public builder applies a positive field whitelist again when producing `data/worldcup.sqlite3`.

See [DATA.md](../../DATA.md) for the source-rights boundary and public distribution policy.
