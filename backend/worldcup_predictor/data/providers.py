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
        return providers

    def validate_sources(self) -> list[dict[str, Any]]:
        sources = []
        for provider in self.providers:
            validate = getattr(provider, "validate", None)
            if validate:
                sources.append(validate())
        sources.append(self.odds_provider.validate())
        return sources
