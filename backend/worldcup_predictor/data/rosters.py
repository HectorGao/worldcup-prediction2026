from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any
from unicodedata import normalize

import httpx
from dotenv import load_dotenv

load_dotenv()


API_FOOTBALL_BASE = "https://v3.football.api-sports.io"
API_FOOTBALL_LINEUPS_URL = (
    "https://www.api-football.com/news/post/fifa-world-cup-2026-lineups-all-teams-coaches-and-players"
)
THESPORTSDB_BASE = "https://www.thesportsdb.com/api/v1/json"
VERIFIED_PLAYER_ALIASES = {
    ("england", "o watkins"): "Ollie Watkins",
    ("uzbekistan", "o orunov"): "Oston Urunov",
    ("uzbekistan", "a ganiyev"): "Azizjon Ganiev",
}


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
        start = next(
            (
                index
                for index, line in enumerate(lines)
                if line.lower() in {lower_team, f"{lower_team} squad list"}
            ),
            -1,
        )
        window = lines[start : start + 90] if start >= 0 else []
        coach_line = next((line for line in window if "coach" in line.lower()), "")
        players: list[dict[str, Any]] = []
        for line in window:
            category = self._category_for_line(line)
            if not category:
                if line.lower().endswith("squad list") and line != window[0]:
                    break
                continue
            names = line.split(":", 1)[1] if ":" in line else ""
            for name in self._split_names(names):
                players.append(
                    {
                        "player_id": f"web-{team}-{len(players) + 1}",
                        "name": name,
                        "age": None,
                        "number": None,
                        "position": category,
                        "photo": None,
                        "source": "api-football-lineups-page",
                    }
                )
        if not players:
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
                                "position": self._position_from_text(line),
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

    def _category_for_line(self, line: str) -> str | None:
        label = line.split(":", 1)[0].strip().lower()
        if label == "goalkeepers":
            return "Goalkeeper"
        if label == "defenders":
            return "Defender"
        if label == "midfielders":
            return "Midfielder"
        if label == "forwards":
            return "Attacker"
        return None

    def _split_names(self, text: str) -> list[str]:
        names = re.split(r"\s*[·•]\s*|\s*,\s*", text)
        return [name.strip() for name in names if name.strip()]

    def _position_from_text(self, text: str) -> str:
        lower = text.lower()
        if "goalkeeper" in lower:
            return "Goalkeeper"
        if "defender" in lower:
            return "Defender"
        if "forward" in lower or "attacker" in lower:
            return "Attacker"
        return "Midfielder"


