from pathlib import Path

import httpx

from worldcup_predictor.prediction.ensemble import blend_model_probabilities
from worldcup_predictor.prediction.xgboost_model import validate_probabilities
from worldcup_predictor.result_sync import (
    collect_world_cup_finished_matches,
    evaluate_world_cup_regression,
    fetch_latest_finished_matches,
    retrain_team_ratings_from_world_cup,
    sync_finished_matches_to_local_store,
    update_knockout_bracket_with_result,
    update_team_ratings_from_finished_matches,
    validate_bracket_after_result_sync,
)
from worldcup_predictor.service import WorldCupService
import worldcup_predictor.service as service_module


def penalty_match() -> dict:
    return {
        "match_id": "ko-penalty-1",
        "date": "2026-07-05",
        "stage": "Round of 32",
        "home_team": "Germany",
        "away_team": "Paraguay",
        "home_goals_90": 1,
        "away_goals_90": 1,
        "home_goals_extra_time": None,
        "away_goals_extra_time": None,
        "home_penalties": 3,
        "away_penalties": 4,
        "winner": "Paraguay",
        "loser": "Germany",
        "is_finished": True,
        "decided_by_extra_time": False,
        "decided_by_penalties": True,
        "source": "test_source",
        "source_url": "https://example.test/match",
        "fetched_at": "2026-07-05T22:00:00+00:00",
    }


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


class FakeClient:
    def __init__(self, payloads: dict[str, dict]):
        self.payloads = payloads
        self.requested_dates: list[str] = []

    def get(self, _url: str, params: dict[str, str]):
        date = params["dates"]
        self.requested_dates.append(date)
        return FakeResponse(self.payloads.get(date, {"events": []}))


class FailingClient:
    def get(self, _url: str, params: dict[str, str]):
        raise httpx.ConnectError("boom")


def espn_event(event_id: str, status_name: str, completed: bool, date: str, home_score: int, away_score: int) -> dict:
    return {
        "id": event_id,
        "date": f"{date}T02:00Z",
        "season": {"slug": "group-stage"},
        "links": [{"href": f"https://www.espn.com/soccer/match/_/gameId/{event_id}"}],
        "competitions": [
            {
                "id": event_id,
                "status": {"type": {"name": status_name, "completed": completed, "description": status_name}},
                "competitors": [
                    {
                        "homeAway": "home",
                        "score": str(home_score),
                        "winner": home_score > away_score,
                        "team": {"displayName": f"Home {event_id}"},
                    },
                    {
                        "homeAway": "away",
                        "score": str(away_score),
                        "winner": away_score > home_score,
                        "team": {"displayName": f"Away {event_id}"},
                    },
                ],
            }
        ],
    }


def test_fetch_latest_finished_matches_filters_target_date_and_non_final_events():
    client = FakeClient(
        {
            "20260630": {
                "events": [
                    espn_event("final-1", "STATUS_FINAL", True, "2026-06-30", 2, 1),
                    espn_event("live-1", "STATUS_IN_PROGRESS", False, "2026-06-30", 1, 1),
                ]
            },
            "20260629": {"events": [espn_event("old-1", "STATUS_FINAL", True, "2026-06-29", 3, 0)]},
        }
    )

    matches = fetch_latest_finished_matches(target_date="2026-06-30", timezone="Asia/Shanghai", client=client)

    assert client.requested_dates == ["20260630"]
    assert [match["match_id"] for match in matches] == ["espn-final-1"]
    assert matches[0]["date"] == "2026-06-30"
    assert matches[0]["is_finished"] is True


def test_fetch_latest_finished_matches_returns_empty_when_source_errors():
    matches = fetch_latest_finished_matches(target_date="2026-06-30", client=FailingClient())

    assert matches == []


