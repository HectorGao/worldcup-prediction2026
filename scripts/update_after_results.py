from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND = PROJECT_ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from worldcup_predictor.service import WorldCupService  # noqa: E402
from export_static_site import export_static_data  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Update World Cup predictions after real results.")
    parser.add_argument("--fetch-online-results", action="store_true", help="Fetch latest finished matches from online sources.")
    parser.add_argument("--use-xgboost", action="store_true", help="Check and use the XGBoost prediction layer.")
    parser.add_argument("--recalculate", action="store_true", help="Recalculate all unfinished predictions.")
    parser.add_argument("--date", default=None, help="Beijing date to sync, YYYY-MM-DD. Defaults to today in Asia/Shanghai.")
    parser.add_argument("--sync-fifa", action="store_true", help="Sync FIFA official World Cup page data before recalculation.")
    parser.add_argument("--sync-fifa-rosters", action="store_true", help="Sync FIFA official roster/lineup data as the primary squad source.")
    parser.add_argument("--sync-footballdata-io", action="store_true", help="Sync supplemental FootballData.io data.")
    parser.add_argument("--sync-sportmonks", action="store_true", help="Sync supplemental SportMonks data.")
    parser.add_argument("--use-sportmonks", action="store_true", help="Use SportMonks as supplemental result/features source.")
    parser.add_argument("--sync-sporttery", action="store_true", help="Refresh live China Sporttery odds.")
    parser.add_argument("--sync-sporttery-history", action="store_true", help="Attempt China Sporttery historical odds sync without fabricating missing odds.")
    parser.add_argument("--backfill-historical", action="store_true", help="Backfill finished World Cup matches from group round 3 through 2026-06-28.")
    parser.add_argument("--train-over25", action="store_true", help="Retrain over2.5 team parameters from 90-minute World Cup scores.")
    parser.add_argument(
        "--all-finished-results",
        action="store_true",
        help="Fetch and reconcile all finished World Cup results in the configured lookback window.",
    )
    parser.add_argument("--regression", action="store_true", help="Write stage-specific regression diagnostics after updating results.")
    parser.add_argument("--stage", default=None, help="Stage diagnostics to run, e.g. round_of_32.")
    parser.add_argument("--db-path", default="data/worldcup.sqlite3", help="SQLite database path.")
    parser.add_argument("--output-dir", default="outputs", help="Directory for updated prediction outputs.")
    parser.add_argument(
        "--skip-precomputed-export",
        action="store_true",
        help="Do not refresh precomputed/api after the local update.",
    )
    parser.add_argument("--precomputed-dir", default="precomputed", help="Directory for Render read-only API JSON.")
    parser.add_argument(
        "--precomputed-simulations",
        type=int,
        default=10000,
        help="Monte Carlo simulations per exported precomputed prediction.",
    )
    parser.add_argument(
        "--precomputed-full-team-details",
        action="store_true",
        help="Export full team detail payloads for the precomputed API cache.",
    )
    args = parser.parse_args()

    service = WorldCupService(db_path=args.db_path)
    result = service.update_after_results(
        fetch_online_results=args.fetch_online_results,
        use_xgboost=args.use_xgboost,
        recalculate=args.recalculate,
        output_dir=args.output_dir,
        date=args.date,
        sync_fifa=args.sync_fifa,
        sync_fifa_rosters=args.sync_fifa_rosters,
        sync_footballdata_io=args.sync_footballdata_io,
        sync_sportmonks=args.sync_sportmonks,
        use_sportmonks=args.use_sportmonks,
        sync_sporttery_odds=args.sync_sporttery,
        sync_sporttery_history=args.sync_sporttery_history,
        backfill_historical_matches=args.backfill_historical,
        train_over25=args.train_over25,
        fetch_all_finished_results=args.all_finished_results,
    )
    if args.regression and (args.stage or "").lower() in {"round_of_32", "r32", "1/16"}:
        result["r32_regression"] = service.run_round_of_32_regression(output_dir=args.output_dir)
    if not args.skip_precomputed_export:
        meta = export_static_data(
            service,
            simulations=args.precomputed_simulations,
            max_dates=None,
            full_team_details=args.precomputed_full_team_details,
            output_dir=Path(args.precomputed_dir),
        )
        result["precomputed_export"] = meta.get("static_export")
    print(
        json.dumps(
            {
                "outputs": result["outputs"],
                "prediction_count": result["prediction_count"],
                "r32_regression": result.get("r32_regression"),
                "precomputed_export": result.get("precomputed_export"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
