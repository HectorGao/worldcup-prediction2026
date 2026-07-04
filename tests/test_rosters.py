from pathlib import Path
import json
from statistics import mean

import httpx
from fastapi.testclient import TestClient

from worldcup_predictor.api import create_app
from worldcup_predictor.data.rosters import (
    API_FOOTBALL_LINEUPS_URL,
    ApiFootballLineupsPageScraper,
    ApiFootballRosterProvider,
    TheSportsDBRosterProvider,
)
from worldcup_predictor.roster_strength import (
    aggregate_team_strength,
    league_tier_score,
    player_strength,
)
from worldcup_predictor.service import WorldCupService
from worldcup_predictor.data.fifa_official import FifaOfficialWorldCupCrawler


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


def test_api_football_lineups_page_parser_extracts_position_groups():
    html = """
    <h2>Portugal squad list</h2>
    <p>Goalkeepers: Diogo Costa · José Sá</p>
    <p>Defenders: Rúben Dias · João Cancelo</p>
    <p>Midfielders: Bruno Fernandes · Bernardo Silva</p>
    <p>Forwards: Cristiano Ronaldo · Gonçalo Ramos</p>
    <h2>Uzbekistan squad list</h2>
    <p>Goalkeepers: Utkir Yusupov</p>
    <p>Defenders: Abdukodir Khusanov</p>
    <p>Midfielders: Abbosbek Fayzullaev</p>
    <p>Forwards: Eldor Shomurodov</p>
    """

    squad = ApiFootballLineupsPageScraper().parse_squad("Portugal", html)

    assert squad["source_url"] == API_FOOTBALL_LINEUPS_URL
    assert [player["name"] for player in squad["players"]] == [
        "Diogo Costa",
        "José Sá",
        "Rúben Dias",
        "João Cancelo",
        "Bruno Fernandes",
        "Bernardo Silva",
        "Cristiano Ronaldo",
        "Gonçalo Ramos",
    ]
    assert squad["players"][0]["position"] == "Goalkeeper"
    assert squad["players"][-1]["position"] == "Attacker"


def test_thesportsdb_provider_enriches_abbreviated_player_via_public_lineups(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=20, follow_redirects=False):
        if "searchplayers.php" in url:
            assert params["p"] == "Bukayo Saka"
            return DummyResponse(
                {
                    "player": [
                        {
                            "idPlayer": "34169884",
                            "idTeam": "133604",
                            "strPlayer": "Bukayo Saka",
                            "strTeam": "Arsenal",
                            "strNationality": "England",
                            "strPosition": "Right Winger",
                            "strThumb": "https://example.test/saka.png",
                            "relevance": "42.7",
                        }
                    ]
                }
            )
        if "lookupteam.php" in url:
            return DummyResponse({"teams": [{"idTeam": "133604", "strTeam": "Arsenal", "strLeague": "English Premier League"}]})
        raise AssertionError((url, params))

    monkeypatch.setattr(httpx, "get", fake_get)
    lineups = {
        "England": [
            {"name": "Bukayo Saka", "position": "Attacker"},
            {"name": "Harry Kane", "position": "Attacker"},
        ]
    }

    provider = TheSportsDBRosterProvider(lineup_aliases=lineups)
    stats = provider.enrich_player({"player_id": 1460, "name": "B. Saka", "position": "Attacker"}, team="England")

    assert stats["stats_status"] == "enriched"
    assert stats["name"] == "Bukayo Saka"
    assert stats["club"] == "Arsenal"
    assert stats["league"] == "English Premier League"
    assert stats["position"] == "Right Winger"
    assert stats["source"] == "thesportsdb"


def test_thesportsdb_provider_uses_verified_alias_when_lineup_fetch_is_unavailable(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=20, follow_redirects=False):
        if "searchplayers.php" in url:
            assert params["p"] == "Ollie Watkins"
            return DummyResponse(
                {
                    "player": [
                        {
                            "idPlayer": "34157367",
                            "idTeam": "133601",
                            "strPlayer": "Ollie Watkins",
                            "strTeam": "Aston Villa",
                            "strNationality": "England",
                            "strPosition": "Centre-Forward",
                        }
                    ]
                }
            )
        if "lookupteam.php" in url:
            return DummyResponse({"teams": [{"strLeague": "English Premier League"}]})
        raise AssertionError((url, params))

    monkeypatch.setattr(httpx, "get", fake_get)
    provider = TheSportsDBRosterProvider(lineup_aliases={"England": []})

    stats = provider.enrich_player({"player_id": 19366, "name": "O. Watkins", "position": "Midfielder"}, team="England")

    assert stats["stats_status"] == "enriched"
    assert stats["name"] == "Ollie Watkins"
    assert stats["club"] == "Aston Villa"


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


