from pathlib import Path

from fastapi.testclient import TestClient

from worldcup_predictor.api import create_app
from worldcup_predictor.data.providers import BetfairOddsProvider, SportteryOddsProvider
from worldcup_predictor.data.sporttery_snapshot import SPORTTERY_LOTTERY_SNAPSHOT
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
    assert {"elo", "poisson", "monte_carlo", "market", "xgboost", "ensemble", "value_analysis", "learning"} <= set(prediction)
    assert prediction["monte_carlo"]["simulations"] >= 10_000
    assert set(prediction["ensemble"]["weights"]) >= {"elo", "poisson", "monte_carlo", "xgboost"}
    assert "h2h" in prediction["odds_markets"]
    assert "lottery_market" in prediction
    assert "handicap_analysis" in prediction
    assert "score_heatmap" in prediction
    assert "model_weight_run" in prediction
    assert "odds_data_status" in prediction
    assert prediction["odds_data_status"]["market_available"] is True
    assert isinstance(prediction["value_analysis"]["risk_warnings"], list)
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

    resim_response = client.post(f"/api/predict/{fixture_id}", params={"simulations": 5000})
    assert resim_response.status_code == 200
    assert resim_response.json()["monte_carlo"]["simulations"] == 5000

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

    weights_response = client.get("/api/models/weights", params={"date": "2026-06-15"})
    assert weights_response.status_code == 200
    assert "weights" in weights_response.json()


def test_default_match_date_skips_completed_day_and_shows_sporttery_window(tmp_path: Path):
    db_path = tmp_path / "worldcup.sqlite3"
    service = WorldCupService(db_path=db_path)
    service.db.upsert_lyihub_match(
        {
            "id": "lyihub-completed-1",
            "match_id": "completed-1",
            "date": "2026-06-28",
            "kickoff": "2026-06-28T02:00:00+00:00",
            "home_team": "Jordan",
            "away_team": "Argentina",
            "home_team_zh_source": "约旦",
            "away_team_zh_source": "阿根廷",
            "group": "小组赛 第3轮",
            "venue": "test",
            "status": "final",
            "home_score": 1,
            "away_score": 3,
            "has_predict": False,
            "payload": {},
        }
    )
    for market in SPORTTERY_LOTTERY_SNAPSHOT:
        service.db.upsert_lyihub_match(
            {
                "id": market["fixture_id"],
                "match_id": market["fixture_id"].replace("lyihub-", ""),
                "date": market["date"],
                "kickoff": f"{market['date']}T03:00:00+08:00",
                "home_team": market["home_team"],
                "away_team": market["away_team"],
                "home_team_zh_source": market["home_team"],
                "away_team_zh_source": market["away_team"],
                "group": "1/16决赛",
                "venue": "test",
                "status": "scheduled",
                "home_score": None,
                "away_score": None,
                "has_predict": False,
                "payload": {},
            }
        )

    assert service.default_match_date("2026-06-28") == "2026-06-29"
    matches = service.list_matches("2026-06-29")

    assert len(matches) == len(SPORTTERY_LOTTERY_SNAPSHOT)
    assert {match["id"] for match in matches} == {market["fixture_id"] for market in SPORTTERY_LOTTERY_SNAPSHOT}
    assert all("China Sporttery snapshot" in str(match.get("market_source")) for match in matches)
    summaries = service.list_matches_with_prediction_summary("2026-06-29")
    first_summary = next(match for match in summaries if match["id"] == "lyihub-54327935")
    assert first_summary["lottery_market"]["match_no"] == "周一074"
    assert first_summary["odds_markets"]["h2h"]["odds"]["home"] == 1.49
    assert first_summary["odds_markets"]["handicap"]["odds"]["away"] == 2.26
    prediction = service.predict_fixture("lyihub-54327935")
    assert prediction["lottery_market"]["match_no"] == "周一074"
    assert prediction["score_heatmap"]["handicap"] == -1.0
    assert set(prediction["score_heatmap"]["handicap_probabilities"]) == {"home", "draw", "away"}
    assert "7-7" in prediction["score_heatmap"]["handicap_regions"]


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


def test_model_weight_recalibration_uses_only_completed_predictions_before_date(tmp_path: Path):
    service = WorldCupService(db_path=tmp_path / "worldcup.sqlite3")
    completed = [
        ("cal-1", "2026-06-20", 2, 0, "Team A", "Team B"),
        ("cal-2", "2026-06-21", 1, 0, "Team C", "Team D"),
        ("cal-3", "2026-06-22", 0, 2, "Team E", "Team F"),
        ("future", "2026-06-28", 0, 4, "Team G", "Team H"),
    ]
    for fixture_id, date, home_score, away_score, home_team, away_team in completed:
        service.db.upsert_fixture(
            {
                "id": fixture_id,
                "date": date,
                "kickoff": f"{date}T12:00:00+08:00",
                "home_team": home_team,
                "away_team": away_team,
                "group": "Group Test",
                "venue": "Test",
                "status": "final",
                "home_score": home_score,
                "away_score": away_score,
                "home_elo": 1700,
                "away_elo": 1700,
            }
        )
        service.db.save_prediction(
            fixture_id,
            {
                "ensemble": {
                    "source_probabilities": {
                        "elo": {"home": 0.50, "draw": 0.25, "away": 0.25},
                        "poisson": {"home": 0.72, "draw": 0.18, "away": 0.10}
                        if home_score > away_score
                        else {"home": 0.10, "draw": 0.18, "away": 0.72},
                        "monte_carlo": {"home": 0.66, "draw": 0.20, "away": 0.14}
                        if home_score > away_score
                        else {"home": 0.14, "draw": 0.20, "away": 0.66},
                        "market": {"home": 0.24, "draw": 0.30, "away": 0.46}
                        if home_score > away_score
                        else {"home": 0.46, "draw": 0.30, "away": 0.24},
                        "xgboost": {"home": 0.68, "draw": 0.19, "away": 0.13}
                        if home_score > away_score
                        else {"home": 0.13, "draw": 0.19, "away": 0.68},
                    }
                }
            },
        )

    run = service.recalibrate_model_weights("2026-06-27")

    assert run["sample_count"] == 3
    assert run["weights"]["poisson"] > run["weights"]["market"]
    assert service.db.get_model_weight_run("2026-06-27")["weights"] == run["weights"]


