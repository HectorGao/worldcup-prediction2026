# Canonical 104-match results, prediction evaluation, and final report

> **For Codex:** Execute this plan in the isolated `codex/final-data-update-2026-07-20` worktree. Do not alter remote history, delete source records, or overwrite the user's main worktree.

**Goal:** Make 104 real, unique World Cup matches the sole evaluation population; preserve raw source records and historical Sporttery snapshots; rebuild each persisted pre-match prediction's evaluation; generate reproducible final statistics and bilingual report assets.

**Architecture:** Keep `finished_match_results` as raw-source provenance. Add `canonical_match_results`, keyed by canonical date/home/away identity, for all UI and reports. Store the requested score fields and a canonical display string there. Attach an immutable evaluation object to each existing prediction instead of recomputing post-result forecasts, preventing look-ahead leakage.

**Data rules:** ESPN is preferred for facts where it exists; the Lyihub final records supply the remaining knockout fixtures. Team aliases normalize to one identity. All 104 canonical records are retained with source provenance. Sporttery snapshots are copied into the public snapshot only after a whitelist-based safety transform; private provider payload tables remain excluded.

## Steps

1. Add team aliases and score/result utilities with tests for 90-minute, extra-time, and penalty displays.
2. Add canonical-result storage and idempotent rebuild logic that never deletes raw source records; write tests proving 104-equivalent alias rows collapse to one canonical record.
3. Build prediction-evaluation reconstruction from saved model outputs. Record outcome and exact-score hit status against 90-minute results only, plus score errors, without rerunning the model after final scores are known.
4. Expose canonical results and evaluation fields through match summaries and single-match analysis; make the browser use the canonical display/status values.
5. Update the public-snapshot policy and tests to retain a safe, documented Sporttery historical-odds subset while continuing to purge private provider/raw fields.
6. Run the canonical rebuild against the reviewed private database copy, build the public database, then generate deterministic metrics and bilingual SVG assets from the public database.
7. Update `README.md`, `README.zh-CN.md`, and `DATA.md` with the 104-match methodology, data boundary, UI detail path, generated metrics, and reproducibility commands.
8. Run focused tests, full regression tests, public-data validation, secret scan, database integrity checks, and static export. Review the staged diff before any commit or push.

## Acceptance criteria

- The canonical table contains exactly 104 rows and raw source rows remain intact.
- Every evaluated prediction references one canonical match; summary explicitly reports both evaluated and unavailable-prediction counts.
- Outcome/exact score evaluation uses only 90-minute scores; display includes extra-time/penalties as informational detail.
- Final metric files and figures are generated from the database, not hand-written.
- Public database passes integrity/safety validation and includes only safe Sporttery fields.
- No force push, reset, rebase, history rewrite, or bulk deletion is performed.
