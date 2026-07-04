import httpx

from worldcup_predictor.data.fifa_official import merge_field_sources
from worldcup_predictor.data.providers import SportmonksProvider


class DummyResponse:
    def __init__(self, payload, status_code=200, text=None, headers=None):
        self.payload = payload
        self.status_code = status_code
        self.text = text if text is not None else ""
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=httpx.Request("GET", "https://example.test"), response=httpx.Response(self.status_code))

    def json(self):
        return self.payload


def sportmonks_fixture_payload():
    return {
        "data": [
            {
                "id": 991,
                "name": "France vs Senegal",
                "starting_at": "2026-07-02 20:00:00",
                "state_id": 5,
                "state": {"name": "Finished"},
                "result_info": "France won after full-time.",
                "has_odds": True,
                "participants": [
                    {"id": 1, "name": "France", "meta": {"location": "home", "winner": True}},
                    {"id": 2, "name": "Senegal", "meta": {"location": "away", "winner": False}},
                ],
                "scores": [
                    {"participant_id": 1, "score": {"goals": 2}, "description": "CURRENT"},
                    {"participant_id": 2, "score": {"goals": 1}, "description": "CURRENT"},
                ],
                "lineups": [{"team_id": 1, "player_name": "Forward A", "position": "F", "type_id": 11}],
                "statistics": [{"participant_id": 1, "type": {"name": "Shots On Target"}, "data": {"value": 7}}],
                "odds": [{"market_id": 1, "label": "France", "value": "1.80"}],
                "standings": [{"participant_id": 1, "position": 1}],
                "sidelined": [{"participant_id": 2, "player_name": "Defender B", "type": "injury"}],
            }
        ],
        "pagination": {"has_more": False},
        "rate_limit": {"remaining": 1999},
    }


def test_sportmonks_provider_reads_api_key_and_normalizes_fixture(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return DummyResponse(sportmonks_fixture_payload(), headers={"x-ratelimit-remaining": "1999"})

    monkeypatch.setenv("SPORTMONKS_API_KEY", "sportmonks-secret")
    monkeypatch.setattr(httpx, "get", fake_get)

    provider = SportmonksProvider()
    fixtures = provider.fetch_fixtures("2026-07-02")

    assert calls[0]["url"] == "https://api.sportmonks.com/v3/football/fixtures/date/2026-07-02"
    assert calls[0]["headers"]["Authorization"] == "sportmonks-secret"
    assert "lineups" in calls[0]["params"]["include"]
    assert fixtures[0]["id"] == "sportmonks-991"
    assert fixtures[0]["source"] == "sportmonks"
    assert fixtures[0]["source_priority"] == 2
    assert fixtures[0]["source_id"] == "991"
    assert fixtures[0]["status"] == "final"
    assert fixtures[0]["home_team"] == "France"
    assert fixtures[0]["away_team"] == "Senegal"
    assert fixtures[0]["home_score"] == 2
    assert fixtures[0]["away_score"] == 1
    assert fixtures[0]["sportmonks_features"]["shots_on_target_home"] == 7
    assert fixtures[0]["sportmonks_features"]["lineup_count"] == 1
    assert fixtures[0]["sportmonks_features"]["sidelined_away"] == 1
    assert provider.last_rate_limit["remaining"] == 1999
    assert "sportmonks-secret" not in str(provider.validate())


def test_sportmonks_provider_degrades_forbidden_includes(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(kwargs["params"].get("include"))
        if len(calls) == 1:
            return DummyResponse({"message": "Forbidden include"}, status_code=403)
        return DummyResponse(sportmonks_fixture_payload())

    monkeypatch.setattr(httpx, "get", fake_get)

    provider = SportmonksProvider(token="legacy-token")
    fixtures = provider.fetch_fixtures("2026-07-02")

    assert fixtures
    assert provider.capability_warnings
    assert "odds" in calls[0]
    assert "odds" not in calls[1]


def test_sportmonks_conflict_loses_to_fifa():
    merged = merge_field_sources(
        [
            {"home_goals_90": 3, "source": "sportmonks", "source_priority": 2},
            {"home_goals_90": 2, "source": "FIFA", "source_priority": 1},
        ]
    )

    assert merged["fields"]["home_goals_90"]["value"] == 2
    assert merged["warnings"][0]["kept_source"] == "FIFA"
    assert merged["warnings"][0]["discarded_source"] == "sportmonks"
