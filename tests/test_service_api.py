from pathlib import Path

from fastapi.testclient import TestClient

from worldcup_predictor.api import create_app
from worldcup_predictor.service import WorldCupService


def test_daily_sync_is_idempotent_and_prediction_contains_required_fields(tmp_path: Path):
    db_path = tmp_path / "worldcup.sqlite3"
    service = WorldCupService(db_path=db_path)

    first = service.sync_date("2026-06-15")
    second = service.sync_date("2026-06-15")

    assert first["fixture_count"] == 4
    assert second["fixture_count"] == 4
    assert service.count_fixtures("2026-06-15") == 4

    fixture_id = first["fixtures"][0]["id"]
    prediction = service.predict_fixture(fixture_id)

    assert prediction["fixture"]["id"] == fixture_id
    assert set(prediction["probabilities"]) == {"home", "draw", "away"}
    assert "score_matrix" in prediction
    assert len(prediction["top_scorelines"]) == 6
    assert prediction["llm_vote_audit"]["weight_cap"] == 0.15
    assert "数据分析" in prediction["chinese_report"]


def test_fastapi_endpoints_expose_matches_predictions_reports_and_health(tmp_path: Path):
    db_path = tmp_path / "worldcup.sqlite3"
    app = create_app(db_path=db_path)
    client = TestClient(app)

    sync_response = client.post("/api/sync", params={"date": "2026-06-15"})
    assert sync_response.status_code == 200
    fixture_id = sync_response.json()["fixtures"][0]["id"]

    matches_response = client.get("/api/matches", params={"date": "2026-06-15"})
    assert matches_response.status_code == 200
    assert len(matches_response.json()["matches"]) == 4

    predict_response = client.post(f"/api/predict/{fixture_id}")
    assert predict_response.status_code == 200
    assert predict_response.json()["fixture"]["id"] == fixture_id

    get_prediction_response = client.get(f"/api/predictions/{fixture_id}")
    assert get_prediction_response.status_code == 200
    assert get_prediction_response.json()["fixture"]["id"] == fixture_id

    report_response = client.get("/api/reports/daily", params={"date": "2026-06-15"})
    assert report_response.status_code == 200
    assert "世界杯比赛预测日报" in report_response.json()["report"]

    health_response = client.get("/api/health/data-sources")
    assert health_response.status_code == 200
    assert health_response.json()["polling_cadence_minutes"] == 15

    validate_response = client.post("/api/health/validate-sources")
    assert validate_response.status_code == 200
    assert {"name", "configured", "reachable", "auth_valid", "checked_at"} <= set(
        validate_response.json()["sources"][0]
    )


def test_api_allows_file_page_cors_origin(tmp_path: Path):
    app = create_app(db_path=tmp_path / "worldcup.sqlite3")
    client = TestClient(app)

    response = client.options(
        "/api/matches/available-dates",
        headers={
            "Origin": "null",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "null"


def test_match_api_returns_chinese_names_and_flags(tmp_path: Path):
    service = WorldCupService(db_path=tmp_path / "worldcup.sqlite3")
    service.save_web_fixtures(
        [
            {
                "id": "web-france-senegal",
                "date": "2026-06-23",
                "kickoff": "2026-06-23",
                "home_team": "France",
                "away_team": "Senegal",
                "group": "Group I",
                "venue": "Test Stadium",
                "status": "scheduled",
            }
        ],
        source_name="test",
    )
    app = create_app(db_path=tmp_path / "worldcup.sqlite3")
    app.state.service = service
    client = TestClient(app)

    match = client.get("/api/matches", params={"date": "2026-06-23"}).json()["matches"][0]

    assert match["home_team"] == "France"
    assert match["home_team_zh"] == "法国"
    assert match["away_team_zh"] == "塞内加尔"
    assert match["home_flag"] == "🇫🇷"
    assert match["away_flag"] == "🇸🇳"