def test_penalty_result_updates_bracket_but_not_goals():
    teams = {
        "Germany": {"team": "Germany", "attack_rating": 1.5, "defense_rating": 1.0, "elo": 1800},
        "Paraguay": {"team": "Paraguay", "attack_rating": 1.2, "defense_rating": 1.1, "elo": 1700},
    }
    log: set[str] = set()

    update_team_ratings_from_finished_matches([penalty_match()], teams, processed_match_ids=log)

    assert teams["Germany"]["recent_goals_for"] == 1
    assert teams["Germany"]["recent_goals_against"] == 1
    assert teams["Paraguay"]["recent_goals_for"] == 1
    assert teams["Paraguay"]["recent_goals_against"] == 1
    assert teams["Paraguay"]["form_rating"] > teams["Germany"]["form_rating"]
    assert "ko-penalty-1" in log

    bracket = {
        "matches": [
            {"match_id": "ko-penalty-1", "stage": "Round of 32", "home_team": "Germany", "away_team": "Paraguay"},
            {"match_id": "qf-1", "stage": "Round of 16", "home_team": "Winner ko-penalty-1", "away_team": "France"},
        ]
    }
    update_knockout_bracket_with_result(bracket, penalty_match(), teams)

    assert teams["Germany"]["eliminated"] is True
    assert teams["Paraguay"]["eliminated"] is False
    assert bracket["matches"][1]["home_team"] == "Paraguay"


def test_eliminated_team_removed_from_future_matches():
    teams = {
        "Germany": {"team": "Germany", "eliminated": True},
        "Paraguay": {"team": "Paraguay", "eliminated": False},
        "France": {"team": "France", "eliminated": False},
    }
    bracket = {
        "matches": [
            {"match_id": "qf-1", "stage": "Round of 16", "home_team": "Germany", "away_team": "France", "is_finished": False}
        ]
    }

    validation = validate_bracket_after_result_sync(bracket, teams)

    assert validation["valid"] is False
    assert validation["eliminated_future_teams"] == ["Germany"]


def test_xgboost_probabilities_sum_to_one():
    normalized = validate_probabilities({"home": 0.2, "draw": 0.2, "away": 0.2})

    assert round(sum(normalized.values()), 8) == 1.0
    assert normalized == {"home": 1 / 3, "draw": 1 / 3, "away": 1 / 3}

    blended = blend_model_probabilities(
        {"home": 0.5, "draw": 0.25, "away": 0.25},
        None,
        {"home": 0.2, "draw": 0.3, "away": 0.5},
    )

    assert round(sum(blended.values()), 8) == 1.0
    assert set(blended) == {"home", "draw", "away"}


def test_online_result_overrides_local_stale_result(tmp_path: Path):
    service = WorldCupService(db_path=tmp_path / "worldcup.sqlite3")
    service.db.upsert_web_fixture(
        {
            "id": "web-ko-penalty-1",
            "date": "2026-07-05",
            "kickoff": "2026-07-05T20:00:00+00:00",
            "home_team": "Germany",
            "away_team": "Paraguay",
            "group": "Round of 32",
            "venue": "Test Stadium",
            "status": "scheduled",
            "home_score": None,
            "away_score": None,
            "source_url": "https://example.test/local",
        },
        source_name="local_test",
    )

    result = sync_finished_matches_to_local_store([penalty_match()], service.db)
    stored = service.db.get_web_fixture("web-ko-penalty-1")
    saved_result = service.db.get_finished_match("ko-penalty-1")

    assert result["synced_count"] == 1
    assert stored["status"] == "final"
    assert stored["home_score"] == 1
    assert stored["away_score"] == 1
    assert saved_result["winner"] == "Paraguay"
    assert saved_result["home_penalties"] == 3


