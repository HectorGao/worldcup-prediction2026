from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()


API_FOOTBALL_BASE = "https://v3.football.api-sports.io"
API_FOOTBALL_LINEUPS_URL = (
    "https://www.api-football.com/news/post/fifa-world-cup-2026-lineups-all-teams-coaches-and-players"
)


class ApiFootballRosterProvider:
    name = "API-Football rosters"

    def __init__(self, key: str | None = None):
        self.key = key or os.getenv("API_FOOTBALL_KEY")

    def configured(self) -> bool:
        return bool(self.key)

    def lookup_team(self, team: str) -> dict[str, Any] | None:
        if not self.configured():
            return None
        payload = self._get("/teams", {"name": team})
        teams = payload.get("response") or []
        if not teams:
            return None
        team_payload = (teams[0] or {}).get("team") or {}
        return {
            "team_id": team_payload.get("id"),
            "team": team_payload.get("name") or team,
            "country": team_payload.get("country"),
            "logo": team_payload.get("logo"),
        }

    def fetch_squad(self, team: str) -> dict[str, Any]:
        team_info = self.lookup_team(team)
        if not team_info:
            return ApiFootballLineupsPageScraper().fetch_squad(team)
        team_id = team_info["team_id"]
        squad_payload = self._get("/players/squads", {"team": team_id})
        coach_payload = self._get("/coachs", {"team": team_id})
        players = self._parse_squad_players(squad_payload)
        return {
            "team": team_info["team"],
            "team_id": team_id,
            "source": "api-football",
            "source_url": "https://www.api-football.com/documentation-v3",
            "coach": self._parse_coach(coach_payload, team_id),
            "players": players,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

    def fetch_player_statistics(
        self,
        player_id: int | str,
        seasons: tuple[int, ...] = (2026, 2025, 2024),
    ) -> dict[str, Any]:
        if not self.configured():
            return {
                "player_id": int(player_id),
                "stats_status": "failed",
                "last_error": "API_FOOTBALL_KEY not set",
            }
        attempted: list[int] = []
        last_error: str | None = None
        for season in seasons:
            attempted.append(season)
            try:
                payload = self._get("/players", {"id": player_id, "season": season})
            except httpx.HTTPError as exc:
                last_error = _safe_error(str(exc))
                continue
            errors = payload.get("errors")
            if errors:
                last_error = _safe_error(str(errors))
                continue
            responses = payload.get("response") or []
            if not responses:
                last_error = "empty response"
                continue
            parsed = self._parse_player_statistics(responses[0], season)
            parsed["season_attempted"] = attempted
            return parsed
        return {
            "player_id": int(player_id),
            "season": None,
            "season_attempted": attempted,
            "stats_status": "failed",
            "last_error": last_error or "No player statistics found",
        }

    def validate(self) -> dict[str, Any]:
        if not self.configured():
            return _validation_result(self.name, configured=False, last_error="API_FOOTBALL_KEY not set")
        try:
            team = self.lookup_team("England")
            return _validation_result(
                self.name,
                configured=True,
                reachable=True,
                auth_valid=bool(team),
                sample_count=1 if team else 0,
                last_error=None if team else "England team lookup returned no records",
            )
        except (httpx.HTTPError, ValueError) as exc:
            return _validation_result(self.name, configured=True, last_error=_safe_error(str(exc)))

    def _get(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        response = httpx.get(
            f"{API_FOOTBALL_BASE}{endpoint}",
            params=params,
            headers={"x-apisports-key": self.key or ""},
            timeout=20,
        )
        response.raise_for_status()
        return response.json()

    def _parse_squad_players(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        response = payload.get("response") or []
        if not response:
            return []
        players = (response[0] or {}).get("players") or []
        return [
            {
                "player_id": player.get("id"),
                "name": player.get("name"),
                "age": player.get("age"),
                "number": player.get("number"),
                "position": player.get("position"),
                "photo": player.get("photo"),
                "source": "api-football",
            }
            for player in players
            if player.get("id") and player.get("name")
        ]

    def _parse_coach(self, payload: dict[str, Any], team_id: int | str) -> dict[str, Any] | None:
        coaches = payload.get("response") or []
        if not coaches:
            return None
        selected = coaches[0]
        for coach in coaches:
            career = coach.get("career") or []
            if any((entry.get("team") or {}).get("id") == team_id and not entry.get("end") for entry in career):
                selected = coach
                break
        firstname = selected.get("firstname")
        lastname = selected.get("lastname")
        name = f"{firstname} {lastname}".strip() if firstname and lastname else selected.get("name")
        current_job = self._current_coach_job(selected.get("career") or [], team_id)
        return {
            "coach_id": selected.get("id"),
            "name": name,
            "short_name": selected.get("name"),
            "nationality": selected.get("nationality"),
            "photo": selected.get("photo"),
            "start": current_job.get("start") if current_job else None,
            "end": current_job.get("end") if current_job else None,
        }

    def _current_coach_job(self, career: list[dict[str, Any]], team_id: int | str) -> dict[str, Any] | None:
        for entry in career:
            if (entry.get("team") or {}).get("id") == team_id and not entry.get("end"):
                return entry
        return career[-1] if career else None

    def _parse_player_statistics(self, response: dict[str, Any], season: int) -> dict[str, Any]:
        player = response.get("player") or {}
        stat = self._primary_statistics(response.get("statistics") or [])
        games = stat.get("games") or {}
        team = stat.get("team") or {}
        league = stat.get("league") or {}
        goals = stat.get("goals") or {}
        passes = stat.get("passes") or {}
        tackles = stat.get("tackles") or {}
        goalkeeper = stat.get("goals") or {}
        return {
            "player_id": player.get("id"),
            "name": player.get("name"),
            "season": season,
            "club": team.get("name"),
            "club_id": team.get("id"),
            "league": league.get("name"),
            "league_id": league.get("id"),
            "league_country": league.get("country"),
            "position": games.get("position"),
            "appearances": games.get("appearences") or games.get("appearances") or 0,
            "starts": games.get("lineups") or 0,
            "minutes": games.get("minutes") or 0,
            "rating": _float(games.get("rating")),
            "goals": goals.get("total") or 0,
            "assists": goals.get("assists") or 0,
            "passes": passes.get("total") or 0,
            "key_passes": passes.get("key") or 0,
            "tackles": tackles.get("total") or 0,
            "interceptions": tackles.get("interceptions") or 0,
            "saves": goalkeeper.get("saves") or 0,
            "stats_status": "complete",
            "last_error": None,
        }

    def _primary_statistics(self, statistics: list[dict[str, Any]]) -> dict[str, Any]:
        if not statistics:
            return {}
        return max(statistics, key=lambda item: ((item.get("games") or {}).get("minutes") or 0))


class ApiFootballLineupsPageScraper:
    name = "API-Football 2026 lineups page"

    def fetch_squad(self, team: str) -> dict[str, Any]:
        response = httpx.get(API_FOOTBALL_LINEUPS_URL, timeout=20, follow_redirects=True)
        response.raise_for_status()
        return self.parse_squad(team, response.text)

    def parse_squad(self, team: str, html: str) -> dict[str, Any]:
        plain = re.sub(r"<[^>]+>", "\n", html)
        lines = [re.sub(r"\s+", " ", line).strip() for line in plain.splitlines()]
        lines = [line for line in lines if line]
        lower_team = team.lower()
        start = next((index for index, line in enumerate(lines) if lower_team == line.lower()), -1)
        window = lines[start : start + 90] if start >= 0 else []
        coach_line = next((line for line in window if "coach" in line.lower()), "")
        players: list[dict[str, Any]] = []
        for index, line in enumerate(window):
            if re.search(r"\b(goalkeeper|defender|midfielder|forward|attacker)\b", line, re.I):
                name = window[index - 1] if index > 0 else line
                if name and len(name) < 60:
                    players.append(
                        {
                            "player_id": f"web-{team}-{len(players) + 1}",
                            "name": name,
                            "age": None,
                            "number": None,
                            "position": line,
                            "photo": None,
                            "source": "api-football-lineups-page",
                        }
                    )
        return {
            "team": team,
            "team_id": None,
            "source": "api-football-lineups-page",
            "source_url": API_FOOTBALL_LINEUPS_URL,
            "coach": {"name": coach_line.replace("Coach:", "").strip() or None},
            "players": players,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }


def _validation_result(
    name: str,
    configured: bool,
    reachable: bool = False,
    auth_valid: bool = False,
    quota_remaining: int | None = None,
    sample_count: int = 0,
    last_error: str | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "configured": configured,
        "reachable": reachable,
        "auth_valid": auth_valid,
        "quota_remaining": quota_remaining,
        "sample_count": sample_count,
        "last_error": _safe_error(last_error) if last_error else None,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def _float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_error(message: str) -> str:
    for key_name in ("API_FOOTBALL_KEY", "ODDS_API_KEY", "SPORTMONKS_API_TOKEN"):
        key = os.getenv(key_name)
        if key:
            message = message.replace(key, "[redacted]")
    return message