def test_team_strength_uses_fifa_power_rankings_and_marks_fallback_fields():
    players = [
        {
            "name": "Elite Forward",
            "position": "Forward",
            "fifa_power_rating": 91,
            "power_ranking_source": "FIFA power rankings",
            "stats_status": "complete",
        },
        {
            "name": "Control Mid",
            "position": "Midfielder",
            "fifa_power_rating": 84,
            "power_ranking_source": "FIFA power rankings",
            "stats_status": "complete",
        },
        {
            "name": "Backup Defender",
            "position": "Defender",
            "stats_status": "queued",
        },
    ]

    strength = aggregate_team_strength("Brazil", players)

    assert strength["attack_line_strength"] == 91
    assert strength["midfield_line_strength"] == 84
    assert strength["defense_line_strength"] != 70
    assert "defense_line_strength" in strength["fallback_fields"]
    assert strength["paper_strength_source"] == "FIFA power rankings"
    assert strength["starting_xi_strength"] > strength["bench_strength"]


def test_service_strength_fallback_uses_team_profile_not_default_seventy(tmp_path: Path):
    service = WorldCupService(db_path=tmp_path / "worldcup.sqlite3")
    service.db.save_team_profile(
        "Brazil",
        {
            "team": "Brazil",
            "attack_rating": 2.15,
            "midfield_rating": 83,
            "defensive_stability": 1.72,
            "defense_rating": 0.78,
            "world_cup_data_weight": 0.8,
        },
    )

    strength = service.get_team_strength("Brazil", allow_empty=True)

    assert strength["model_version"] == "world-cup-line-strength-v1"
    assert strength["paper_strength_source"] == "本届世界杯表现 fallback"
    assert strength["strength_baseline"] == "50 = 本届世界杯48队平均水平"
    assert {
        strength["attack_line_strength"],
        strength["midfield_line_strength"],
        strength["defense_line_strength"],
    } != {70.0}


def test_service_strength_uses_team_level_fifa_power_rankings_without_exact_player_match(tmp_path: Path):
    service = WorldCupService(db_path=tmp_path / "worldcup.sqlite3")
    for ranking in [
        {"team": "Brazil", "player_name": "FIFA Forward", "position": "Forward", "rating": 91, "source": "FIFA", "source_priority": 1},
        {"team": "Brazil", "player_name": "FIFA Mid", "position": "Midfielder", "rating": 84, "source": "FIFA", "source_priority": 1},
        {"team": "Brazil", "player_name": "FIFA Back", "position": "Defender", "rating": 82, "source": "FIFA", "source_priority": 1},
    ]:
        service.db.save_player_power_ranking(ranking)

    strength = service.get_team_strength("Brazil", allow_empty=True)

    assert strength["model_version"] == "world-cup-line-strength-v1"
    assert strength["paper_strength_source"] == "FIFA power rankings"
    assert strength["attack_line_strength_raw"] == 91
    assert strength["midfield_line_strength_raw"] == 84
    assert strength["defense_line_strength_raw"] == 82
    assert strength["strength_baseline"] == "50 = 本届世界杯48队平均水平"