def test_collect_world_cup_finished_matches_merges_all_final_sources(tmp_path: Path):
    service = WorldCupService(db_path=tmp_path / "worldcup.sqlite3")
    service.db.upsert_lyihub_match(
        {
            "id": "lyihub-group-1",
            "match_id": "group-1",
            "date": "2026-06-12",
            "kickoff": "2026-06-12T12:00:00+00:00",
            "home_team": "Mexico",
            "away_team": "South Africa",
            "group": "小组赛 第1轮",
            "venue": "test",
            "status": "final",
            "home_score": 2,
            "away_score": 0,
            "has_predict": False,
            "payload": {},
        }
    )
    sync_finished_matches_to_local_store([penalty_match()], service.db)

    matches = collect_world_cup_finished_matches(service.db)

    keys = {(match["date"], match["home_team"], match["away_team"]) for match in matches}
    assert ("2026-06-12", "Mexico", "South Africa") in keys
    assert ("2026-07-05", "Germany", "Paraguay") in keys
    assert all(match["is_finished"] for match in matches)


def test_full_retraining_uses_all_world_cup_matches_and_adds_required_fields():
    matches = [
        {
            **penalty_match(),
            "match_id": "group-a",
            "date": "2026-06-12",
            "stage": "Group A",
            "home_team": "Mexico",
            "away_team": "South Africa",
            "home_goals_90": 2,
            "away_goals_90": 0,
            "home_penalties": None,
            "away_penalties": None,
            "winner": "Mexico",
            "loser": "South Africa",
            "decided_by_penalties": False,
        },
        penalty_match(),
    ]
    priors = {
        "Germany": {"team": "Germany", "elo": 1850, "attack_rating": 1.8, "defense_rating": 0.8},
        "Paraguay": {"team": "Paraguay", "elo": 1650, "attack_rating": 1.1, "defense_rating": 1.2},
    }

    result = retrain_team_ratings_from_world_cup(matches, priors)
    germany = result["teams"]["Germany"]
    paraguay = result["teams"]["Paraguay"]

    assert result["match_count"] == 2
    assert result["world_cup_data_weight"] == 0.8
    for field in {
        "strength_rating",
        "attack_rating",
        "midfield_rating",
        "defense_rating",
        "goal_scoring_rating",
        "defensive_stability",
        "form_rating",
        "knockout_rating",
        "eliminated",
        "world_cup_data_weight",
    }:
        assert field in germany
    assert germany["recent_goals_for"] == 1
    assert germany["recent_goals_against"] == 1
    assert paraguay["knockout_rating"] > germany["knockout_rating"]


def test_regression_evaluation_outputs_core_metrics():
    matches = [
        {
            **penalty_match(),
            "match_id": "eval-1",
            "home_team": "Home",
            "away_team": "Away",
            "home_goals_90": 2,
            "away_goals_90": 1,
            "winner": "Home",
            "loser": "Away",
            "decided_by_penalties": False,
        }
    ]
    predictions = {
        "eval-1": {
            "probabilities": {"home": 0.7, "draw": 0.2, "away": 0.1},
            "expected_goals": {"home": 1.5, "away": 1.2},
            "fixture": {"home_team": "Home", "away_team": "Away"},
        }
    }

    evaluation = evaluate_world_cup_regression(matches, predictions)

    assert evaluation["match_count"] == 1
    assert evaluation["accuracy_90"] == 1.0
    assert evaluation["log_loss"] > 0
    assert evaluation["brier_score"] > 0
    assert evaluation["goals_mae"] == 0.35
    assert evaluation["goals_rmse"] > 0
    assert evaluation["advance_accuracy"] == 1.0
    assert evaluation["per_match_errors"][0]["match_id"] == "eval-1"