class TheSportsDBRosterProvider:
    name = "TheSportsDB public player search"

    def __init__(
        self,
        key: str = "123",
        lineup_aliases: dict[str, list[dict[str, Any]]] | None = None,
    ):
        self.key = key
        self.lineup_aliases = lineup_aliases or {}
        self.lineup_scraper = ApiFootballLineupsPageScraper()

    def configured(self) -> bool:
        return bool(self.key)

    def enrich_player(self, player: dict[str, Any], team: str) -> dict[str, Any]:
        candidate = self._candidate_name(player, team)
        payload = self._get("searchplayers.php", {"p": candidate})
        matches = payload.get("player") or []
        best = self._best_player_match(matches, candidate, team)
        if not best:
            return {
                "player_id": player["player_id"],
                "name": candidate,
                "season": None,
                "stats_status": "failed",
                "last_error": "TheSportsDB player search returned no trusted match",
                "source": "thesportsdb",
            }
        team_payload = self._lookup_team(best.get("idTeam"))
        return {
            "player_id": player["player_id"],
            "external_player_id": best.get("idPlayer"),
            "name": best.get("strPlayer") or candidate,
            "season": 2026,
            "club": best.get("strTeam"),
            "club_id": best.get("idTeam"),
            "league": team_payload.get("strLeague"),
            "league_id": team_payload.get("idLeague"),
            "league_country": team_payload.get("strCountry"),
            "position": best.get("strPosition") or player.get("position"),
            "appearances": 0,
            "starts": 0,
            "minutes": 0,
            "rating": None,
            "goals": 0,
            "assists": 0,
            "passes": 0,
            "key_passes": 0,
            "tackles": 0,
            "interceptions": 0,
            "saves": 0,
            "photo": best.get("strThumb") or best.get("strCutout"),
            "stats_status": "enriched",
            "source": "thesportsdb",
            "source_confidence": self._confidence(best, candidate, team),
            "last_error": None,
        }

    def validate(self) -> dict[str, Any]:
        try:
            payload = self._get("searchplayers.php", {"p": "Cristiano Ronaldo"})
            count = len(payload.get("player") or [])
            return _validation_result(
                self.name,
                configured=True,
                reachable=True,
                auth_valid=count > 0,
                sample_count=count,
            )
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            return _validation_result(self.name, configured=True, last_error=_safe_error(str(exc)))

    def _candidate_name(self, player: dict[str, Any], team: str) -> str:
        name = str(player.get("name") or "")
        if not _looks_abbreviated(name):
            return name
        for candidate in self._lineup_players(team):
            candidate_name = str(candidate.get("name") or "")
            if not candidate_name:
                continue
            if _last_name(candidate_name) == _last_name(name):
                return candidate_name
        alias = VERIFIED_PLAYER_ALIASES.get((_normalized(team), _normalized(name)))
        if alias:
            return alias
        return name

    def _lineup_players(self, team: str) -> list[dict[str, Any]]:
        if team in self.lineup_aliases:
            return self.lineup_aliases[team]
        try:
            squad = self.lineup_scraper.fetch_squad(team)
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            self.lineup_aliases[team] = []
            return []
        self.lineup_aliases[team] = squad.get("players", [])
        return self.lineup_aliases[team]

    def _best_player_match(
        self,
        players: list[dict[str, Any]],
        candidate: str,
        team: str,
    ) -> dict[str, Any] | None:
        soccer_players = [
            player
            for player in players
            if not player.get("strSport") or player.get("strSport") == "Soccer"
        ]
        if not soccer_players:
            return None
        return max(
            soccer_players,
            key=lambda player: (
                self._team_match(player, team),
                _name_similarity(player.get("strPlayer"), candidate),
                float(player.get("relevance") or 0),
            ),
        )

    def _team_match(self, player: dict[str, Any], team: str) -> float:
        nationality = _normalized(player.get("strNationality"))
        return 1.0 if nationality == _normalized(team) else 0.0

    def _lookup_team(self, team_id: str | None) -> dict[str, Any]:
        if not team_id:
            return {}
        payload = self._get("lookupteam.php", {"id": team_id})
        teams = payload.get("teams") or []
        return teams[0] if teams else {}

    def _confidence(self, player: dict[str, Any], candidate: str, team: str) -> float:
        base = 0.52
        base += 0.25 * _name_similarity(player.get("strPlayer"), candidate)
        base += 0.18 * self._team_match(player, team)
        return round(min(0.95, base), 4)

    def _get(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        response = httpx.get(
            f"{THESPORTSDB_BASE}/{self.key}/{endpoint}",
            params=params,
            timeout=20,
        )
        response.raise_for_status()
        return response.json()


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


def _looks_abbreviated(name: str) -> bool:
    return bool(re.match(r"^[A-Z]\.\s+\S+", name))


def _last_name(name: str) -> str:
    parts = _normalized(name).split()
    return parts[-1] if parts else ""


def _normalized(value: Any) -> str:
    text = normalize("NFKD", str(value or ""))
    text = "".join(character for character in text if not re.match(r"[\u0300-\u036f]", character))
    text = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def _name_similarity(left: Any, right: Any) -> float:
    return SequenceMatcher(None, _normalized(left), _normalized(right)).ratio()
