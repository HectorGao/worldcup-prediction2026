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
    name = "SportMonks"
    role = "supplemental_fixtures_stats_lineups"
    base_url = "https://api.sportmonks.com/v3/football"
    source_priority = 2
    rich_fixture_includes = "participants;scores;state;lineups;statistics;formations;sidelined;odds;standings"
    fallback_fixture_includes = "participants;scores;state;lineups;statistics;formations;sidelined;standings"

    def __init__(self, token: str | None = None):
        self.token = token or os.getenv("SPORTMONKS_API_KEY") or os.getenv("SPORTMONKS_API_TOKEN")
        self.capability_warnings: list[str] = []
        self.last_rate_limit: dict[str, Any] = {}

    def configured(self) -> bool:
        return bool(self.token)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": str(self.token or ""),
            "Accept": "application/json",
            "User-Agent": "world-cup-prediction-local/0.1",
        }

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = httpx.get(f"{self.base_url}{path}", params=params or {}, headers=self._headers(), timeout=20)
        if response.status_code in {400, 403} and params and params.get("include") == self.rich_fixture_includes:
            self.capability_warnings.append(
                f"SportMonks rich fixture include unavailable HTTP {response.status_code}; retried without odds."
            )
            fallback_params = dict(params)
            fallback_params["include"] = self.fallback_fixture_includes
            response = httpx.get(f"{self.base_url}{path}", params=fallback_params, headers=self._headers(), timeout=20)
        response.raise_for_status()
        self.last_rate_limit = self._rate_limit_meta(response)
        return response.json()

    def _rate_limit_meta(self, response: Any) -> dict[str, Any]:
        headers = getattr(response, "headers", {}) or {}
        meta: dict[str, Any] = {}
        for key in ("x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset"):
            value = headers.get(key)
            if value is not None:
                clean_key = key.replace("x-ratelimit-", "").replace("-", "_")
                try:
                    meta[clean_key] = int(value)
                except (TypeError, ValueError):
                    meta[clean_key] = value
        try:
            payload = response.json()
        except Exception:
            payload = {}
        for container_key in ("rate_limit", "meta"):
            value = payload.get(container_key) if isinstance(payload, dict) else None
            if isinstance(value, dict):
                meta.update({k: v for k, v in value.items() if "key" not in str(k).lower() and "token" not in str(k).lower()})
        return meta

    def fetch_fixtures(self, date: str) -> list[dict[str, Any]]:
        if not self.configured():
            return []
        try:
            payload = self._get(
                f"/fixtures/date/{date}",
                params={"include": self.rich_fixture_includes, "per_page": 50, "page": 1},
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                self.capability_warnings.append(f"SportMonks returned no fixture route coverage for date {date}.")
                return []
            raise
        return [self._normalize(item, date) for item in payload.get("data", [])]

    def fetch_fixture_detail(self, source_id: str) -> dict[str, Any]:
        if not self.configured():
            return {}
        return self._get(f"/fixtures/{source_id}", params={"include": self.rich_fixture_includes})

    def fetch_teams(self, season_id: int | str | None = None) -> list[dict[str, Any]]:
        if not self.configured():
            return []
        path = f"/teams/seasons/{season_id}" if season_id else "/teams"
        return self._get(path, params={"per_page": 50}).get("data") or []

    def fetch_players(self, country_id: int | str | None = None) -> list[dict[str, Any]]:
        if not self.configured():
            return []
        path = f"/players/countries/{country_id}" if country_id else "/players"
        return self._get(path, params={"per_page": 50}).get("data") or []

    def fetch_team_squad(self, team_id: int | str) -> list[dict[str, Any]]:
        if not self.configured():
            return []
        return self._get(f"/squads/teams/{team_id}", params={"include": "player;position", "per_page": 50}).get("data") or []

    def fetch_livescores(self) -> list[dict[str, Any]]:
        if not self.configured():
            return []
        return self._get("/livescores", params={"include": "participants;scores;state", "per_page": 50}).get("data") or []

    def fetch_standings(self, season_id: int | str | None = None) -> list[dict[str, Any]]:
        if not self.configured() or not season_id:
            return []
        return self._get(f"/standings/seasons/{season_id}", params={"include": "participant", "per_page": 50}).get("data") or []

    def _normalize(self, item: dict[str, Any], date: str) -> dict[str, Any]:
        participants = item.get("participants") or []
        home = next((team for team in participants if team.get("meta", {}).get("location") == "home"), {})
        away = next((team for team in participants if team.get("meta", {}).get("location") == "away"), {})
        home_score, away_score = self._scores(item, home.get("id"), away.get("id"))
        features = self._features(item, home.get("id"), away.get("id"))
        return {
            "id": f"sportmonks-{item.get('id')}",
            "source": "sportmonks",
            "source_priority": self.source_priority,
            "source_id": str(item.get("id") or ""),
            "updated_at": _checked_at(),
            "date": date,
            "kickoff": item.get("starting_at") or f"{date}T00:00:00+08:00",
            "home_team": home.get("name") or "Home",
            "away_team": away.get("name") or "Away",
            "group": item.get("group", {}).get("name") if isinstance(item.get("group"), dict) else None,
            "venue": (item.get("venue") or {}).get("name"),
            "status": self._status(item),
            "home_score": home_score,
            "away_score": away_score,
            "home_elo": 1700,
            "away_elo": 1700,
            "market_home": None,
            "market_draw": None,
            "market_away": None,
            "sportmonks_features": features,
            "sportmonks_lineups": item.get("lineups") or [],
            "sportmonks_statistics": item.get("statistics") or [],
            "sportmonks_sidelined": item.get("sidelined") or [],
            "sportmonks_standings": item.get("standings") or [],
            "payload": item,
        }

    def _status(self, item: dict[str, Any]) -> str:
        state = item.get("state")
        state_name = str((state or {}).get("name") if isinstance(state, dict) else item.get("state_name") or "").lower()
        result_info = str(item.get("result_info") or "").lower()
        if state_name in {"finished", "ended", "full-time", "ft"} or "after full-time" in result_info:
            return "final"
        return "scheduled"

    def _scores(self, item: dict[str, Any], home_id: Any, away_id: Any) -> tuple[int | None, int | None]:
        home_score = away_score = None
        for score in item.get("scores") or []:
            participant_id = score.get("participant_id") or score.get("team_id")
            value = score.get("score")
            if isinstance(value, dict):
                value = first_present(value, ["goals", "score", "total"])
            try:
                parsed = int(value) if value not in (None, "") else None
            except (TypeError, ValueError):
                parsed = None
            if str(participant_id) == str(home_id):
                home_score = parsed
            elif str(participant_id) == str(away_id):
                away_score = parsed
        return home_score, away_score

    def _features(self, item: dict[str, Any], home_id: Any, away_id: Any) -> dict[str, Any]:
        features: dict[str, Any] = {
            "lineup_count": len(item.get("lineups") or []),
            "sidelined_home": 0,
            "sidelined_away": 0,
            "standing_home_position": None,
            "standing_away_position": None,
            "has_odds": bool(item.get("has_odds") or item.get("odds")),
        }
        for row in item.get("sidelined") or []:
            participant_id = row.get("participant_id") or row.get("team_id")
            if str(participant_id) == str(home_id):
                features["sidelined_home"] += 1
            elif str(participant_id) == str(away_id):
                features["sidelined_away"] += 1
        for row in item.get("standings") or []:
            participant_id = row.get("participant_id") or row.get("team_id")
            position = row.get("position") or row.get("rank")
            if str(participant_id) == str(home_id):
                features["standing_home_position"] = position
            elif str(participant_id) == str(away_id):
                features["standing_away_position"] = position
        for stat in item.get("statistics") or []:
            participant_id = stat.get("participant_id") or stat.get("team_id")
            raw_name = (stat.get("type") or {}).get("name") if isinstance(stat.get("type"), dict) else stat.get("type")
            key = self._stat_key(raw_name)
            if not key:
                continue
            value = stat.get("data")
            if isinstance(value, dict):
                value = first_present(value, ["value", "count", "total"])
            suffix = "home" if str(participant_id) == str(home_id) else "away" if str(participant_id) == str(away_id) else None
            if suffix:
                features[f"{key}_{suffix}"] = value
        return features

    def _stat_key(self, name: Any) -> str | None:
        normalized = str(name or "").strip().lower().replace("%", "pct")
        if not normalized:
            return None
        aliases = {
            "shots on target": "shots_on_target",
            "shots": "shots",
            "ball possession": "possession",
            "possession": "possession",
            "passes": "passes",
            "corners": "corners",
            "corner kicks": "corners",
            "goals": "goals",
        }
        return aliases.get(normalized, normalized.replace(" ", "_").replace("-", "_"))

    def validate(self) -> dict[str, Any]:
        if not self.configured():
            return _validation_result(self.name, configured=False, last_error="SPORTMONKS_API_KEY not set")
        try:
            fixtures = self.fetch_fixtures("2026-07-02")
            if not fixtures:
                probe = self._get("/fixtures", params={"per_page": 1})
                fixtures = [self._normalize(item, str(item.get("starting_at") or "")[:10] or "unknown") for item in probe.get("data", [])]
            teams_status = self._probe_collection("/teams")
            players_status = self._probe_collection("/players")
            livescores_status = self._probe_collection("/livescores")
            detail_status = "SKIPPED"
            if fixtures and fixtures[0].get("source_id"):
                try:
                    detail = self.fetch_fixture_detail(str(fixtures[0]["source_id"]))
                    detail_data = detail.get("data") or {}
                    if isinstance(detail_data, list):
                        detail_data = detail_data[0] if detail_data else {}
                    normalized_detail = self._normalize(detail_data, str(detail_data.get("starting_at") or "")[:10] or "unknown") if detail_data else {}
                    detail_features = normalized_detail.get("sportmonks_features") or {}
                    detail_status = "OK"
                    if normalized_detail:
                        fixtures[0].update(
                            {
                                "sportmonks_lineups": normalized_detail.get("sportmonks_lineups") or fixtures[0].get("sportmonks_lineups"),
                                "sportmonks_statistics": normalized_detail.get("sportmonks_statistics") or fixtures[0].get("sportmonks_statistics"),
                                "sportmonks_sidelined": normalized_detail.get("sportmonks_sidelined") or fixtures[0].get("sportmonks_sidelined"),
                                "sportmonks_standings": normalized_detail.get("sportmonks_standings") or fixtures[0].get("sportmonks_standings"),
                                "sportmonks_features": {**(fixtures[0].get("sportmonks_features") or {}), **detail_features},
                            }
                        )
                except httpx.HTTPStatusError as exc:
                    detail_status = "SKIPPED"
                    self.capability_warnings.append(f"SportMonks fixture detail include skipped: HTTP {exc.response.status_code}.")
                except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                    detail_status = "SKIPPED"
                    self.capability_warnings.append(f"SportMonks fixture detail include skipped: {exc}.")
            capabilities = {
                "fixtures": "OK",
                "results": "OK" if any(item.get("home_score") is not None for item in fixtures) else "SKIPPED",
                "teams": teams_status,
                "players": players_status,
                "fixture_detail": detail_status,
                "livescores": livescores_status,
                "lineups": "OK" if any(item.get("sportmonks_lineups") for item in fixtures) else "SKIPPED",
                "statistics": "OK" if any(item.get("sportmonks_statistics") for item in fixtures) else "SKIPPED",
                "odds": "OK" if any((item.get("sportmonks_features") or {}).get("has_odds") for item in fixtures) else "SKIPPED",
                "standings": "OK" if any(item.get("sportmonks_standings") for item in fixtures) else "SKIPPED",
                "sidelined": "OK" if any(item.get("sportmonks_sidelined") for item in fixtures) else "SKIPPED",
            }
            remaining = self.last_rate_limit.get("remaining")
            return _validation_result(
                self.name,
                configured=True,
                reachable=True,
                auth_valid=True,
                quota_remaining=int(remaining) if isinstance(remaining, int) else None,
                sample_count=len(fixtures),
                capabilities=capabilities,
                warnings=list(self.capability_warnings),
                rate_limit=self.last_rate_limit,
            )
        except httpx.HTTPError as exc:
            return _validation_result(self.name, configured=True, last_error=str(exc))

    def _probe_collection(self, path: str) -> str:
        try:
            payload = self._get(path, params={"per_page": 1})
            return "OK" if payload.get("data") is not None else "SKIPPED"
        except httpx.HTTPStatusError as exc:
            self.capability_warnings.append(f"SportMonks {path} skipped: HTTP {exc.response.status_code}.")
            return "SKIPPED"
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            self.capability_warnings.append(f"SportMonks {path} skipped: {exc}.")
            return "SKIPPED"


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


class FootballDataIoProvider:
    name = "FootballData.io"
    role = "supplemental_matches_teams_stats"
    base_url = "https://footballdata.io/api/v1"

    def __init__(self, key: str | None = None):
        self.key = key or os.getenv("FOOTBALLDATA_IO_API_KEY")
        self.last_meta: dict[str, Any] = {}
        self.last_warnings: list[str] = []

    def configured(self) -> bool:
        return bool(self.key)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.key}", "Accept": "application/json"}

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = httpx.get(f"{self.base_url}{path}", params=params or {}, headers=self._headers(), timeout=20)
        response.raise_for_status()
        payload = response.json()
        self.last_meta = payload.get("meta") or {}
        return payload

    def fetch_fixtures(self, date: str) -> list[dict[str, Any]]:
        if not self.configured():
            return []
        payload = self._get("/matches", params={"date": date})
        rows = payload.get("data") or payload.get("matches") or []
        return [self._normalize_match(item, date) for item in rows]

    def fetch_match_detail(self, source_id: str) -> dict[str, Any]:
        if not self.configured():
            return {}
        return self._get(f"/matches/{source_id}")

    def fetch_teams(self) -> list[dict[str, Any]]:
        if not self.configured():
            return []
        payload = self._get("/teams")
        return payload.get("data") or payload.get("teams") or []

    def fetch_stats(self, match_id: str | None = None) -> list[dict[str, Any]]:
        if not self.configured():
            return []
        payload = self._get("/stats", params={"match_id": match_id} if match_id else {})
        return payload.get("data") or payload.get("stats") or []

    def fetch_players(self, team_id: str | None = None) -> list[dict[str, Any]]:
        if not self.configured():
            return []
        payload = self._get("/players", params={"team_id": team_id} if team_id else {})
        rows = payload.get("data") or payload.get("players") or []
        if not rows:
            self.last_warnings.append("FootballData.io did not return player ability rows for this request.")
        return rows

    def _normalize_match(self, item: dict[str, Any], date: str) -> dict[str, Any]:
        home = item.get("home_team") or item.get("homeTeam") or item.get("home") or {}
        away = item.get("away_team") or item.get("awayTeam") or item.get("away") or {}
        score = item.get("score") or item.get("result") or {}
        status = str(item.get("status") or "").lower()
        return {
            "id": f"footballdata-io-{item.get('id') or item.get('match_id')}",
            "source_id": str(item.get("id") or item.get("match_id") or ""),
            "source": self.name,
            "source_priority": 2,
            "date": str(item.get("date") or item.get("match_date") or date)[:10],
            "kickoff": item.get("kickoff") or item.get("utcDate") or item.get("start_time") or f"{date}T00:00:00Z",
            "home_team": self._team_name(home) or "Home",
            "away_team": self._team_name(away) or "Away",
            "group": self._display_value(item.get("stage") or item.get("round") or item.get("group")),
            "venue": self._display_value(item.get("venue")),
            "status": "final" if status in {"completed", "complete", "final", "finished"} else "scheduled",
            "home_score": self._score(score, "home"),
            "away_score": self._score(score, "away"),
            "home_elo": 1700,
            "away_elo": 1700,
            "market_home": None,
            "market_draw": None,
            "market_away": None,
            "footballdata_io_stats": item.get("stats") or {},
            "payload": item,
        }

    def _team_name(self, value: Any) -> str | None:
        if isinstance(value, dict):
            return value.get("name") or value.get("displayName") or value.get("short_name")
        return str(value) if value else None

    def _display_value(self, value: Any) -> str | None:
        if value is None or value == "":
            return None
        if isinstance(value, dict):
            return (
                value.get("name")
                or value.get("displayName")
                or value.get("short_name")
                or value.get("title")
                or value.get("label")
                or str(value.get("id") or "")
                or None
            )
        if isinstance(value, list):
            return ", ".join(str(item) for item in value if item is not None) or None
        return str(value)

    def _score(self, score: dict[str, Any], side: str) -> int | None:
        if not isinstance(score, dict):
            return None
        value = first_present(score, [side, f"{side}_score", f"{side}Score"])
        try:
            return int(value) if value is not None and value != "" else None
        except (TypeError, ValueError):
            return None

    def validate(self) -> dict[str, Any]:
        if not self.configured():
            return _validation_result(self.name, configured=False, last_error="FOOTBALLDATA_IO_API_KEY not set")
        try:
            fixtures = self.fetch_fixtures("2026-07-01")
            used = self.last_meta.get("requests_used")
            limit = self.last_meta.get("requests_limit")
            remaining = None
            if isinstance(used, int) and isinstance(limit, int):
                remaining = max(0, limit - used)
            return _validation_result(
                self.name,
                configured=True,
                reachable=True,
                auth_valid=True,
                quota_remaining=remaining,
                sample_count=len(fixtures),
                capabilities=["matches", "results", "match_detail", "teams", "stats", "players_optional"],
                warnings=list(self.last_warnings),
            )
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
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
    page_url = "https://m.sporttery.cn/mjc/jsq/zqspf/"
    endpoint = "https://webapi.sporttery.cn/gateway/uniform/football/getMatchCalculatorV1.qry"

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
        headers = self._headers()
        with httpx.Client(timeout=12, follow_redirects=True, headers=headers) as client:
            page_response = client.get(self.page_url, headers=headers)
            page_response.raise_for_status()
            page_text = page_response.text or ""
            endpoints = self._candidate_endpoints(page_text)
            last_error: Exception | None = None
            for endpoint in endpoints:
                for pool_code in ("hhad,had", "had,hhad"):
                    try:
                        response = client.get(
                            endpoint,
                            params={"channel": "m", "poolCode": pool_code},
                            headers=headers,
                        )
                        response.raise_for_status()
                        text = response.text.strip()
                        if "WAF" in text or "禁止访问" in text or text.startswith("<"):
                            raise ValueError("China Sporttery gateway blocked this request or returned non-JSON HTML")
                        events = self.parse_events(response.json())
                        return events
                    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                        last_error = exc
                        continue
            if last_error:
                raise last_error
        return []

    def _headers(self) -> dict[str, str]:
        return {
            "Referer": self.page_url,
            "Origin": "https://m.sporttery.cn",
            "User-Agent": (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
            ),
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
            "Connection": "keep-alive",
        }

    def _candidate_endpoints(self, page_text: str) -> list[str]:
        candidates = [self.endpoint]
        if "getMatchCalculatorV1.qry" in page_text:
            candidates.insert(0, self.endpoint)
        seen = set()
        unique = []
        for item in candidates:
            if item not in seen:
                seen.add(item)
                unique.append(item)
        return unique

    def fetch_historical_odds(self, start_date: str, end_date: str) -> list[dict[str, Any]]:
        return []

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
        home = first_present(item, ["homeTeamAllName", "homeTeamAbbName", "homeTeamName", "homeName", "home_team"])
        away = first_present(item, ["awayTeamAllName", "awayTeamAbbName", "awayTeamName", "awayName", "away_team"])
        if not home or not away:
            return None
        h2h = item.get("had") or item.get("spf") or {}
        handicap = item.get("hhad") or item.get("rqspf") or {}
        totals = item.get("ttg") or item.get("goals") or {}
        return {
            "source": self.name,
            "date": first_present(item, ["matchDate", "businessDate", "date"]),
            "match_num": first_present(item, ["matchNumStr", "matchNum", "matchId"]),
            "match_num_date": first_present(item, ["matchNumDate"]),
            "league": first_present(item, ["leagueAllName", "leagueAbbName", "l_cn"]),
            "home_team": home,
            "away_team": away,
            "h2h": normalize_three_way_odds(h2h, home_key="h", draw_key="d", away_key="a"),
            "handicap": normalize_three_way_odds(handicap, home_key="h", draw_key="d", away_key="a"),
            "handicap_line": first_present(handicap, ["fixedodds", "goalLine", "line"]) or first_present(item, ["goalLine"]),
            "totals": normalize_two_way_odds(totals),
            "updated_at": _checked_at(),
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
            else:
                for item in node:
                    lists.extend(self._find_event_lists(item))
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
        self.footballdata_io_provider = FootballDataIoProvider()
        self.sportmonks_provider = SportmonksProvider()
        self.providers: list[FixtureProvider] = [
            ApiFootballProvider(),
            self.footballdata_io_provider,
            self.sportmonks_provider,
            FootballDataProvider(),
            EspnScoreboardProvider(),
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
