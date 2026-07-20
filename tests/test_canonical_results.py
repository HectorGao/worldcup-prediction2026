from pathlib import Path

from worldcup_predictor.database import Database
from worldcup_predictor.results import (
    canonical_match_key,
    format_result_display,
    prediction_evaluation,
)
from worldcup_predictor.service import WorldCupService


def test_result_formatter_keeps_90_minute_score_and_adds_knockout_details():
    assert format_result_display({"home_team": "France", "away_team": "England", "home_score_90": 1, "away_score_90": 1}) == "France 1-1 England"
    assert format_result_display(
        {
            "home_team": "France",
            "away_team": "England",
            "home_score_90": 1,
            "away_score_90": 1,
            "home_score_extra_time": 2,
            "away_score_extra_time": 2,
            "home_score_penalties": 4,
            "away_score_penalties": 3,
        }
    ) == "France 1-1 England（加时 2-2，点球 4-3）"


def test_canonical_key_collapses_known_source_aliases():
    assert canonical_match_key("2026-06-16", "Czechia", "Curaçao") == canonical_match_key(
        "2026-06-16", "Czech Republic", "Curacao"
    )
    assert canonical_match_key("2026-06-17", "Bosnia-Herzegovina", "Congo DR") == canonical_match_key(
        "2026-06-17", "Bosnia and Herzegovina", "DR Congo"
    )


def test_database_rebuilds_one_canonical_record_without_removing_raw_source_records(tmp_path: Path):
    db = Database(tmp_path / "worldcup.sqlite3")
    raw = {
        "date": "2026-06-16",
        "stage": "group-stage",
        "home_goals_90": 2,
        "away_goals_90": 1,
        "source_url": "https://example.invalid/result",
        "fetched_at": "2026-06-17T00:00:00Z",
    }
    db.upsert_finished_match_result(
        {
            **raw,
            "match_id": "espn-1",
            "home_team": "Czechia",
            "away_team": "Curaçao",
            "source": "ESPN",
        }
    )
    db.upsert_finished_match_result(
        {
            **raw,
            "match_id": "lyihub-1",
            "home_team": "Czech Republic",
            "away_team": "Curacao",
            "source": "lyihub_worldcup_static_json",
        }
    )

    summary = db.rebuild_canonical_match_results()

    assert summary == {"raw_records": 2, "canonical_records": 1, "duplicate_source_records": 1}
    assert len(db.list_finished_matches()) == 2
    canonical = db.list_canonical_match_results()
    assert len(canonical) == 1
    assert canonical[0]["home_team"] == "Czech Republic"
    assert canonical[0]["result_display"] == "Czech Republic 2-1 Curacao"
    assert canonical[0]["source_match_ids"] == ["espn-1", "lyihub-1"]


def test_prediction_evaluation_uses_90_minute_result_only():
    evaluation = prediction_evaluation(
        predicted_score="1-1",
        result={
            "home_score_90": 1,
            "away_score_90": 1,
            "home_score_extra_time": 2,
            "away_score_extra_time": 1,
        },
    )

    assert evaluation["outcome_hit"] is True
    assert evaluation["exact_score_hit"] is True
    assert evaluation["actual_outcome"] == "draw"
    assert evaluation["actual_score_90"] == "1-1"


def test_service_rebuilds_saved_prediction_evaluation_without_regenerating_model(tmp_path: Path):
    service = WorldCupService(db_path=tmp_path / "worldcup.sqlite3")
    service.db.upsert_finished_match_result(
        {
            "match_id": "result-1",
            "date": "2026-06-16",
            "stage": "group-stage",
            "home_team": "Czechia",
            "away_team": "Curaçao",
            "home_goals_90": 2,
            "away_goals_90": 1,
            "source": "ESPN",
        }
    )
    service.db.rebuild_canonical_match_results()
    service.db.save_prediction(
        "fixture-1",
        {
            "fixture": {
                "id": "fixture-1",
                "date": "2026-06-16",
                "kickoff": "2026-06-16T20:00:00+08:00",
                "home_team": "Czech Republic",
                "away_team": "Curacao",
            },
            "top_scorelines": [{"score": "2-1", "probability": 0.2}],
        },
    )

    summary = service.rebuild_prediction_evaluations()
    saved = service.db.get_prediction("fixture-1")

    assert summary["matched_predictions"] == 1
    assert summary["evaluated_predictions"] == 1
    assert saved["evaluation"]["exact_score_hit"] is True
    assert saved["evaluation"]["canonical_match_key"] == canonical_match_key(
        "2026-06-16", "Czech Republic", "Curacao"
    )