def test_update_after_results_returns_today_retraining_and_regression_fields(tmp_path: Path, monkeypatch):
    service = WorldCupService(db_path=tmp_path / "worldcup.sqlite3")
    service.db.upsert_lyihub_match(
        {
            "id": "lyihub-old-final",
            "match_id": "old-final",
            "date": "2026-06-12",
            "kickoff": "2026-06-12T12:00:00+00:00",
            "home_team": "Mexico",
            "away_team": "South Africa",
            "group": "小组赛 第1轮",
            "venue": "test",
            "status": "final",
            "home_score": 2,
            "away_score": 0,
            "has_predict": False,
            "payload": {},
        }
    )
    today = {
        **penalty_match(),
        "match_id": "today-final",
        "date": "2026-06-30",
        "stage": "Group A",
        "home_team": "France",
        "away_team": "Senegal",
        "home_goals_90": 2,
        "away_goals_90": 1,
        "home_penalties": None,
        "away_penalties": None,
        "winner": "France",
        "loser": "Senegal",
        "decided_by_penalties": False,
    }

    monkeypatch.setattr(service_module, "fetch_latest_finished_matches", lambda **_kwargs: [today])

    result = service.update_after_results(
        fetch_online_results=True,
        use_xgboost=False,
        recalculate=False,
        output_dir=tmp_path / "outputs",
        date="2026-06-30",
    )

    assert result["today_finished_matches"] == [today]
    assert result["world_cup_finished_match_count"] == 2
    assert result["retraining"]["world_cup_data_weight"] == 0.75
    assert result["regression_evaluation"]["match_count"] == 2
    assert (tmp_path / "outputs" / "regression_evaluation.json").exists()
    assert (tmp_path / "outputs" / "model_retraining_report.json").exists()


def test_update_after_results_does_not_500_when_online_source_fails(tmp_path: Path, monkeypatch):
    service = WorldCupService(db_path=tmp_path / "worldcup.sqlite3")
    service.db.upsert_lyihub_match(
        {
            "id": "lyihub-old-final",
            "match_id": "old-final",
            "date": "2026-06-12",
            "kickoff": "2026-06-12T12:00:00+00:00",
            "home_team": "Brazil",
            "away_team": "Morocco",
            "group": "小组赛 第1轮",
            "venue": "test",
            "status": "final",
            "home_score": 1,
            "away_score": 1,
            "has_predict": False,
            "payload": {},
        }
    )
    monkeypatch.setattr(service_module, "fetch_latest_finished_matches", lambda **_kwargs: [])

    result = service.update_after_results(
        fetch_online_results=True,
        use_xgboost=False,
        recalculate=False,
        output_dir=tmp_path / "outputs",
        date="2026-06-30",
    )

    assert result["today_finished_matches"] == []
    assert result["world_cup_finished_match_count"] == 1
    assert result["sync"]["synced_count"] == 0


def test_update_after_results_falls_back_to_lyihub_for_today_results(tmp_path: Path, monkeypatch):
    service = WorldCupService(db_path=tmp_path / "worldcup.sqlite3")
    monkeypatch.setattr(service_module, "fetch_latest_finished_matches", lambda **_kwargs: [])
    service.lyihub_scraper.fetch_index = lambda: {
        "matches": [
            {
                "match_id": "54327935",
                "kickoff_at": "2026-06-29T17:00:00+00:00",
                "stage": "1/16决赛",
                "team_a": "巴西",
                "team_b": "日本",
                "team_a_id": "bra",
                "team_b_id": "jpn",
                "venue": "test",
                "score": {"team_a": 2, "team_b": 1},
                "score_full": {"team_a": 2, "team_b": 1},
                "has_predict": False,
            },
            {
                "match_id": "future",
                "kickoff_at": "2026-06-30T22:00:00+00:00",
                "stage": "1/16决赛",
                "team_a": "德国",
                "team_b": "巴拉圭",
                "team_a_id": "ger",
                "team_b_id": "par",
                "venue": "test",
                "has_predict": False,
            },
        ]
    }

    result = service.update_after_results(
        fetch_online_results=True,
        use_xgboost=False,
        recalculate=False,
        output_dir=tmp_path / "outputs",
        date="2026-06-30",
    )

    assert [match["match_id"] for match in result["today_finished_matches"]] == ["lyihub-54327935"]
    stored = service.db.get_lyihub_match_by_fixture("lyihub-54327935")
    assert stored["status"] == "final"
    assert stored["home_team"] == "Brazil"
    assert stored["away_team"] == "Japan"
    assert stored["home_score"] == 2
    assert stored["away_score"] == 1
