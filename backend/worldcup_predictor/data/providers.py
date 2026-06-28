from __future__ import annotations

import os
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any, Protocol

import httpx
from dotenv import load_dotenv

from .sample import SAMPLE_FIXTURES

load_dotenv()


class FixtureProvider(Protocol):
    name: str

    def configured(self) -> bool:
        ...

    def fetch_fixtures(self, date: str) -> list[dict[str, Any]]:
        ...


def _checked_at() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validation_result(
    name: str,
    configured: bool,
    reachable: bool = False,
    auth_valid: bool = False,
    quota_remaining: int | None = None,
    sample_count: int = 0,
    last_error: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    payload = {
        "name": name,
        "configured": configured,
        "reachable": reachable,
        "auth_valid": auth_valid,
        "quota_remaining": quota_remaining,
        "sample_count": sample_count,
        "last_error": last_error,
        "checked_at": _checked_at(),
    }
    payload.update(extra)
    return payload


class SampleFixtureProvider:
    name = "sample_fallback"
    role = "local_fallback"

    def configured(self) -> bool:
        return True

    def fetch_fixtures(self, date: str) -> list[dict[str, Any]]:
        return [fixture for fixture in SAMPLE_FIXTURES if fixture["date"] == date]

    def validate(self) -> dict[str, Any]:
        return _validation_result(
            self.name,
            configured=True,
            reachable=True,
            auth_valid=True,
            sample_count=len(SAMPLE_FIXTURES),
        )


class SportmonksProvider:
    name = "Sportmonks"
    role = "premium_optional_no_free_quota"

    def __init__(self, token: str | None = None):
        self.token = token or os.getenv("SPORTMONKS_API_TOKEN")

    def configured(self) -> bool:
        return bool(self.token)

    def fetch_fixtures(self, date: str) -> list[dict[str, Any]]:
        if not self.configured():
            return []
        url = f"https://api.sportmonks.com/v3/football/fixtures/date/{date}"
        params = {"api_token": self.token, "include": "participants;scores;venue;state"}
        response = httpx.get(url, params=params, timeout=20)
        response.raise_for_status()
        payload = response.json()
        return [self._normalize(item, date) for item in payload.get("data", [])]

    def _normalize(self, item: dict[str, Any], date: str) -> dict[str, Any]:
        participants = item.get("participants") or []
        home = next((team for team in participants if team.get("meta", {}).get("location") == "home"), {})
        away = next((team for team in participants if team.get("meta", {}).get("location") == "away"), {})
        return {
            "id": f"sportmonks-{item.get('id')}",
            "date": date,
            "kickoff": item.get("starting_at") or f"{date}T00:00:00+08:00",
            "home_team": home.get("name") or "Home",
            "away_team": away.get("name") or "Away",
            "group": item.get("group", {}).get("name") if isinstance(item.get("group"), dict) else None,
            "venue": (item.get("venue") or {}).get("name"),
            "status": self._status(item),
            "home_score": None,
            "away_score": None,
            "home_elo": 1700,
            "away_elo": 1700,
            "market_home": None,
            "market_draw": None,
            "market_away": None,
        }

    def _status(self, item: dict[str, Any]) -> str:
        state = item.get("state")
        if isinstance(state, dict) and str(state.get("name", "")).lower() in {"finished", "ended"}:
            return "final"
        return "scheduled"

    def validate(self) -> dict[str, Any]:
        if not self.configured():
            return _validation_result(self.name, configured=False, last_error="SPORTMONKS_API_TOKEN not set")
        try:
            response = httpx.get(
                "https://api.sportmonks.com/v3/football/fixtures/date/2026-06-23",
                params={"api_token": self.token, "per_page": 1},
                timeout=20,
            )
            return _validation_result(
                self.name,
                configured=True,
                reachable=True,
                auth_valid=response.status_code < 400,
                sample_count=len(response.json().get("data", [])) if response.status_code < 400 else 0,
                last_error=None if response.status_code < 400 else f"HTTP {response.status_code}",
            )
        except httpx.HTTPError as exc:
            return _validation_result(self.name, configured=True, last_error=str(exc))


class ApiFootballProvider:
    name = "API-Football"
    role = "primary_free_tier"

    def __init__(self, key: str | None = None):
        self.key = key or os.getenv("API_FOOTBALL_KEY")

    def configured(self) -> bool:
        return bool(self.key)

    def fetch_fixtures(self, date: str) -> list[dict[str, Any]]:
        if not self.configured():
            return []
        response = httpx.get(
            "https://v3.football.api-sports.io/fixtures",
            params={"date": date},
            headers={"x-apisports-key": self.key},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        return [self._normalize(item, date) for item in payload.get("response", [])]

    def _normalize(self, item: dict[str, Any], date: str) -> dict[str, Any]:
        fixture = item.get("fixture") or {}
        teams = item.get("teams") or {}
        goals = item.get("goals") or {}
        status = (fixture.get("status") or {}).get("short")
        return {
            "id": f"api-football-{fixture.get('id')}",
            "date": date,
            "kickoff": fixture.get("date") or f"{date}T00:00:00+08:00",
            "home_team": (teams.get("home") or {}).get("name") or "Home",
            "away_team": (teams.get("away") or {}).get("name") or "Away",
            "group": (item.get("league") or {}).get("round"),
            "venue": (fixture.get("venue") or {}).get("name"),
            "status": "final" if status == "FT" else "scheduled",
            "home_score": goals.get("home"),
            "away_score": goals.get("away"),
            "home_elo": 1700,
            "away_elo": 1700,
            "market_home": None,
            "market_draw": None,
            "market_away": None,
        }

    def validate(self) -> dict[str, Any]:
        if not self.configured():
            return _validation_result(self.name, configured=False, last_error="API_FOOTBALL_KEY not set")
        try:
            response = httpx.get(
                "https://v3.football.api-sports.io/status",
                headers={"x-apisports-key": self.key},
                timeout=20,
            )
            reachable = response.status_code < 500
            auth_valid = response.status_code < 400
            payload = response.json() if auth_valid else {}
            requests = (payload.get("response") or {}).get("requests") or {}
            current = requests.get("current")
            limit_day = requests.get("limit_day")
            quota_remaining = None
            if isinstance(current, int) and isinstance(limit_day, int):
                quota_remaining = max(0, limit_day - current)
            return _validation_result(
                self.name,
                configured=True,
                reachable=reachable,
                auth_valid=auth_valid,
                quota_remaining=quota_remaining,
                sample_count=1 if auth_valid else 0,
                last_error=None if auth_valid else f"HTTP {response.status_code}",
            )
        except (httpx.HTTPError, ValueError) as exc:
            return _validation_result(self.name, configured=True, last_error=str(exc))


class FootballDataProvider:
    name = "football-data.org"
    role = "free_tier_fixture_scores"

    def __init__(self, key: str | None = None):
        self.key = key or os.getenv("FOOTBALL_DATA_API_KEY")

    def configured(self) -> bool:
        return bool(self.key)

    def fetch_fixtures(self, date: str) -> list[dict[str, Any]]:
        if not self.configured():
            return []
        response = httpx.get(
            "https://api.football-data.org/v4/competitions/WC/matches",
            params={"dateFrom": date, "dateTo": date},
            headers={"X-Auth-Token": self.key},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        return [self._normalize(item, date) for item in payload.get("matches", [])]

    def _normalize(self, item: dict[str, Any], date: str) -> dict[str, Any]:
        home = item.get("homeTeam") or {}
        away = item.get("awayTeam") or {}
        score = item.get("score") or {}
        full_time = score.get("fullTime") or {}
        status = str(item.get("status") or "").upper()
        return {
            "id": f"football-data-{item.get('id')}",
            "date": date,
            "kickoff": item.get("utcDate") or f"{date}T00:00:00Z",
            "home_team": home.get("name") or home.get("shortName") or "Home",
            "away_team": away.get("name") or away.get("shortName") or "Away",
            "group": (item.get("stage") or "").replace("_", " ").title() or None,
            "venue": None,
            "status": "final" if status == "FINISHED" else "scheduled",
            "home_score": full_time.get("home"),
            "away_score": full_time.get("away"),
            "home_elo": 1700,
            "away_elo": 1700,
            "market_home": None,
            "market_draw": None,
            "market_away": None,
        }

    def validate(self) -> dict[str, Any]:
        if not self.configured():
            return _validation_result(
                self.name,
                configured=False,
                last_error="FOOTBALL_DATA_API_KEY not set",
            )
        try:
            response = httpx.get(
                "https://api.football-data.org/v4/competitions/WC/matches",
                params={"dateFrom": "2026-06-23", "dateTo": "2026-06-23"},
                headers={"X-Auth-Token": self.key},
                timeout=20,
            )
            auth_valid = response.status_code < 400
            return _validation_result(
                self.name,
                configured=True,
                reachable=response.status_code < 500,
                auth_valid=auth_valid,
                sample_count=len(response.json().get("matches", [])) if auth_valid else 0,
                last_error=None if auth_valid else f"HTTP {response.status_code}",
            )
        except (httpx.HTTPError, ValueError) as exc:
            return _validation_result(self.name, configured=True, last_error=str(exc))


class EspnScoreboardProvider:
    name = "ESPN public scoreboard"
    role = "free_no_key_live_scoreboard_experimental"

    def configured(self) -> bool:
        return True

    def fetch_fixtures(self, date: str) -> list[dict[str, Any]]:
        response = httpx.get(
            "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard",
            params={"dates": date.replace("-", "")},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        fixtures = []
        for event in payload.get("events", []):
            fixture = self._normalize(event, date)
            if fixture:
                fixtures.append(fixture)
        return fixtures

    def _normalize(self, event: dict[str, Any], date: str) -> dict[str, Any] | None:
        competition = (event.get("competitions") or [{}])[0]
        competitors = competition.get("competitors") or []
        home = next((team for team in competitors if team.get("homeAway") == "home"), None)
        away = next((team for team in competitors if team.get("homeAway") == "away"), None)
        if not home or not away:
            return None
        status = event.get("status", {}).get("type", {})
        return {
            "id": f"espn-{event.get('id')}",
            "date": date,
            "kickoff": event.get("date") or f"{date}T00:00:00Z",
            "home_team": (home.get("team") or {}).get("displayName") or "Home",
            "away_team": (away.get("team") or {}).get("displayName") or "Away",
            "group": (event.get("season") or {}).get("slug"),
            "venue": (competition.get("venue") or {}).get("fullName"),
            "status": "final" if status.get("completed") else "scheduled",
            "home_score": self._score(home),
            "away_score": self._score(away),
            "home_elo": 1700,
            "away_elo": 1700,
            "market_home": None,
            "market_draw": None,
            "market_away": None,
        }

    def _score(self, competitor: dict[str, Any]) -> int | None:
        score = competitor.get("score")
        return int(score) if str(score).isdigit() else None

    def validate(self) -> dict[str, Any]:
        try:
            fixtures = self.fetch_fixtures("2026-06-23")
            return _validation_result(
                self.name,
                configured=True,
                reachable=True,
                auth_valid=True,
                sample_count=len(fixtures),
            )
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            return _validation_result(self.name, configured=True, last_error=str(exc))


class OddsApiProvider:
    name = "The Odds API"
    role = "odds_paid"

    def __init__(self, key: str | None = None):
        self.key = key or os.getenv("ODDS_API_KEY")

    def configured(self) -> bool:
        return bool(self.key)

    def validate(self) -> dict[str, Any]:
        if not self.configured():
            return _validation_result(self.name, configured=False, last_error="ODDS_API_KEY not set")
        try:
            response = httpx.get(
                "https://api.the-odds-api.com/v4/sports",
                params={"apiKey": self.key},
                timeout=20,
            )
            auth_valid = response.status_code < 400
            sports = response.json() if auth_valid else []
            sport_keys = [
                sport.get("key")
                for sport in sports
                if "soccer" in str(sport.get("key", "")).lower()
                and (
                    "world" in str(sport.get("key", "")).lower()
                    or "cup" in str(sport.get("title", "")).lower()
                    or "fifa" in str(sport.get("title", "")).lower()
                )
            ]
            remaining = response.headers.get("x-requests-remaining")
            return _validation_result(
                self.name,
                configured=True,
                reachable=response.status_code < 500,
                auth_valid=auth_valid,
                quota_remaining=int(remaining) if remaining and remaining.isdigit() else None,
                sample_count=len(sport_keys),
                last_error=None if auth_valid else f"HTTP {response.status_code}",
                sport_keys=sport_keys,
            )
        except (httpx.HTTPError, ValueError) as exc:
            return _validation_result(self.name, configured=True, last_error=str(exc))

    def fetch_market_probabilities(self) -> dict[str, dict[str, Any]]:
        return {}

    def discover_world_cup_sports(self) -> list[str]:
        if not self.configured():
            return []
        response = httpx.get(
            "https://api.the-odds-api.com/v4/sports",
            params={"apiKey": self.key},
            timeout=20,
        )
        response.raise_for_status()
        sports = response.json()
        return [
            sport.get("key")
            for sport in sports
            if "soccer" in str(sport.get("key", "")).lower()
            and (
                "world" in str(sport.get("key", "")).lower()
                or "cup" in str(sport.get("title", "")).lower()
                or "fifa" in str(sport.get("title", "")).lower()
            )
        ]

    def fetch_odds(self, date: str) -> list[dict[str, Any]]:
        if not self.configured():
            return []
        odds: list[dict[str, Any]] = []
        for sport_key in self.discover_world_cup_sports()[:2]:
            response = httpx.get(
                f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds",
                params={
                    "apiKey": self.key,
                    "regions": "us,eu",
                    "markets": "h2h,totals",
                    "oddsFormat": "decimal",
                    "dateFormat": "iso",
                },
                timeout=20,
            )
            if response.status_code >= 400:
                continue
            for event in response.json():
                if str(event.get("commence_time", ""))[:10] == date:
                    odds.append(event)
        return odds

    def enrich_fixtures(self, fixtures: list[dict[str, Any]], date: str) -> list[dict[str, Any]]:
        try:
            odds_events = self.fetch_odds(date)
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return fixtures
        if not odds_events:
            return fixtures
        return [self._enrich_fixture(fixture, odds_events) for fixture in fixtures]

    def _enrich_fixture(
        self,
        fixture: dict[str, Any],
        odds_events: list[dict[str, Any]],
    ) -> dict[str, Any]:
        best_event = max(
            odds_events,
            key=lambda event: self._match_score(
                fixture["home_team"],
                fixture["away_team"],
                event.get("home_team", ""),
                event.get("away_team", ""),
            ),
            default=None,
        )
        if not best_event:
            return fixture
        enriched = dict(fixture)
        for bookmaker in best_event.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                if market.get("key") == "h2h":
                    self._apply_h2h(enriched, market.get("outcomes", []))
                elif market.get("key") == "totals":
                    self._apply_totals(enriched, market.get("outcomes", []))
            if enriched.get("market_home") and enriched.get("market_over_2_5"):
                break
        return enriched

    def _apply_h2h(self, fixture: dict[str, Any], outcomes: list[dict[str, Any]]) -> None:
        for outcome in outcomes:
            name = str(outcome.get("name", ""))
            price = outcome.get("price")
            if not price:
                continue
            if name.lower() == "draw":
                fixture["market_draw"] = price
            elif self._name_similarity(name, fixture["home_team"]) >= 0.55:
                fixture["market_home"] = price
            elif self._name_similarity(name, fixture["away_team"]) >= 0.55:
                fixture["market_away"] = price

    def _apply_totals(self, fixture: dict[str, Any], outcomes: list[dict[str, Any]]) -> None:
        for outcome in outcomes:
            if float(outcome.get("point") or 0) != 2.5:
                continue
            name = str(outcome.get("name", "")).lower()
            if name == "over":
                fixture["market_over_2_5"] = outcome.get("price")
            elif name == "under":
                fixture["market_under_2_5"] = outcome.get("price")

    def _match_score(self, home: str, away: str, event_home: str, event_away: str) -> float:
        direct = self._name_similarity(home, event_home) + self._name_similarity(away, event_away)
        swapped = self._name_similarity(home, event_away) + self._name_similarity(away, event_home)
        return max(direct, swapped)

    def _name_similarity(self, left: str, right: str) -> float:
        return SequenceMatcher(None, left.lower(), right.lower()).ratio()


class SportteryOddsProvider:
    name = "China Sporttery"
    role = "official_cn_odds_public_web_fallback"
    endpoint = "https://webapi.sporttery.cn/gateway/jc/football/getFixedBonusV1.qry"

    def configured(self) -> bool:
        return os.getenv("SPORTTERY_ENABLE_LIVE", "0") == "1"

    def validate(self) -> dict[str, Any]:
        if not self.configured():
            return _validation_result(
                self.name,
                configured=False,
                last_error="Set SPORTTERY_ENABLE_LIVE=1 to attempt live web scrape; current environment may be WAF-blocked.",
            )
        try:
            events = self.fetch_odds()
            return _validation_result(
                self.name,
                configured=True,
                reachable=True,
                auth_valid=True,
                sample_count=len(events),
            )
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            return _validation_result(self.name, configured=True, last_error=str(exc))

    def fetch_odds(self) -> list[dict[str, Any]]:
        response = httpx.get(
            self.endpoint,
            params={"clientCode": "3001"},
            headers={
                "Referer": "https://www.sporttery.cn/",
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json,text/plain,*/*",
            },
            timeout=12,
        )
        response.raise_for_status()
        text = response.text.strip()
        if "WAF" in text or "禁止访问" in text or text.startswith("<"):
            raise ValueError("China Sporttery gateway blocked this request or returned non-JSON HTML")
        return self.parse_events(response.json())

    def parse_events(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        candidates = self._find_event_lists(payload)
        events: list[dict[str, Any]] = []
        for candidate in candidates:
            for item in candidate:
                normalized = self._normalize_event(item)
                if normalized:
                    events.append(normalized)
        return events

    def enrich_fixtures(self, fixtures: list[dict[str, Any]], date: str) -> list[dict[str, Any]]:
        if not self.configured():
            return fixtures
        try:
            odds_events = [
                event for event in self.fetch_odds()
                if not event.get("date") or str(event.get("date")) == date
            ]
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return fixtures
        if not odds_events:
            return fixtures
        return [self._enrich_fixture(fixture, odds_events) for fixture in fixtures]

    def _normalize_event(self, item: dict[str, Any]) -> dict[str, Any] | None:
        home = first_present(item, ["homeTeamAbbName", "homeTeamName", "homeName", "home_team"])
        away = first_present(item, ["awayTeamAbbName", "awayTeamName", "awayName", "away_team"])
        if not home or not away:
            return None
        h2h = item.get("had") or item.get("spf") or {}
        handicap = item.get("hhad") or item.get("rqspf") or {}
        totals = item.get("ttg") or item.get("goals") or {}
        return {
            "source": self.name,
            "date": first_present(item, ["matchDate", "businessDate", "date"]),
            "match_num": first_present(item, ["matchNumStr", "matchNum", "matchId"]),
            "home_team": home,
            "away_team": away,
            "h2h": normalize_three_way_odds(h2h, home_key="h", draw_key="d", away_key="a"),
            "handicap": normalize_three_way_odds(handicap, home_key="h", draw_key="d", away_key="a"),
            "handicap_line": first_present(handicap, ["fixedodds", "goalLine", "line"]),
            "totals": normalize_two_way_odds(totals),
        }

    def _enrich_fixture(self, fixture: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
        best_event = max(
            events,
            key=lambda event: self._match_score(
                fixture["home_team"],
                fixture["away_team"],
                event.get("home_team", ""),
                event.get("away_team", ""),
            ),
            default=None,
        )
        if not best_event or self._match_score(fixture["home_team"], fixture["away_team"], best_event["home_team"], best_event["away_team"]) < 1.1:
            return fixture
        enriched = dict(fixture)
        h2h = best_event.get("h2h") or {}
        enriched["market_home"] = h2h.get("home") or enriched.get("market_home")
        enriched["market_draw"] = h2h.get("draw") or enriched.get("market_draw")
        enriched["market_away"] = h2h.get("away") or enriched.get("market_away")
        totals = best_event.get("totals") or {}
        enriched["market_over_2_5"] = totals.get("over") or enriched.get("market_over_2_5")
        enriched["market_under_2_5"] = totals.get("under") or enriched.get("market_under_2_5")
        enriched["market_source"] = self.name
        enriched["market_handicap"] = best_event.get("handicap")
        enriched["market_handicap_line"] = best_event.get("handicap_line")
        return enriched

    def _match_score(self, home: str, away: str, event_home: str, event_away: str) -> float:
        direct = self._name_similarity(home, event_home) + self._name_similarity(away, event_away)
        swapped = self._name_similarity(home, event_away) + self._name_similarity(away, event_home)
        return max(direct, swapped)

    def _name_similarity(self, left: str, right: str) -> float:
        return SequenceMatcher(None, str(left).lower(), str(right).lower()).ratio()

    def _find_event_lists(self, node: Any) -> list[list[dict[str, Any]]]:
        lists: list[list[dict[str, Any]]] = []
        if isinstance(node, list) and node and all(isinstance(item, dict) for item in node):
            if any("home" in " ".join(item.keys()).lower() or "team" in " ".join(item.keys()).lower() for item in node[:3]):
                lists.append(node)
        elif isinstance(node, dict):
            for value in node.values():
                lists.extend(self._find_event_lists(value))
        return lists


class BetfairOddsProvider:
    name = "Betfair Exchange"
    role = "odds_optional_exchange"
    endpoint = "https://api.betfair.com/exchange/betting/json-rpc/v1"

    def __init__(self, app_key: str | None = None, session_token: str | None = None):
        self.app_key = app_key or os.getenv("BETFAIR_APP_KEY")
        self.session_token = session_token or os.getenv("BETFAIR_SESSION_TOKEN")

    def configured(self) -> bool:
        return bool(self.app_key and self.session_token)

    def validate(self) -> dict[str, Any]:
        if not self.configured():
            return _validation_result(
                self.name,
                configured=False,
                last_error="BETFAIR_APP_KEY and BETFAIR_SESSION_TOKEN not set",
                docs="https://docs.developer.betfair.com/",
            )
        try:
            markets = self.fetch_catalogue("2026-06-23", max_results=1)
            return _validation_result(
                self.name,
                configured=True,
                reachable=True,
                auth_valid=True,
                sample_count=len(markets),
                docs="https://docs.developer.betfair.com/",
            )
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            return _validation_result(
                self.name,
                configured=True,
                last_error=str(exc),
                docs="https://docs.developer.betfair.com/",
            )

    def enrich_fixtures(self, fixtures: list[dict[str, Any]], date: str) -> list[dict[str, Any]]:
        if not self.configured():
            return fixtures
        try:
            catalogue = self.fetch_catalogue(date)
            books = self.fetch_books([market["marketId"] for market in catalogue])
            events = self.parse_markets(catalogue, books)
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return fixtures
        if not events:
            return fixtures
        return [self._enrich_fixture(fixture, events) for fixture in fixtures]

    def fetch_catalogue(self, date: str, max_results: int = 200) -> list[dict[str, Any]]:
        params = {
            "filter": {
                "eventTypeIds": ["1"],
                "marketStartTime": {
                    "from": f"{date}T00:00:00Z",
                    "to": f"{date}T23:59:59Z",
                },
                "marketTypeCodes": ["MATCH_ODDS", "ASIAN_HANDICAP", "OVER_UNDER_25"],
            },
            "marketProjection": ["EVENT", "RUNNER_DESCRIPTION", "MARKET_DESCRIPTION", "MARKET_START_TIME"],
            "sort": "FIRST_TO_START",
            "maxResults": str(max_results),
        }
        return self._rpc("SportsAPING/v1.0/listMarketCatalogue", params)

    def fetch_books(self, market_ids: list[str]) -> list[dict[str, Any]]:
        if not market_ids:
            return []
        params = {
            "marketIds": market_ids,
            "priceProjection": {"priceData": ["EX_BEST_OFFERS"]},
        }
        return self._rpc("SportsAPING/v1.0/listMarketBook", params)

    def parse_markets(
        self,
        catalogue: list[dict[str, Any]],
        books: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        book_by_id = {book.get("marketId"): book for book in books}
        events: dict[str, dict[str, Any]] = {}
        for market in catalogue:
            event = market.get("event") or {}
            event_name = event.get("name") or ""
            home, away = split_event_name(event_name)
            if not home or not away:
                continue
            item = events.setdefault(
                str(event.get("id") or event_name),
                {
                    "source": self.name,
                    "date": str(market.get("marketStartTime") or "")[:10],
                    "event_name": event_name,
                    "home_team": home,
                    "away_team": away,
                },
            )
            market_type = ((market.get("description") or {}).get("marketType") or "").upper()
            odds = self._runner_odds(market, book_by_id.get(market.get("marketId")) or {})
            if market_type == "MATCH_ODDS":
                item["h2h"] = {
                    "home": odds.get(home),
                    "draw": odds.get("The Draw") or odds.get("Draw"),
                    "away": odds.get(away),
                }
            elif market_type == "ASIAN_HANDICAP":
                item["handicap"] = compact_odds(
                    {
                        "home": first_by_similarity(odds, home),
                        "away": first_by_similarity(odds, away),
                    }
                )
            elif market_type == "OVER_UNDER_25":
                item["totals"] = compact_odds(
                    {
                        "over": odds.get("Over 2.5 Goals") or odds.get("Over 2.5"),
                        "under": odds.get("Under 2.5 Goals") or odds.get("Under 2.5"),
                    }
                )
        return list(events.values())

    def _runner_odds(self, market: dict[str, Any], book: dict[str, Any]) -> dict[str, float]:
        names = {
            runner.get("selectionId"): runner.get("runnerName")
            for runner in market.get("runners", [])
        }
        odds = {}
        for runner in book.get("runners", []):
            offers = ((runner.get("ex") or {}).get("availableToBack") or [])
            if not offers:
                continue
            name = names.get(runner.get("selectionId"))
            if name:
                odds[str(name)] = float(offers[0].get("price"))
        return odds

    def _rpc(self, method: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        response = httpx.post(
            self.endpoint,
            json={"jsonrpc": "2.0", "method": method, "params": params, "id": 1},
            headers={
                "X-Application": str(self.app_key),
                "X-Authentication": str(self.session_token),
                "Content-Type": "application/json",
            },
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("error"):
            raise ValueError(str(payload["error"].get("message") or payload["error"]))
        result = payload.get("result") or []
        if not isinstance(result, list):
            raise ValueError("Betfair response did not contain a list result")
        return result

    def _enrich_fixture(self, fixture: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
        best_event = max(
            events,
            key=lambda event: name_pair_score(
                fixture["home_team"],
                fixture["away_team"],
                event.get("home_team", ""),
                event.get("away_team", ""),
            ),
            default=None,
        )
        if not best_event or name_pair_score(fixture["home_team"], fixture["away_team"], best_event["home_team"], best_event["away_team"]) < 1.1:
            return fixture
        enriched = dict(fixture)
        h2h = compact_odds(best_event.get("h2h") or {})
        enriched["market_home"] = h2h.get("home") or enriched.get("market_home")
        enriched["market_draw"] = h2h.get("draw") or enriched.get("market_draw")
        enriched["market_away"] = h2h.get("away") or enriched.get("market_away")
        totals = compact_odds(best_event.get("totals") or {})
        enriched["market_over_2_5"] = totals.get("over") or enriched.get("market_over_2_5")
        enriched["market_under_2_5"] = totals.get("under") or enriched.get("market_under_2_5")
        handicap = compact_odds(best_event.get("handicap") or {})
        if handicap:
            enriched["market_handicap"] = handicap
        enriched["market_source"] = self.name
        return enriched


class ProviderRegistry:
    def __init__(self):
        self.providers: list[FixtureProvider] = [
            ApiFootballProvider(),
            FootballDataProvider(),
            EspnScoreboardProvider(),
            SportmonksProvider(),
            SampleFixtureProvider(),
        ]
        self.odds_provider = OddsApiProvider()
        self.betfair_odds_provider = BetfairOddsProvider()
        self.sporttery_odds_provider = SportteryOddsProvider()

    def fetch_fixtures(self, date: str) -> tuple[str, list[dict[str, Any]]]:
        for provider in self.providers:
            if not provider.configured():
                continue
            try:
                fixtures = provider.fetch_fixtures(date)
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                continue
            if fixtures:
                fixtures = self.odds_provider.enrich_fixtures(fixtures, date)
                fixtures = self.betfair_odds_provider.enrich_fixtures(fixtures, date)
                fixtures = self.sporttery_odds_provider.enrich_fixtures(fixtures, date)
                return provider.name, fixtures
        return "none", []

    def health(self) -> list[dict[str, Any]]:
        providers = []
        for provider in self.providers:
            providers.append(
                {
                    "name": provider.name,
                    "configured": provider.configured(),
                    "role": getattr(provider, "role", "provider"),
                }
            )
        providers.append(
            {
                "name": self.odds_provider.name,
                "configured": self.odds_provider.configured(),
                "role": self.odds_provider.role,
            }
        )
        providers.append(
            {
                "name": self.betfair_odds_provider.name,
                "configured": self.betfair_odds_provider.configured(),
                "role": self.betfair_odds_provider.role,
            }
        )
        providers.append(
            {
                "name": self.sporttery_odds_provider.name,
                "configured": self.sporttery_odds_provider.configured(),
                "role": self.sporttery_odds_provider.role,
            }
        )
        return providers

    def validate_sources(self) -> list[dict[str, Any]]:
        sources = []
        for provider in self.providers:
            validate = getattr(provider, "validate", None)
            if validate:
                sources.append(validate())
        sources.append(self.odds_provider.validate())
        sources.append(self.betfair_odds_provider.validate())
        sources.append(self.sporttery_odds_provider.validate())
        return sources


def first_present(payload: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return value
    return None


def split_event_name(name: str) -> tuple[str | None, str | None]:
    for separator in (" v ", " vs ", " - "):
        if separator in name:
            left, right = name.split(separator, 1)
            return left.strip() or None, right.strip() or None
    return None, None


def first_by_similarity(odds: dict[str, float], team: str) -> float | None:
    if not odds:
        return None
    name, value = max(
        odds.items(),
        key=lambda item: SequenceMatcher(None, str(item[0]).lower(), team.lower()).ratio(),
    )
    if SequenceMatcher(None, str(name).lower(), team.lower()).ratio() < 0.45:
        return None
    return value


def name_pair_score(home: str, away: str, event_home: str, event_away: str) -> float:
    direct = SequenceMatcher(None, home.lower(), str(event_home).lower()).ratio() + SequenceMatcher(
        None, away.lower(), str(event_away).lower()
    ).ratio()
    swapped = SequenceMatcher(None, home.lower(), str(event_away).lower()).ratio() + SequenceMatcher(
        None, away.lower(), str(event_home).lower()
    ).ratio()
    return max(direct, swapped)


def normalize_three_way_odds(
    payload: dict[str, Any],
    *,
    home_key: str,
    draw_key: str,
    away_key: str,
) -> dict[str, float]:
    return compact_odds(
        {
            "home": payload.get(home_key) or payload.get("home") or payload.get("win"),
            "draw": payload.get(draw_key) or payload.get("draw"),
            "away": payload.get(away_key) or payload.get("away") or payload.get("lose"),
        }
    )


def normalize_two_way_odds(payload: dict[str, Any]) -> dict[str, float]:
    return compact_odds(
        {
            "over": payload.get("over") or payload.get("大") or payload.get("h"),
            "under": payload.get("under") or payload.get("小") or payload.get("a"),
        }
    )


def compact_odds(payload: dict[str, Any]) -> dict[str, float]:
    compact: dict[str, float] = {}
    for key, value in payload.items():
        try:
            if value not in (None, "", "-"):
                compact[key] = float(value)
        except (TypeError, ValueError):
            continue
    return compact
