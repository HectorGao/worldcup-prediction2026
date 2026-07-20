# Full finished-results update — 2026-07-20

This archive records the reviewed full-results synchronization and regression run.

- Source window: ESPN public scoreboard range covering 2026-06-12 through 2026-07-20; local public facts not returned by the current feed remain preserved.
- Result policy: 90-minute goals are the primary outcome; extra-time goals and penalty shootout scores are stored separately.
- Canonical result policy: `finished_match_results` remains raw provenance; the active `canonical_match_results` build contains the real 104 unique tournament matches. Team aliases are normalized and all hit metrics use 90-minute scores only.
- Prediction policy: saved forecasts are not rerun after final results. The rebuild attaches a derived `evaluation` object to 104 matched records; 1 is timestamp-eligible pre-match and 103 are clearly labelled reconstructed post-match-or-unknown audits.
- Final audit: outcome hit rate 67.3% (70/104), exact-score hit rate 23.1% (24/104), score MAE 0.692, RMSE 1.052, and Brier score 0.142. These are reconstructed-record diagnostics, not a live pre-match performance claim.
- Public snapshot: raw provider payloads and restricted roster/player data were removed. The reviewed Sporttery snapshot is retained only through the documented safe field whitelist; it is not relicensed.
- Validation: SQLite `PRAGMA integrity_check`, repository safety check, and full test suite passed. Reproducible charts and metrics are in `docs/assets/`.

The associated raw provider responses remain in the ignored local private archive and are not part of this public release. See `DATA.md` for the separate rights boundary for the reviewed Sporttery historical snapshot.
