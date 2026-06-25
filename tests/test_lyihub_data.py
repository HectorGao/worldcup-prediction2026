from pathlib import Path

from fastapi.testclient import TestClient

from worldcup_predictor.api import create_app
from worldcup_predictor.data.lyihub import LyihubWorldCupScraper, canonical_team
from worldcup_predictor.service import WorldCupService


INDEX_MATCH = {
    "match_id": "54329968",
    "kickoff_at": "2026-06-23T17:00:00+00:00",
    "stage": "小组赛 第2轮",
    "team_a": "葡萄牙",
    "team_b": "乌兹别克斯坦",
    "team_a_id": "50001540",
    "team_b_id": "50002027",
    "venue": "休斯顿体育场",
    "has_predict": True,
    "score_full": {"team_a": 5, "team_b": 0},
}


DETAIL = {
    "match": INDEX_MATCH,
    "players": {
        "p1": {
            "player_id": "50000356",
            "team_side": "A",
            "team_id": "50001540",
            "player_name": "C罗",
            "shirt_number": 7,
            "position": "attacker",
            "score10": {"评分": 9, "射门": 10},
            "fitness": {"starts_last_10": 9},
        },
        "p2": {
            "player_id": "50213371",
            "team_side": "B",
            "team_id": "50002027",
            "player_name": "肖穆罗多夫",
            "shirt_number": 14,
            "position": "attacker",
            "score10": {"评分": 7},
        },
        "p3": {
            "player_id": "50213372",
            "team_side": "A",
            "team_id": "50001540",
            "player_name": "分项球员",
            "shirt_number": 8,
            "position": "midfielder",
            "score10": {"射门": 7, "传球": 9, "防守": 8},
        },
        "p4": {
            "player_id": "50213373",
            "team_side": "A",
            "team_id": "50001540",
            "player_name": "缺失球员",
            "shirt_number": 9,
            "position": "midfielder",
            "score10": {},
        },
    },
    "llm_predict": [],
    "team_profiles": {},
}


def test_lyihub_normalizer_uses_beijing_date_and_canonical_teams():
    scraper = LyihubWorldCupScraper()

    match = scraper.normalize_index_match(INDEX_MATCH)

    assert match["id"] == "lyihub-54329968"
    assert match["date"] == "2026-06-24"
    assert match["home_team"] == "Portugal"
    assert match["away_team"] == "Uzbekistan"
    assert match["home_score"] == 5
    assert canonical_team("葡萄牙") == "Portugal"


def test_service_stores_lyihub_match_players_and_team_detail(tmp_path: Path):
    service = WorldCupService(db_path=tmp_path / "worldcup.sqlite3")
    scraper = LyihubWorldCupScraper()
    match = scraper.normalize_index_match(INDEX_MATCH)
    detail = scraper.normalize_detail(DETAIL)
    service.db.upsert_lyihub_match(match)
    service.db.upsert_web_fixture(match, source_name="lyihub_worldcup_static_json")
    service.db.save_lyihub_match_detail(detail)
    service.db.upsert_web_fixture(
        {
            "id": "web-stale",
            "date": "2026-06-24",
            "kickoff": "2026-06-24",
            "home_team": "England",
            "away_team": "Ghana",
            "group": "Group X",
            "venue": None,
            "status": "scheduled",
            "source_url": "https://example.test/stale",
        },
        source_name="stale_fallback",
    )

    team_detail = service.team_world_cup_detail("Portugal")
    day_matches = service.lyihub_matches(date="2026-06-24")
    preferred_matches = service.list_matches_with_prediction_summary("2026-06-24")

    assert day_matches["count"] == 1
    assert day_matches["matches"][0]["actual_score"] == "5-0"
    assert len(preferred_matches) == 1
    assert preferred_matches[0]["source_name"] == "lyihub_worldcup_static_json"
    assert team_detail["coverage"]["matches"] == 1
    assert team_detail["coverage"]["players"] == 3
    assert team_detail["coverage"]["players_with_estimated_ability"] == 1
    assert team_detail["players"][0]["player_name"] == "C罗"
    assert team_detail["players"][0]["ability"] == 9
    assert team_detail["players"][1]["ability"] == 8
    assert team_detail["players"][2]["ability"] == 8
    assert team_detail["players"][2]["ability_estimated"] is True


def test_lyihub_api_routes_return_rounds_and_coverage(tmp_path: Path):
    service = WorldCupService(db_path=tmp_path / "worldcup.sqlite3")
    scraper = LyihubWorldCupScraper()
    match = scraper.normalize_index_match(INDEX_MATCH)
    detail = scraper.normalize_detail(DETAIL)
    service.db.upsert_lyihub_match(match)
    service.db.upsert_web_fixture(match, source_name="lyihub_worldcup_static_json")
    service.db.save_lyihub_match_detail(detail)

    app = create_app(db_path=tmp_path / "worldcup.sqlite3")
    app.state.service = service
    client = TestClient(app)

    assert client.get("/api/lyihub/rounds").json()["rounds"][0]["stage"] == "小组赛 第2轮"
    assert client.get("/api/lyihub/coverage").json()["teams_found"] == 2
    assert client.get("/api/teams/Portugal/world-cup-detail").json()["players"][0]["ability"] == 9
