from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from worldcup_predictor.api import create_app
from worldcup_predictor.data.rosters import ApiFootballRosterProvider
from worldcup_predictor.roster_strength import (
    aggregate_team_strength,
    league_tier_score,
    player_strength,
)
from worldcup_predictor.service import WorldCupService


class DummyResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=None)

    def json(self) -> dict:
        return self.payload


def test_api_football_roster_provider_parses_squad_coach_and_player_stats(monkeypatch):
    calls = []

    def fake_get(url, params=None, headers=None, timeout=20, follow_redirects=False):
        calls.append((url, params))
        if url.endswith("/teams"):
            return DummyResponse({"response": [{"team": {"id": 10, "name": "England"}}]})
        if url.endswith("/players/squads"):
            return DummyResponse(
                {
                    "response": [
                        {
                            "team": {"id": 10, "name": "England"},
                            "players": [
                                {
                                    "id": 129718,
                                    "name": "Jude Bellingham",
                                    "age": 22,
                                    "number": 10,
                                    "position": "Midfielder",
                                    "photo": "https://example.test/jude.png",
                                }
                            ],
                        }
                    ]
                }
            )
        if url.endswith("/coachs"):
            return DummyResponse(
                {
                    "response": [
                        {
                            "id": 1001,
                            "name": "T. Tuchel",
                            "firstname": "Thomas",
                            "lastname": "Tuchel",
                            "nationality": "Germany",
                            "career": [{"team": {"id": 10}, "start": "2025-01-01", "end": None}],
                        }
                    ]
                }
            )
        if url.endswith("/players") and params["season"] in (2026, 2025):
            return DummyResponse({"errors": {"plan": "Free plans do not have access to this season"}, "response": []})
        if url.endswith("/players") and params["season"] == 2024:
            return DummyResponse(
                {
                    "response": [
                        {
                            "player": {"id": 129718, "name": "Jude Bellingham"},
                            "statistics": [
                                {
                                    "team": {"id": 541, "name": "Real Madrid"},
                                    "league": {"id": 140, "name": "La Liga", "country": "Spain"},
                                    "games": {
                                        "position": "Midfielder",
                                        "appearences": 31,
                                        "lineups": 29,
                                        "minutes": 2500,
                                        "rating": "7.42",
                                    },
                                    "goals": {"total": 9, "assists": 8},
                                    "passes": {"total": 1450, "key": 52},
                                    "tackles": {"total": 41, "interceptions": 22},
                                }
                            ],
                        }
                    ]
                }
            )
        raise AssertionError((url, params))

    monkeypatch.setattr(httpx, "get", fake_get)

    provider = ApiFootballRosterProvider(key="secret-key")
    squad = provider.fetch_squad("England")
    stats = provider.fetch_player_statistics(129718)

    assert squad["team_id"] == 10
    assert squad["coach"]["name"] == "Thomas Tuchel"
    assert squad["players"][0]["name"] == "Jude Bellingham"
    assert stats["season"] == 2024
    assert stats["club"] == "Real Madrid"
    assert [params["season"] for url, params in calls if url.endswith("/players")] == [2026, 2025, 2024]
    assert "secret-key" not in str(squad)
    assert "secret-key" not in str(stats)


def test_player_strength_rewards_tier_one_regulars_over_lower_tier_substitutes():
    elite = player_strength(
        {
            "league": "Premier League",
            "club_rank": 2,
            "appearances": 34,
            "starts": 31,
            "minutes": 2850,
            "rating": 7.5,
            "position": "Attacker",
            "goals": 18,
            "assists": 8,
        }
    )
    reserve = player_strength(
        {
            "league": "Unknown League",
            "club_rank": None,
            "appearances": 9,
            "starts": 2,
            "minutes": 260,
            "rating": None,
            "position": "Attacker",
            "goals": 1,
            "assists": 0,
        }
    )

    assert league_tier_score("Premier League") > league_tier_score("Unknown League")
    assert elite > reserve


def test_team_strength_buckets_positions_and_reports_coverage():
    players = [
        {"position": "Attacker", "player_strength": 86, "stats_status": "complete"},
        {"position": "Midfielder", "player_strength": 78, "stats_status": "complete"},
        {"position": "Defender", "player_strength": 75, "stats_status": "complete"},
        {"position": "Goalkeeper", "player_strength": 72, "stats_status": "queued"},
    ]

    strength = aggregate_team_strength("England", players)

    assert strength["attack_strength"] == 86
    assert strength["midfield_control_strength"] == 78
    assert strength["defense_gk_strength"] == 73.5
    assert strength["coverage"] == 0.75
    assert strength["missing_player_stats"] == 1