def test_sporttery_parser_normalizes_odds_without_live_fetch():
    provider = SportteryOddsProvider()
    events = provider.parse_events(
        {
            "value": {
                "matchInfoList": [
                    {
                        "subMatchList": [
                            {
                                "matchDate": "2026-06-23",
                                "businessDate": "2026-06-23",
                                "matchNumStr": "周二001",
                                "leagueAllName": "世界杯",
                                "homeTeamAllName": "法国",
                                "awayTeamAllName": "塞内加尔",
                                "had": {"h": "1.65", "d": "3.60", "a": "5.20", "goalLine": "0"},
                                "hhad": {"h": "3.10", "d": "3.35", "a": "1.92", "goalLine": "-1"},
                                "ttg": {"over": "1.78", "under": "2.02"},
                            }
                        ]
                    }
                ]
            }
        }
    )

    assert events[0]["home_team"] == "法国"
    assert events[0]["match_num"] == "周二001"
    assert events[0]["league"] == "世界杯"
    assert events[0]["h2h"] == {"home": 1.65, "draw": 3.6, "away": 5.2}
    assert events[0]["handicap"]["away"] == 1.92
    assert events[0]["handicap_line"] == "-1"
    assert events[0]["totals"]["over"] == 1.78


def test_betfair_parser_normalizes_match_odds_handicap_and_totals():
    provider = BetfairOddsProvider(app_key="app", session_token="session")
    catalogue = [
        {
            "marketId": "1.100",
            "marketStartTime": "2026-06-23T12:00:00Z",
            "event": {"id": "event-1", "name": "France v Senegal"},
            "description": {"marketType": "MATCH_ODDS"},
            "runners": [
                {"selectionId": 11, "runnerName": "France"},
                {"selectionId": 12, "runnerName": "The Draw"},
                {"selectionId": 13, "runnerName": "Senegal"},
            ],
        },
        {
            "marketId": "1.101",
            "marketStartTime": "2026-06-23T12:00:00Z",
            "event": {"id": "event-1", "name": "France v Senegal"},
            "description": {"marketType": "ASIAN_HANDICAP"},
            "runners": [
                {"selectionId": 21, "runnerName": "France -1.0"},
                {"selectionId": 22, "runnerName": "Senegal +1.0"},
            ],
        },
        {
            "marketId": "1.102",
            "marketStartTime": "2026-06-23T12:00:00Z",
            "event": {"id": "event-1", "name": "France v Senegal"},
            "description": {"marketType": "OVER_UNDER_25"},
            "runners": [
                {"selectionId": 31, "runnerName": "Over 2.5 Goals"},
                {"selectionId": 32, "runnerName": "Under 2.5 Goals"},
            ],
        },
    ]
    books = [
        {
            "marketId": "1.100",
            "runners": [
                {"selectionId": 11, "ex": {"availableToBack": [{"price": 1.82}]}},
                {"selectionId": 12, "ex": {"availableToBack": [{"price": 3.45}]}},
                {"selectionId": 13, "ex": {"availableToBack": [{"price": 4.6}]}},
            ],
        },
        {
            "marketId": "1.101",
            "runners": [
                {"selectionId": 21, "ex": {"availableToBack": [{"price": 2.18}]}},
                {"selectionId": 22, "ex": {"availableToBack": [{"price": 1.76}]}},
            ],
        },
        {
            "marketId": "1.102",
            "runners": [
                {"selectionId": 31, "ex": {"availableToBack": [{"price": 1.9}]}},
                {"selectionId": 32, "ex": {"availableToBack": [{"price": 1.96}]}},
            ],
        },
    ]

    events = provider.parse_markets(catalogue, books)
    enriched = provider._enrich_fixture(
        {
            "home_team": "France",
            "away_team": "Senegal",
            "market_home": None,
            "market_draw": None,
            "market_away": None,
        },
        events,
    )

    assert events[0]["h2h"] == {"home": 1.82, "draw": 3.45, "away": 4.6}
    assert events[0]["totals"] == {"over": 1.9, "under": 1.96}
    assert enriched["market_source"] == "Betfair Exchange"
    assert enriched["market_home"] == 1.82
    assert enriched["market_handicap"] == {"home": 2.18, "away": 1.76}
