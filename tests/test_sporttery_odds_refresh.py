import httpx

from worldcup_predictor.data.providers import SportteryOddsProvider
from worldcup_predictor.service import WorldCupService


class DummyResponse:
    def __init__(self, payload=None, status_code=200, text=None):
        self.payload = payload or {}
        self.status_code = status_code
        self.text = text if text is not None else ""

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=httpx.Request("GET", "https://example.test"), response=httpx.Response(self.status_code))

    def json(self):
        return self.payload


class DummyClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return self.responses.pop(0)


def sporttery_payload(rows):
    return {"value": {"matchInfoList": [{"subMatchList": rows}]}}


def test_sporttery_fetch_warms_mobile_page_then_gateway(monkeypatch):
    rows = [
        {
            "matchDate": "2026-07-02",
            "matchNumStr": "周四001",
            "leagueAllName": "世界杯",
            "homeTeamAllName": "法国",
            "awayTeamAllName": "塞内加尔",
            "had": {"h": "1.65", "d": "3.60", "a": "5.20"},
            "hhad": {"h": "3.10", "d": "3.35", "a": "1.92", "goalLine": "-1"},
        }
    ]
    client = DummyClient(
        [
            DummyResponse(text="<html>/gateway/uniform/football/getMatchCalculatorV1.qry</html>"),
            DummyResponse(sporttery_payload(rows), text='{"value":{}}'),
        ]
    )
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client)

    events = SportteryOddsProvider().fetch_odds()

    assert client.calls[0]["url"] == "https://m.sporttery.cn/mjc/jsq/zqspf/"
    assert "Mobile" in client.calls[0]["headers"]["User-Agent"]
    assert "poolCode" in client.calls[1]["params"]
    assert events[0]["match_num"] == "周四001"
    assert events[0]["h2h"]["home"] == 1.65
    assert events[0]["handicap"]["away"] == 1.92


def test_sporttery_fetch_returns_empty_for_empty_live_window(monkeypatch):
    client = DummyClient([DummyResponse(text="<html></html>"), DummyResponse(sporttery_payload([]), text='{"value":{}}')])
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client)

    assert SportteryOddsProvider().fetch_odds() == []


def test_sporttery_refresh_reports_empty_live_without_fake_odds(tmp_path):
    service = WorldCupService(db_path=tmp_path / "worldcup.sqlite3")
    service.providers.sporttery_odds_provider.fetch_odds = lambda: []

    result = service.refresh_sporttery_odds()

    assert result["mode"] == "empty_live"
    assert result["sample_count"] == 0
    assert result["updated"] == 0
    assert result["unmatched_matches"] == []


def test_sporttery_refresh_preserves_snapshot_on_blocked_request(tmp_path):
    service = WorldCupService(db_path=tmp_path / "worldcup.sqlite3")

    def blocked():
        raise ValueError("China Sporttery gateway blocked this request")

    service.providers.sporttery_odds_provider.fetch_odds = blocked
    result = service.refresh_sporttery_odds()

    assert result["mode"] == "snapshot_fallback"
    assert result["last_error"]
    assert result["updated"] > 0


def test_sporttery_unmatched_event_is_reported(tmp_path):
    service = WorldCupService(db_path=tmp_path / "worldcup.sqlite3")
    service.providers.sporttery_odds_provider.fetch_odds = lambda: [
        {
            "source": "China Sporttery",
            "date": "2026-07-02",
            "match_num": "周四009",
            "league": "世界杯",
            "home_team": "Unknown A",
            "away_team": "Unknown B",
            "h2h": {"home": 2.0, "draw": 3.0, "away": 3.5},
            "handicap": {},
        }
    ]

    result = service.refresh_sporttery_odds()

    assert result["mode"] == "live"
    assert result["updated"] == 0
    assert result["unmatched_matches"][0]["match_num"] == "周四009"


def test_sporttery_handicap_only_event_updates_fixture_and_logs_reason(tmp_path):
    service = WorldCupService(db_path=tmp_path / "worldcup.sqlite3")
    service.db.upsert_lyihub_match(
        {
            "id": "lyihub-argentina-cape-verde",
            "match_id": "54327947",
            "date": "2026-07-04",
            "kickoff": "2026-07-04T03:00:00+08:00",
            "home_team": "Argentina",
            "away_team": "Cape Verde",
            "home_team_zh": "阿根廷",
            "away_team_zh": "佛得角",
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
            "date": "2026-07-04",
            "match_num": "周五087",
            "league": "世界杯",
            "home_team": "阿根廷",
            "away_team": "佛得角",
            "h2h": {},
            "handicap": {"home": 2.2, "draw": 3.75, "away": 2.45},
            "handicap_line": "-2",
        }
    ]

    result = service.refresh_sporttery_odds()
    fixture = service.db.get_fixture("lyihub-argentina-cape-verde")

    assert result["updated"] == 1
    assert result["matched_count"] == 1
    assert result["match_results"][0]["status"] == "updated"
    assert result["match_results"][0]["reason"] == "handicap_only"
    assert fixture["market_home"] is None
    assert fixture["market_handicap_line"] == "-2"
    assert "周五087" in fixture["market_source"]
