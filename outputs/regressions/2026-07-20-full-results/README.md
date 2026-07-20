# Full finished-results update — 2026-07-20

This archive records the reviewed full-results synchronization and regression run.

- Source window: ESPN public scoreboard range covering 2026-06-12 through 2026-07-20; local public facts not returned by the current feed remain preserved.
- Result policy: 90-minute goals are the primary outcome; extra-time goals and penalty shootout scores are stored separately.
- Prediction policy: persisted pre-match predictions are restored after result invalidation, with `post_match_evaluation` refreshed for completed matches. Unfinished fixtures are recalculated only when requested.
- Public snapshot: restricted odds and provider payloads were removed by `scripts/build_public_data_snapshot.py`.
- Validation: SQLite `PRAGMA integrity_check` passed; targeted result-sync/service tests passed.

The associated raw provider responses and licensed odds remain in the ignored local private archive and are not part of this public release.
