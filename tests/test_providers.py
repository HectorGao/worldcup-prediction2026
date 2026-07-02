import httpx

from worldcup_predictor.data.providers import (
    ApiFootballProvider,
    EspnScoreboardProvider,
    FootballDataIoProvider,
    FootballDataProvider,
    OddsApiProvider,
    ProviderRegistry,
)
from worldcup_predictor.data.fifa_official import FifaOfficialWorldCupCrawler, merge_field_sources


class DummyResponse:
    def __init__(self, payload: dict, status_code: int = 200, headers: dict | None = None):
        self.payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


def test_football_data_provider_normalizes_free_tier_world_cup_matches(monkeypatch):
    def fake_get(*args, **kwargs):
        return DummyResponse(
            {
                "matches": [
                    {
                        "id": 100,
                        "utcDate": "2026-06-23T19:00:00Z",
                        "status": "FINISHED",
                        "stage": "GROUP_STAGE",
                        "homeTeam": {"name": "United States"},
                        "awayTeam": {"name": "Mexico"},
                        "score": {"fullTime": {"home": 2, "away": 1}},
                    }
                ]
            }
        )

    monkeypatch.setattr(httpx, "get", fake_get)

    fixtures = FootballDataProvider(key="free-key").fetch_fixtures("2026-06-23")

    assert fixtures[0]["id"] == "football-data-100"
    assert fixtures[0]["status"] == "final"
    assert fixtures[0]["home_score"] == 2
    assert fixtures[0]["group"] == "Group Stage"