def test_team_strength_regresses_uncompleted_players_to_neutral_not_low_score():
    strength = aggregate_team_strength(
        "England",
        [
            {"position": "Attacker", "stats_status": "queued"},
            {"position": "Attacker", "stats_status": "queued"},
        ],
    )

    assert strength["attack_strength"] == 65.0
    assert strength["coverage"] == 0.0


def test_service_syncs_squad_processes_queue_and_exposes_roster_endpoints(tmp_path: Path):
    service = WorldCupService(db_path=tmp_path / "worldcup.sqlite3")
    service.roster_provider.fetch_squad = lambda team: {
        "team": team,
        "team_id": 10,
        "source": "api-football",
        "coach": {"name": "Thomas Tuchel", "nationality": "Germany", "start": "2025-01-01"},
        "players": [
            {"player_id": 1, "name": "Elite Forward", "age": 26, "number": 9, "position": "Attacker"},
            {"player_id": 2, "name": "Control Mid", "age": 27, "number": 8, "position": "Midfielder"},
        ],
    }
    stats_by_id = {
        1: {
            "player_id": 1,
            "season": 2024,
            "club": "Arsenal",
            "league": "Premier League",
            "club_rank": 2,
            "position": "Attacker",
            "appearances": 35,
            "starts": 32,
            "minutes": 2800,
            "rating": 7.4,
            "goals": 18,
            "assists": 7,
        },
        2: {
            "player_id": 2,
            "season": 2024,
            "club": "Real Madrid",
            "league": "La Liga",
            "club_rank": 1,
            "position": "Midfielder",
            "appearances": 31,
            "starts": 26,
            "minutes": 2300,
            "rating": 7.2,
            "goals": 4,
            "assists": 8,
        },
    }
    service.roster_provider.fetch_player_statistics = lambda player_id: stats_by_id[player_id]

    synced = service.sync_squad("England")
    processed = service.process_roster_queue(limit=5)
    squad = service.get_team_squad("England")
    strength = service.get_team_strength("England")

    assert synced["players_count"] == 2
    assert processed["processed"] == 2
    assert squad["coach"]["name"] == "Thomas Tuchel"
    elite_forward = next(player for player in squad["players"] if player["name"] == "Elite Forward")
    assert elite_forward["club"] == "Arsenal"
    assert strength["attack_strength"] > 80

    app = create_app(db_path=tmp_path / "worldcup.sqlite3")
    app.state.service = service
    client = TestClient(app)

    assert client.get("/api/teams/England/squad").json()["coach"]["name"] == "Thomas Tuchel"
    assert client.get("/api/teams/England/strength").json()["coverage"] == 1.0
    health = client.get("/api/health/roster-data").json()
    assert health["queue_pending"] == 0
    assert "secret" not in str(health).lower()


def test_roster_weight_changes_prediction_when_strengths_are_available(tmp_path: Path):
    service = WorldCupService(db_path=tmp_path / "worldcup.sqlite3")
    service.db.upsert_fixture(
        {
            "id": "roster-fixture",
            "date": "2026-06-23",
            "kickoff": "2026-06-23T12:00:00Z",
            "home_team": "England",
            "away_team": "Ghana",
            "group": "Group L",
            "venue": "Test",
            "status": "scheduled",
            "home_elo": 1800,
            "away_elo": 1700,
        }
    )
    service.db.save_squad_strength(
        "England",
        {
            "team": "England",
            "attack_strength": 88,
            "midfield_control_strength": 86,
            "defense_gk_strength": 83,
            "coverage": 1.0,
            "missing_player_stats": 0,
            "model_version": "test",
        },
    )
    service.db.save_squad_strength(
        "Ghana",
        {
            "team": "Ghana",
            "attack_strength": 67,
            "midfield_control_strength": 66,
            "defense_gk_strength": 61,
            "coverage": 1.0,
            "missing_player_stats": 0,
            "model_version": "test",
        },
    )

    baseline = service.predict_fixture("roster-fixture", roster_weight=0.0)
    rostered = service.predict_fixture("roster-fixture", roster_weight=0.25)

    assert rostered["roster_weight"] == 0.25
    assert rostered["roster_strength"]["home"]["attack_strength"] == 88
    assert rostered["expected_goals"]["home"] > baseline["expected_goals"]["home"]
    assert rostered["model_inputs"]["roster_adjustment"]["home_xg_multiplier"] > 1.0