def test_squad_strength_standardizes_48_team_pool_around_fifty(tmp_path: Path):
    service = WorldCupService(db_path=tmp_path / "worldcup.sqlite3")
    for index in range(48):
        team = f"Team {index:02d}"
        service.db.save_team_profile(
            team,
            {
                "team": team,
                "attack_rating": 0.75 + index * 0.035,
                "midfield_rating": 52 + index * 0.75,
                "defensive_stability": 0.75 + index * 0.025,
                "defense_rating": 1.5 - index * 0.015,
                "world_cup_data_weight": 0.8,
            },
        )

    summary = service.recompute_all_squad_strengths(force=True)
    strengths = [service.get_team_strength(f"Team {index:02d}", allow_empty=True) for index in range(48)]

    assert summary["standardization"]["team_count"] == 48
    for field in ("attack_line_strength", "midfield_line_strength", "defense_line_strength"):
        values = [strength[field] for strength in strengths]
        assert abs(mean(values) - 50) < 0.2
        assert values.count(70.0) < 3
    assert strengths[47]["squad_overall_strength"] > 50
    assert strengths[0]["squad_overall_strength"] < 50
    assert all(strength["strength_baseline"] == "50 = 本届世界杯48队平均水平" for strength in strengths)
    output_paths = service._write_squad_strength_outputs(tmp_path)
    payload = json.loads(Path(output_paths["squad_strengths_updated_json"]).read_text(encoding="utf-8"))
    assert payload["baseline_note"] == "50 = 本届世界杯48队平均水平"
    assert payload["standardization"]["team_count"] == 48


def test_fifa_parser_extracts_roster_lineup_and_substitutes():
    crawler = FifaOfficialWorldCupCrawler()
    parsed = crawler.parse_embedded_payload(
        {
            "matches": [
                {
                    "id": "fifa-1",
                    "date": "2026-06-23",
                    "status": "final",
                    "homeTeam": {"name": "Brazil"},
                    "awayTeam": {"name": "Germany"},
                    "score": {"home": 2, "away": 1},
                    "lineup": {
                        "home": {
                            "team": "Brazil",
                            "startingXI": [
                                {"id": 10, "name": "FIFA Forward", "position": "Forward", "rating": 91}
                            ],
                            "substitutes": [
                                {"id": 20, "name": "FIFA Bench", "position": "Midfielder", "rating": 78}
                            ],
                        },
                        "away": {
                            "team": "Germany",
                            "players": [
                                {"id": 30, "name": "FIFA Defender", "position": "Defender", "rating": 84}
                            ],
                        },
                    },
                }
            ]
        },
        source_url="https://www.fifa.com/match",
    )

    assert parsed["rosters"][0]["team"] == "Brazil"
    assert parsed["rosters"][0]["source"] == "FIFA"
    assert parsed["rosters"][0]["players"][0]["name"] == "FIFA Forward"
    assert parsed["rosters"][0]["players"][0]["lineup_role"] == "starting"
    assert parsed["rosters"][0]["players"][1]["lineup_role"] == "substitute"
    assert parsed["rosters"][1]["players"][0]["position"] == "Defender"


def test_fifa_roster_priority_prevents_lower_source_overwrite(tmp_path: Path):
    service = WorldCupService(db_path=tmp_path / "worldcup.sqlite3")
    service.db.save_team_squad(
        "Brazil",
        {
            "team": "Brazil",
            "source": "FIFA",
            "source_priority": 1,
            "players": [
                {
                    "player_id": "10",
                    "name": "FIFA Forward",
                    "position": "Forward",
                    "source": "FIFA",
                    "source_priority": 1,
                    "fifa_power_rating": 91,
                }
            ],
        },
    )
    service.db.save_team_squad(
        "Brazil",
        {
            "team": "Brazil",
            "source": "FootballData.io",
            "source_priority": 2,
            "players": [
                {
                    "player_id": "10",
                    "name": "Lower Source Name",
                    "position": "Defender",
                    "source": "FootballData.io",
                    "source_priority": 2,
                }
            ],
        },
    )

    squad = service.get_team_squad("Brazil")
    player = squad["players"][0]

    assert squad["source"] == "FIFA"
    assert player["name"] == "FIFA Forward"
    assert player["position"] == "Forward"
    assert player["source_name"] == "FIFA"
    assert player["source_priority"] == 1
    assert player["fifa_power_rating"] == 91


