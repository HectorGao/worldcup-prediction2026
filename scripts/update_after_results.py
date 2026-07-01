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


def main() -> None:
    parser = argparse.ArgumentParser(description="Update World Cup predictions after real results.")
    parser.add_argument("--fetch-online-results", action="store_true", help="Fetch latest finished matches from online sources.")
    parser.add_argument("--use-xgboost", action="store_true", help="Check and use the XGBoost prediction layer.")
    parser.add_argument("--recalculate", action="store_true", help="Recalculate all unfinished predictions.")
    parser.add_argument("--date", default=None, help="Beijing date to sync, YYYY-MM-DD. Defaults to today in Asia/Shanghai.")
    parser.add_argument("--db-path", default="data/worldcup.sqlite3", help="SQLite database path.")
    parser.add_argument("--output-dir", default="outputs", help="Directory for updated prediction outputs.")
    args = parser.parse_args()

    service = WorldCupService(db_path=args.db_path)
    result = service.update_after_results(
        fetch_online_results=args.fetch_online_results,
        use_xgboost=args.use_xgboost,
        recalculate=args.recalculate,
        output_dir=args.output_dir,
        date=args.date,
    )
    print(json.dumps({"outputs": result["outputs"], "prediction_count": result["prediction_count"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