def test_footballdata_io_provider_uses_bearer_key_and_redacts_validation(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return DummyResponse(
            {
                "data": [
                    {
                        "id": "m-100",
                        "date": "2026-07-01",
                        "status": "completed",
                        "home_team": {"name": "Brazil"},
                        "away_team": {"name": "Japan"},
                        "score": {"home": 2, "away": 1},
                    }
                ],
                "meta": {"requests_used": 4, "requests_limit": 1000},
            }
        )

    monkeypatch.setattr(httpx, "get", fake_get)

    provider = FootballDataIoProvider(key="fd_secret_key")
    fixtures = provider.fetch_fixtures("2026-07-01")
    validation = provider.validate()

    assert calls[0]["url"] == "https://footballdata.io/api/v1/matches"
    assert calls[0]["headers"]["Authorization"] == "Bearer fd_secret_key"
    assert fixtures[0]["id"] == "footballdata-io-m-100"
    assert fixtures[0]["status"] == "final"
    assert fixtures[0]["source_priority"] == 2
    assert validation["quota_remaining"] == 996
    assert "fd_secret_key" not in str(validation)


def test_fifa_parser_and_field_merge_prefer_fifa_with_conflict_warning():
    crawler = FifaOfficialWorldCupCrawler()
    parsed = crawler.parse_embedded_payload(
        {
            "matches": [
                {
                    "id": "400",
                    "date": "2026-07-01",
                    "status": "final",
                    "homeTeam": {"name": "Brazil"},
                    "awayTeam": {"name": "Japan"},
                    "score": {"home": 2, "away": 1},
                    "stats": {"possessionHome": 58},
                    "lineup": {"home": ["A"], "away": ["B"]},
                }
            ],
            "powerRankings": [
                {"player": "Forward A", "team": "Brazil", "position": "Forward", "rating": 91}
            ],
        },
        source_url="https://www.fifa.com/example",
    )
    merged = merge_field_sources(
        [
            {
                "home_goals_90": 1,
                "home_team": "Brazil",
                "source": "ESPN",
                "source_priority": 3,
            },
            {
                "home_goals_90": 2,
                "home_team": "Brazil",
                "source": "FIFA",
                "source_priority": 1,
            },
        ]
    )

    assert parsed["matches"][0]["source"] == "FIFA"
    assert parsed["matches"][0]["match_stats"]["possessionHome"] == 58
    assert parsed["power_rankings"][0]["player_name"] == "Forward A"
    assert merged["fields"]["home_goals_90"]["value"] == 2
    assert merged["warnings"][0]["field"] == "home_goals_90"


def test_provider_registry_enriches_fixtures_with_sporttery_only():
    class FixtureSource:
        name = "fixture-source"

        def configured(self):
            return True

        def fetch_fixtures(self, date):
            return [
                {
                    "id": "fixture-1",
                    "date": date,
                    "kickoff": f"{date}T12:00:00Z",
                    "home_team": "France",
                    "away_team": "Senegal",
                    "group": "World Cup",
                    "venue": "test",
                    "status": "scheduled",
                    "home_elo": 1800,
                    "away_elo": 1700,
                    "market_home": None,
                    "market_draw": None,
                    "market_away": None,
                }
            ]

    class ForbiddenOdds:
        name = "forbidden"
        role = "forbidden"

        def configured(self):
            return True

        def enrich_fixtures(self, fixtures, date):
            raise AssertionError("non-sporttery odds provider must not be called")

    class SportteryOnly:
        name = "China Sporttery"
        role = "official_cn_odds_public_web_fallback"

        def configured(self):
            return True

        def enrich_fixtures(self, fixtures, date):
            enriched = dict(fixtures[0])
            enriched["market_home"] = 1.8
            enriched["market_draw"] = 3.4
            enriched["market_away"] = 4.8
            enriched["market_source"] = "China Sporttery live 周三001"
            return [enriched]

    registry = ProviderRegistry()
    registry.providers = [FixtureSource()]
    registry.odds_provider = ForbiddenOdds()
    registry.betfair_odds_provider = ForbiddenOdds()
    registry.sporttery_odds_provider = SportteryOnly()

    source, fixtures = registry.fetch_fixtures("2026-07-01")

    assert source == "fixture-source"
    assert fixtures[0]["market_source"].startswith("China Sporttery")


def test_espn_public_scoreboard_normalizes_no_key_events(monkeypatch):
    def fake_get(*args, **kwargs):
        return DummyResponse(
            {
                "events": [
                    {
                        "id": "401",
                        "date": "2026-06-23T22:00:00Z",
                        "season": {"slug": "fifa-world-cup"},
                        "status": {"type": {"completed": False}},
                        "competitions": [
                            {
                                "venue": {"fullName": "Seattle Stadium"},
                                "competitors": [
                                    {
                                        "homeAway": "home",
                                        "score": "0",
                                        "team": {"displayName": "Brazil"},
                                    },
                                    {
                                        "homeAway": "away",
                                        "score": "0",
                                        "team": {"displayName": "Japan"},
                                    },
                                ],
                            }
                        ],
                    }
                ]
            }
        )

    monkeypatch.setattr(httpx, "get", fake_get)

    fixtures = EspnScoreboardProvider().fetch_fixtures("2026-06-23")

    assert fixtures[0]["id"] == "espn-401"
    assert fixtures[0]["home_team"] == "Brazil"
    assert fixtures[0]["away_team"] == "Japan"
    assert fixtures[0]["venue"] == "Seattle Stadium"


def test_provider_registry_continues_when_public_provider_fails(monkeypatch):
    registry = ProviderRegistry()

    def broken_fetch(date):
        raise httpx.HTTPError("rate limited")

    registry.providers[0].configured = lambda: False
    registry.providers[1].configured = lambda: True
    registry.providers[1].fetch_fixtures = broken_fetch
    registry.providers[2].configured = lambda: False
    registry.providers[3].configured = lambda: False

    source, fixtures = registry.fetch_fixtures("2026-06-15")

    assert source == "sample_fallback"
    assert len(fixtures) == 4


def test_api_football_status_validation_sanitizes_key(monkeypatch):
    def fake_get(*args, **kwargs):
        return DummyResponse({"response": {"requests": {"current": 12, "limit_day": 100}}})

    monkeypatch.setattr(httpx, "get", fake_get)

    result = ApiFootballProvider(key="secret-api-football-key").validate()

    assert result["configured"] is True
    assert result["reachable"] is True
    assert result["auth_valid"] is True
    assert result["quota_remaining"] == 88
    assert "secret" not in str(result)


def test_odds_api_validation_discovers_world_cup_sport_without_leaking_key(monkeypatch):
    def fake_get(*args, **kwargs):
        return DummyResponse(
            [
                {"key": "soccer_fifa_world_cup", "title": "FIFA World Cup", "active": True},
                {"key": "basketball_nba", "title": "NBA", "active": True},
            ]
        )

    monkeypatch.setattr(httpx, "get", fake_get)

    result = OddsApiProvider(key="secret-odds-key").validate()

    assert result["configured"] is True
    assert result["reachable"] is True
    assert result["auth_valid"] is True
    assert result["sample_count"] == 1
    assert result["sport_keys"] == ["soccer_fifa_world_cup"]
    assert "secret" not in str(result)