def test_service_recomputes_legacy_squad_strength_cache_missing_line_fields(tmp_path: Path):
    service = WorldCupService(db_path=tmp_path / "worldcup.sqlite3")
    service.db.save_team_squad(
        "Netherlands",
        {
            "team": "Netherlands",
            "source": "test",
            "players": [
                {"player_id": 1, "name": "Forward", "position": "Forward"},
                {"player_id": 2, "name": "Mid", "position": "Midfielder"},
                {"player_id": 3, "name": "Back", "position": "Defender"},
            ],
        },
    )
    service.db.save_squad_strength(
        "Netherlands",
        {
            "team": "Netherlands",
            "attack_strength": 70,
            "midfield_control_strength": 70,
            "defense_gk_strength": 70,
            "coverage": 0,
            "model_version": "roster-strength-v1",
        },
    )

    strength = service.get_team_strength("Netherlands", allow_empty=True)

    assert strength["model_version"] == "world-cup-line-strength-v1"
    assert "attack_line_strength" in strength


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


def test_team_strength_counts_public_enriched_players_as_usable_coverage():
    strength = aggregate_team_strength(
        "Portugal",
        [
            {
                "position": "Attacker",
                "stats_status": "enriched",
                "club": "Al-Nassr",
                "league": "Saudi Pro League",
                "player_strength": 68,
            },
            {"position": "Defender", "stats_status": "queued"},
        ],
    )

    assert strength["attack_strength"] == 68
    assert strength["coverage"] == 0.5
    assert strength["missing_player_stats"] == 1


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


def test_service_enriches_roster_queue_from_public_sources(tmp_path: Path):
    service = WorldCupService(db_path=tmp_path / "worldcup.sqlite3")
    service.db.save_team_squad(
        "England",
        {
            "team": "England",
            "team_id": 10,
            "source": "test",
            "players": [{"player_id": 1460, "name": "B. Saka", "position": "Attacker"}],
        },
    )
    service.public_roster_provider.enrich_player = lambda player, team: {
        "player_id": player["player_id"],
        "name": "Bukayo Saka",
        "season": 2026,
        "club": "Arsenal",
        "league": "English Premier League",
        "position": "Right Winger",
        "stats_status": "enriched",
        "source": "thesportsdb",
    }

    result = service.enrich_roster_queue_from_public(limit=5)
    squad = service.get_team_squad("England")
    strength = service.get_team_strength("England")

    assert result["enriched"] == 1
    assert squad["coverage"] == 1.0
    assert squad["players"][0]["club"] == "Arsenal"
    assert squad["players"][0]["stats_status"] == "enriched"
    assert strength["coverage"] == 1.0
    assert service.roster_data_health()["players_with_stats"] == 1


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
                "attack_strength": 66,
                "midfield_control_strength": 62,
                "defense_gk_strength": 61,
                "attack_line_strength": 66,
                "midfield_line_strength": 62,
                "defense_line_strength": 60,
                "goalkeeper_strength": 62,
                "starting_xi_strength": 63,
                "bench_strength": 57,
                "squad_overall_strength": 62,
                "coverage": 1.0,
                "missing_player_stats": 0,
                "paper_strength_source": "test normalized strength",
                "fallback_fields": [],
                "strength_baseline": "50 = 本届世界杯48队平均水平",
                "model_version": "world-cup-line-strength-v1",
            },
        )
    service.db.save_squad_strength(
        "Ghana",
            {
                "team": "Ghana",
                "attack_strength": 43,
                "midfield_control_strength": 45,
                "defense_gk_strength": 44,
                "attack_line_strength": 43,
                "midfield_line_strength": 45,
                "defense_line_strength": 44,
                "goalkeeper_strength": 44,
                "starting_xi_strength": 44,
                "bench_strength": 41,
                "squad_overall_strength": 43,
                "coverage": 1.0,
                "missing_player_stats": 0,
                "paper_strength_source": "test normalized strength",
                "fallback_fields": [],
                "strength_baseline": "50 = 本届世界杯48队平均水平",
                "model_version": "world-cup-line-strength-v1",
            },
        )

    baseline = service.predict_fixture("roster-fixture", roster_weight=0.0)
    rostered = service.predict_fixture("roster-fixture", roster_weight=0.25)

    assert rostered["roster_weight"] == 0.25
    assert rostered["roster_strength"]["home"]["attack_strength"] == 66
    assert rostered["roster_strength"]["home"]["strength_baseline"] == "50 = 本届世界杯48队平均水平"
    assert rostered["expected_goals"]["home"] > baseline["expected_goals"]["home"]
    assert rostered["model_inputs"]["roster_adjustment"]["home_xg_multiplier"] > 1.0
