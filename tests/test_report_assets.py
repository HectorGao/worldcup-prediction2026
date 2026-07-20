import importlib.util
import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("generate_report_assets", ROOT / "tools" / "generate_report_assets.py")


def test_report_asset_generator_uses_canonical_results_and_persisted_evaluations(tmp_path: Path):
    database = tmp_path / "worldcup.sqlite3"
    output = tmp_path / "assets"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE canonical_match_results (
          match_key TEXT PRIMARY KEY, stage TEXT, home_team TEXT, away_team TEXT,
          home_score_90 INTEGER, away_score_90 INTEGER, result_display TEXT
        );
        CREATE TABLE predictions (fixture_id TEXT PRIMARY KEY, payload_json TEXT, created_at TEXT);
        """
    )
    connection.execute(
        "INSERT INTO canonical_match_results VALUES ('2026-06-11|A|B', '小组赛 第1轮', 'A', 'B', 2, 1, 'A 2-1 B')"
    )
    connection.execute(
        "INSERT INTO predictions VALUES ('fixture-1', ?, '2026-06-10 10:00:00')",
        (
            json.dumps(
                {
                    "probabilities": {"home": 0.6, "draw": 0.25, "away": 0.15},
                    "evaluation": {
                        "canonical_match_key": "2026-06-11|A|B",
                        "prediction_timing": "pre_match",
                        "outcome_hit": True,
                        "exact_score_hit": True,
                        "home_goal_error": 0,
                        "away_goal_error": 0,
                    },
                }
            ),
        ),
    )
    connection.commit()
    connection.close()

    module = importlib.util.module_from_spec(SPEC)
    assert SPEC.loader is not None
    SPEC.loader.exec_module(module)
    metrics = module.generate_assets(database, output)

    assert metrics["canonical_matches"] == 1
    assert metrics["evaluated_predictions"] == 1
    assert metrics["outcome_hit_rate"] == 1.0
    assert metrics["home_goal_mae"] == 0.0
    assert metrics["outcome_metrics"]["home"]["matches"] == 1
    assert metrics["group_vs_knockout"]["group_stage"]["matches"] == 1
    assert (output / "overview.svg").is_file()
    assert (output / "match-detail-flow.svg").is_file()
    assert (output / "final_metrics.json").is_file()
