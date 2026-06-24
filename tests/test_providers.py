import httpx

from worldcup_predictor.data.providers import (
    ApiFootballProvider,
    EspnScoreboardProvider,
    FootballDataProvider,
    OddsApiProvider,
    ProviderRegistry,
)


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
