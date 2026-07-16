# 2026-07-17 semifinal regression archive

This directory preserves the project-authored synchronization, retraining,
regression, prediction, and bracket outputs generated after the two World Cup
semifinals were recorded:

- France 0-2 Spain on 2026-07-15;
- England 1-2 Argentina on 2026-07-16.

The public snapshot was generated from the maintainer's ignored private data
tree with `scripts/build_public_data_snapshot.py`. Raw provider payloads,
restricted odds snapshots, credentials, roster/player source tables, and
provider URLs are not included.

## Archived files

| File | Purpose |
| --- | --- |
| `result_sync_log.json` | Sanitized result synchronization record |
| `model_retraining_report.json` | Full model retraining output |
| `regression_evaluation.json` | Aggregate and per-match regression metrics |
| `r32_regression_metrics_before_after.json` | Round-of-32 90-minute score correction comparison |
| `r32_result_90_error_analysis.json` | Round-of-32 error analysis |
| `world_cup_weight_scheme_comparison.json` | World Cup weighting comparison |
| `updated_predictions.json` / `.csv` | Predictions after retraining |
| `bracket_predictions.json` | Updated knockout bracket projection |

The aggregate regression file records 156 evaluated matches, 64.74% 90-minute
outcome accuracy, log loss 0.7832, and Brier score 0.1518. These metrics describe
this archived run only and are not a guarantee of future prediction quality.
