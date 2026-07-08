from pathlib import Path
import json

from fastapi.testclient import TestClient

from worldcup_predictor.api import create_app
from worldcup_predictor.data.providers import BetfairOddsProvider, SportteryOddsProvider
from worldcup_predictor.data.sporttery_snapshot import SPORTTERY_LOTTERY_SNAPSHOT
from worldcup_predictor.service import WorldCupService


def seed_sporttery_snapshot_matches(service: WorldCupService) -> None:
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
    service.ensure_sporttery_lottery_snapshot()


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
    assert prediction["odds_data_status"]["market_available"] is False
    assert "中国体育彩票" in prediction["odds_data_status"]["reason"]
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


def test_deployment_healthcheck_and_env_db_path(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "render" / "worldcup.sqlite3"
    monkeypatch.setenv("WORLDCUP_DB_PATH", str(db_path))
    monkeypatch.setenv("WORLDCUP_CORS_ORIGINS", "https://preview.example.com")
    app = create_app()
    client = TestClient(app)

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["deployment"]["use_precomputed"] is False
    assert db_path.exists()


def test_render_precomputed_mode_serves_json_without_syncing(tmp_path: Path, monkeypatch):
    precomputed = tmp_path / "precomputed"
    api_root = precomputed / "api"
    (api_root / "matches").mkdir(parents=True)
    (api_root / "predictions").mkdir(parents=True)
    (api_root / "matches" / "fixture-1").mkdir(parents=True)
    (api_root / "teams").mkdir(parents=True)
    (api_root / "health").mkdir(parents=True)
    (api_root / "lyihub" / "matches").mkdir(parents=True)
    (api_root / "reports" / "daily").mkdir(parents=True)
    payloads = {
        api_root / "matches" / "available-dates.json": {"dates": ["2026-07-08"], "default_date": "2026-07-08"},
        api_root / "matches" / "2026-07-08.json": {
            "date": "2026-07-08",
            "requested_date": "2026-07-08",
            "matches": [{"id": "fixture-1", "home_team": "Brazil", "away_team": "Germany"}],
        },
        api_root / "meta.json": {"static_export": {"generated_at": "2026-07-08T08:00:00+08:00"}},
        api_root / "predictions" / "fixture-1.json": {"fixture": {"id": "fixture-1"}, "probabilities": {"home": 0.4}},
        api_root / "matches" / "fixture-1" / "analysis.json": {"fixture_id": "fixture-1", "prediction": {}},
        api_root / "teams" / "rankings.json": {"teams": []},
        api_root / "health" / "data-sources.json": {"providers": [], "polling_cadence_minutes": 15},
        api_root / "health" / "roster-data.json": {"queue_pending": 0},
        api_root / "lyihub" / "rounds.json": {"rounds": []},
        api_root / "lyihub" / "matches" / "all.json": {"matches": []},
        api_root / "reports" / "daily" / "2026-07-08.json": {"date": "2026-07-08", "report": "cached"},
        api_root / "knockout.json": {"rounds": []},
    }
    for path, payload in payloads.items():
        path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("WORLDCUP_USE_PRECOMPUTED", "1")
    monkeypatch.setenv("WORLDCUP_READ_ONLY", "1")
    monkeypatch.setenv("WORLDCUP_PRECOMPUTED_DIR", str(precomputed))
    app = create_app(db_path=tmp_path / "empty.sqlite3")
    app.state.service.sync_date = lambda date: (_ for _ in ()).throw(AssertionError("sync_date should not run"))
    client = TestClient(app)

    matches_response = client.get("/api/matches", params={"date": "2026-07-08"})
    assert matches_response.status_code == 200
    assert matches_response.json()["matches"][0]["id"] == "fixture-1"
    assert client.get("/api/matches/available-dates").json()["default_date"] == "2026-07-08"
    assert client.get("/api/meta").json()["deployment"]["read_only"] is True
    assert client.post("/api/predict/fixture-1").json()["fixture"]["id"] == "fixture-1"

    sync_response = client.post("/api/sync", params={"date": "2026-07-08"})
    assert sync_response.status_code == 409
    assert sync_response.json()["detail"]["error"] == "read_only_deployment"


def test_precomputed_mode_reports_missing_json(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("WORLDCUP_USE_PRECOMPUTED", "1")
    monkeypatch.setenv("WORLDCUP_PRECOMPUTED_DIR", str(tmp_path / "precomputed"))
    client = TestClient(create_app(db_path=tmp_path / "empty.sqlite3"))

    response = client.get("/api/matches", params={"date": "2026-07-08"})

    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "precomputed_json_missing"


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
    seed_sporttery_snapshot_matches(service)

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


def test_round_sync_endpoint_returns_sporttery_predictions(tmp_path: Path, monkeypatch):
    app = create_app(db_path=tmp_path / "worldcup.sqlite3")
    service = app.state.service
    seed_sporttery_snapshot_matches(service)
    monkeypatch.setattr(
        service,
        "update_after_results",
        lambda **kwargs: {
            "today_finished_matches": [],
            "world_cup_finished_match_count": 0,
            "retraining": {"world_cup_data_weight": 0.0},
            "regression_evaluation": {},
            "prediction_count": 0,
        },
    )
    monkeypatch.setattr(
        service,
        "refresh_sporttery_odds",
        lambda: {
            "source": "https://m.sporttery.cn/mjc/jsq/zqspf/",
            "mode": "snapshot_fallback",
            "updated": len(SPORTTERY_LOTTERY_SNAPSHOT),
        },
    )
    client = TestClient(app)

    response = client.post("/api/rounds/sync", params={"date": "2026-06-29"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["sporttery"]["updated"] == len(SPORTTERY_LOTTERY_SNAPSHOT)
    assert len(payload["predictions"]) == len(SPORTTERY_LOTTERY_SNAPSHOT)
    first = payload["predictions"][0]
    assert {"home", "draw", "away"} <= set(first["probabilities_90"])
    assert {"home", "draw", "away"} <= set(first["handicap_probabilities"])
    assert first["lottery_market"]["available"] is True


def test_round_regress_endpoint_returns_probability_delta(tmp_path: Path):
    app = create_app(db_path=tmp_path / "worldcup.sqlite3")
    service = app.state.service
    seed_sporttery_snapshot_matches(service)
    service.db.upsert_finished_match_result(
        {
            "match_id": "finished-1",
            "date": "2026-06-20",
            "stage": "小组赛 第1轮",
            "home_team": "England",
            "away_team": "Iran",
            "home_goals_90": 2,
            "away_goals_90": 0,
            "winner": "England",
            "loser": "Iran",
            "source": "test",
            "fetched_at": "2026-06-20T18:00:00+00:00",
        }
    )
    service.predict_fixture(SPORTTERY_LOTTERY_SNAPSHOT[0]["fixture_id"])
    client = TestClient(app)

    response = client.post("/api/rounds/regress", params={"date": "2026-06-29", "auto_sync": False})

    assert response.status_code == 200
    payload = response.json()
    assert payload["needs_sync"] is False
    assert payload["finished_match_count"] == 1
    assert payload["unfinished_predictions"]
    assert "regression_evaluation" in payload
    assert "probability_delta" in payload["unfinished_predictions"][0]


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


def test_simulation_rankings_filter_eliminated_and_non_bracket_teams(tmp_path: Path):
    service = WorldCupService(db_path=tmp_path / "worldcup.sqlite3")
    for team, elo, eliminated in [
        ("Nigeria", 1900, False),
        ("France", 1800, False),
        ("Sweden", 1750, True),
        ("Paraguay", 1720, False),
    ]:
        service.db.save_team_profile(team, {"team": team, "elo": elo, "strength_rating": elo, "eliminated": eliminated})
    service.db.upsert_lyihub_match(
        {
            "id": "lyihub-r32",
            "match_id": "r32",
            "date": "2026-07-01",
            "kickoff": "2026-07-01T12:00:00+00:00",
            "home_team": "France",
            "away_team": "Sweden",
            "group": "1/16决赛",
            "stage": "1/16决赛",
            "venue": "test",
            "status": "final",
            "home_score": 3,
            "away_score": 0,
            "has_predict": False,
            "payload": {},
        }
    )
    service.db.upsert_lyihub_match(
        {
            "id": "lyihub-qf",
            "match_id": "qf",
            "date": "2026-07-05",
            "kickoff": "2026-07-05T12:00:00+00:00",
            "home_team": "France",
            "away_team": "Paraguay",
            "group": "1/8决赛",
            "stage": "1/8决赛",
            "venue": "test",
            "status": "scheduled",
            "has_predict": False,
            "payload": {},
        }
    )

    payload = service.simulation_rankings()

    teams = [team["team"] for team in payload["teams"]]
    assert "Nigeria" not in teams
    assert "Sweden" not in teams
    assert {"France", "Paraguay"} <= set(teams)
    assert any("Nigeria" in warning for warning in payload["warnings"])


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


def test_refresh_sporttery_endpoint_groups_live_odds_and_reports_unmatched(tmp_path: Path):
    service = WorldCupService(db_path=tmp_path / "worldcup.sqlite3")
    service.db.upsert_lyihub_match(
        {
            "id": "lyihub-france-senegal",
            "match_id": "france-senegal",
            "date": "2026-07-01",
            "kickoff": "2026-07-01T20:00:00+08:00",
            "home_team": "France",
            "away_team": "Senegal",
            "home_team_zh_source": "France",
            "away_team_zh_source": "Senegal",
            "group": "1/16决赛",
            "venue": "test",
            "status": "scheduled",
            "home_score": None,
            "away_score": None,
            "has_predict": False,
            "payload": {},
        }
    )
    service.providers.sporttery_odds_provider.fetch_odds = lambda: [
        {
            "source": "China Sporttery",
            "date": "2026-07-01",
            "match_num": "周三001",
            "league": "世界杯",
            "home_team": "France",
            "away_team": "Senegal",
            "h2h": {"home": 1.7, "draw": 3.4, "away": 5.2},
            "handicap": {"home": 3.1, "draw": 3.3, "away": 1.9},
            "handicap_line": "-1",
        },
        {
            "source": "China Sporttery",
            "date": "2026-07-02",
            "match_num": "周四001",
            "league": "世界杯",
            "home_team": "Unknown A",
            "away_team": "Unknown B",
            "h2h": {"home": 2.0, "draw": 3.0, "away": 3.5},
            "handicap": {},
        },
    ]

    app = create_app(db_path=tmp_path / "worldcup.sqlite3")
    app.state.service = service
    client = TestClient(app)
    payload = client.post("/api/odds/sporttery/refresh").json()

    assert payload["mode"] == "live"
    assert payload["updated"] == 1
    assert payload["grouped_by_date"]["2026-07-01"][0]["match_num"] == "周三001"
    assert payload["unmatched_matches"][0]["home_team"] == "Unknown A"
    assert payload["last_updated"]


def test_update_after_results_accepts_external_sync_flags(tmp_path: Path):
    service = WorldCupService(db_path=tmp_path / "worldcup.sqlite3")
    service.sync_fifa_official_data = lambda date=None: {
        "source": "FIFA",
        "matches": [],
        "power_rankings": [],
        "warnings": [],
    }
    service.sync_footballdata_io = lambda date=None: {
        "source": "FootballData.io",
        "configured": True,
        "fixtures": [],
        "warnings": [],
    }
    service.refresh_sporttery_odds = lambda: {
        "source": "https://m.sporttery.cn/mjc/jsq/zqspf/",
        "mode": "live",
        "updated": 0,
        "grouped_by_date": {},
        "unmatched_matches": [],
    }

    result = service.update_after_results(
        fetch_online_results=False,
        use_xgboost=False,
        recalculate=False,
        output_dir=tmp_path / "outputs",
        date="2026-07-01",
        sync_fifa=True,
        sync_footballdata_io=True,
        sync_sporttery_odds=True,
    )

    assert result["external_data"]["fifa"]["source"] == "FIFA"
    assert result["external_data"]["footballdata_io"]["configured"] is True
    assert result["external_data"]["sporttery"]["mode"] == "live"


def test_backfill_historical_matches_keeps_predictions_without_fake_odds(tmp_path: Path):
    service = WorldCupService(db_path=tmp_path / "worldcup.sqlite3")
    for match_id, date, home, away, home_score, away_score in [
        ("group3-1", "2026-06-23", "France", "Senegal", 2, 0),
        ("before-window-1", "2026-06-28", "Brazil", "Germany", 1, 1),
    ]:
        service.db.upsert_lyihub_match(
            {
                "id": f"lyihub-{match_id}",
                "match_id": match_id,
                "date": date,
                "kickoff": f"{date}T20:00:00+08:00",
                "home_team": home,
                "away_team": away,
                "home_team_zh_source": home,
                "away_team_zh_source": away,
                "group": "小组赛 第3轮",
                "venue": "test",
                "status": "final",
                "home_score": home_score,
                "away_score": away_score,
                "has_predict": True,
                "payload": {},
            }
        )

    result = service.backfill_historical_matches(start_date="2026-06-23", end_date="2026-06-28")

    assert result["backfilled_match_count"] == 2
    assert len(service.db.list_finished_matches()) == 2
    prediction = service.db.get_prediction("lyihub-group3-1")
    assert prediction is not None
    assert prediction["odds_data_status"]["market_available"] is False
    assert prediction["fixture"]["historical_without_odds"] is True
    assert prediction["fixture"]["odds_available"] is False
    matches_payload = service.list_matches_with_prediction_summary("2026-06-23")[0]
    assert matches_payload["has_prediction_record"] is True
    assert matches_payload["historical_without_odds"] is True


def test_sporttery_history_attempt_records_empty_without_fake_odds(tmp_path: Path):
    service = WorldCupService(db_path=tmp_path / "worldcup.sqlite3")
    service.providers.sporttery_odds_provider.fetch_historical_odds = lambda start_date, end_date: []

    result = service.refresh_sporttery_odds(include_history=True, history_start="2026-06-23", history_end="2026-06-28")

    assert result["history"]["history_available"] is False
    assert result["history"]["updated"] == 0
    assert result["history"]["covered_dates"] == []


def test_train_over25_uses_only_90_minute_scores_and_updates_profiles(tmp_path: Path):
    service = WorldCupService(db_path=tmp_path / "worldcup.sqlite3")
    for match in [
        {
            "match_id": "m1",
            "date": "2026-06-23",
            "stage": "小组赛 第3轮",
            "home_team": "France",
            "away_team": "Senegal",
            "home_goals_90": 3,
            "away_goals_90": 0,
            "source": "test",
        },
        {
            "match_id": "m2",
            "date": "2026-06-24",
            "stage": "1/16决赛",
            "home_team": "France",
            "away_team": "Brazil",
            "home_goals_90": 1,
            "away_goals_90": 1,
            "home_goals_extra_time": 2,
            "away_goals_extra_time": 1,
            "winner": "France",
            "loser": "Brazil",
            "decided_by_extra_time": True,
            "source": "test",
        },
    ]:
        service.db.upsert_finished_match_result(match)

    report = service.train_over25_parameters(start_date="2026-06-23")
    profiles = {profile["team"]: profile for profile in service.db.list_team_profiles()}

    assert report["sample_count"] == 2
    assert report["over25_count"] == 1
    assert report["matches"][1]["total_goals_90"] == 2
    assert report["matches"][1]["over25"] is False
    for field in (
        "over25_attack_tendency",
        "over25_defense_tendency",
        "over25_match_tendency",
        "over25_recent_rate",
        "over25_adjusted_rating",
        "under25_stability",
    ):
        assert field in profiles["France"]


def test_prediction_payload_includes_over25_breakdown_and_no_value_without_totals_odds(tmp_path: Path):
    service = WorldCupService(db_path=tmp_path / "worldcup.sqlite3")
    service.db.upsert_fixture(
        {
            "id": "over25-fixture",
            "date": "2026-06-30",
            "kickoff": "2026-06-30T20:00:00+08:00",
            "home_team": "France",
            "away_team": "Senegal",
            "group": "1/16决赛",
            "venue": "test",
            "status": "scheduled",
            "home_elo": 1900,
            "away_elo": 1750,
        }
    )
    service.db.save_team_profile("France", {"team": "France", "elo": 1900, "over25_adjusted_rating": 0.72, "under25_stability": 0.28})
    service.db.save_team_profile("Senegal", {"team": "Senegal", "elo": 1750, "over25_adjusted_rating": 0.42, "under25_stability": 0.58})

    prediction = service.predict_fixture("over25-fixture", simulations=1000)

    assert 0 <= prediction["over25_prob"] <= 1
    assert prediction["under25_prob"] == 1 - prediction["over25_prob"]
    assert 0 <= prediction["xgboost_over25_prob"] <= 1
    assert 0 <= prediction["monte_carlo_over25_prob"] <= 1
    assert 0 <= prediction["score_heatmap_over25_prob"] <= 1
    assert 0 <= prediction["final_over25_prob"] <= 1
    assert prediction["over25_market_prob"] is None
    assert prediction["over25_edge"] is None
    assert prediction["over25_kelly"] is None
    assert prediction["over25_value"]["available"] is False
    assert "无赔率" in prediction["over25_value"]["reason"]


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


def test_market_payload_rejects_non_sporttery_market_source(tmp_path: Path):
    service = WorldCupService(db_path=tmp_path / "worldcup.sqlite3")
    payload = service._market_payload(
        {
            "home_team": "France",
            "away_team": "Senegal",
            "market_home": 1.82,
            "market_draw": 3.45,
            "market_away": 4.6,
            "market_source": "The Odds API",
        }
    )

    assert payload["available"] is False
    assert "中国体育彩票" in payload["reason"]
