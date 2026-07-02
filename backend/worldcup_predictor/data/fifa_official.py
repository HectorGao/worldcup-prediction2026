from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

import httpx


FIFA_WORLD_CUP_URL = "https://www.fifa.com/en/tournaments/mens/worldcup"
SOURCE_PRIORITIES = {
    "FIFA": 1,
    "FootballData.io": 2,
    "ESPN": 3,
    "sporttery": 4,
    "other": 5,
}


class FifaOfficialWorldCupCrawler:
    name = "FIFA"
    role = "official_world_cup_highest_priority"

    def __init__(self, client: httpx.Client | None = None):
        self.client = client or httpx.Client(
            timeout=25,
            follow_redirects=True,
            headers={"User-Agent": "world-cup-prediction-local/0.1"},
        )

    def fetch_world_cup_data(self, url: str = FIFA_WORLD_CUP_URL) -> dict[str, Any]:
        response = self.client.get(url)
        response.raise_for_status()
        return self.parse_page(response.text, source_url=str(response.url))

    def parse_page(self, html: str, source_url: str = FIFA_WORLD_CUP_URL) -> dict[str, Any]:
        payloads = self._embedded_json_payloads(html)
        merged = {"matches": [], "power_rankings": [], "rosters": [], "news": [], "warnings": []}
        for payload in payloads:
            parsed = self.parse_embedded_payload(payload, source_url=source_url)
            merged["matches"].extend(parsed["matches"])
            merged["power_rankings"].extend(parsed["power_rankings"])
            merged["rosters"].extend(parsed["rosters"])
            merged["news"].extend(parsed["news"])
            merged["warnings"].extend(parsed["warnings"])
        if not payloads:
            merged["warnings"].append("No embedded FIFA JSON payload found; dynamic API discovery may be required.")
        merged["source"] = self.name
        merged["source_url"] = source_url
        merged["fetched_at"] = datetime.now(timezone.utc).isoformat()
        return merged

    def parse_embedded_payload(self, payload: dict[str, Any], source_url: str = FIFA_WORLD_CUP_URL) -> dict[str, Any]:
        match_items = self._find_items(payload, {"homeTeam", "awayTeam"})
        matches = [self._normalize_match(item, source_url) for item in match_items]
        power_rankings = [
            self._normalize_power_ranking(item, source_url)
            for item in self._find_power_ranking_items(payload)
        ]
        rosters: list[dict[str, Any]] = []
        for item in match_items:
            rosters.extend(self._normalize_match_rosters(item, source_url))
        news = [
            self._normalize_news(item, source_url)
            for item in self._find_items(payload, {"title"})
            if item.get("title") and (item.get("slug") or item.get("url"))
        ][:20]
        return {
            "source": self.name,
            "matches": [match for match in matches if match],
            "power_rankings": [item for item in power_rankings if item],
            "rosters": rosters,
            "news": [item for item in news if item],
            "warnings": [],
        }

    def _embedded_json_payloads(self, html: str) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        patterns = [
            r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>',
            r'<script[^>]+type="application/json"[^>]*>(.*?)</script>',
        ]
        for pattern in patterns:
            for match in re.finditer(pattern, html, re.S):
                text = match.group(1).strip()
                if not text:
                    continue
                try:
                    payloads.append(json.loads(text))
                except ValueError:
                    continue
        return payloads

    def _find_items(self, value: Any, required_keys: set[str]) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        if isinstance(value, dict):
            if required_keys <= set(value):
                found.append(value)
            for child in value.values():
                found.extend(self._find_items(child, required_keys))
        elif isinstance(value, list):
            for child in value:
                found.extend(self._find_items(child, required_keys))
        return found

    def _find_power_ranking_items(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        candidates = self._find_items(payload, {"player", "team"})
        candidates.extend(self._find_items(payload, {"playerName", "teamName"}))
        return [
            item
            for item in candidates
            if any(key in item for key in ("rating", "rank", "score", "powerRating"))
        ]

    def _normalize_match(self, item: dict[str, Any], source_url: str) -> dict[str, Any] | None:
        home = item.get("homeTeam") or item.get("home") or {}
        away = item.get("awayTeam") or item.get("away") or {}
        home_name = _name(home)
        away_name = _name(away)
        if not home_name or not away_name:
            return None
        score = item.get("score") or item.get("result") or {}
        penalties = item.get("penalties") or item.get("shootout") or {}
        return {
            "match_id": f"fifa-{item.get('id') or item.get('matchId') or f'{home_name}-{away_name}'}",
            "source_id": str(item.get("id") or item.get("matchId") or ""),
            "date": str(item.get("date") or item.get("matchDate") or item.get("kickoff") or "")[:10],
            "stage": item.get("stage") or item.get("phase") or item.get("round"),
            "home_team": home_name,
            "away_team": away_name,
            "home_goals_90": _score(score, "home"),
            "away_goals_90": _score(score, "away"),
            "home_goals_extra_time": _score(item.get("extraTime") or {}, "home"),
            "away_goals_extra_time": _score(item.get("extraTime") or {}, "away"),
            "home_penalties": _score(penalties, "home"),
            "away_penalties": _score(penalties, "away"),
            "winner": _name(item.get("winner") or {}) or item.get("winnerName"),
            "loser": _name(item.get("loser") or {}) or item.get("loserName"),
            "is_finished": str(item.get("status") or "").lower() in {"final", "finished", "completed"},
            "status": "final" if str(item.get("status") or "").lower() in {"final", "finished", "completed"} else "scheduled",
            "source": self.name,
            "source_priority": SOURCE_PRIORITIES[self.name],
            "source_url": item.get("url") or source_url,
            "match_stats": item.get("stats") or item.get("matchStats") or {},
            "lineup": item.get("lineup") or item.get("lineups") or {},
            "related_matches": item.get("relatedMatches") or [],
            "news": item.get("news") or [],
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

    def _normalize_power_ranking(self, item: dict[str, Any], source_url: str) -> dict[str, Any] | None:
        player = item.get("player") or item.get("playerName") or item.get("name")
        team = item.get("team") or item.get("teamName") or item.get("country")
        if isinstance(player, dict):
            player = _name(player)
        if isinstance(team, dict):
            team = _name(team)
        if not player or not team:
            return None
        rating = item.get("rating") or item.get("score") or item.get("powerRating") or item.get("rank")
        return {
            "player_name": str(player),
            "team": str(team),
            "position": item.get("position") or item.get("role"),
            "rating": _float(rating),
            "rank": _float(item.get("rank")),
            "source": self.name,
            "source_priority": SOURCE_PRIORITIES[self.name],
            "source_url": item.get("url") or source_url,
            "payload": item,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    def _normalize_match_rosters(self, item: dict[str, Any], source_url: str) -> list[dict[str, Any]]:
        lineup = item.get("lineup") or item.get("lineups") or {}
        if not isinstance(lineup, dict):
            return []
        rosters: list[dict[str, Any]] = []
        for side, team_payload in (("home", item.get("homeTeam") or item.get("home")), ("away", item.get("awayTeam") or item.get("away"))):
            side_payload = lineup.get(side) or lineup.get(f"{side}Team") or {}
            if not isinstance(side_payload, dict):
                continue
            team_name = _name(side_payload.get("team")) or _name(team_payload) or side_payload.get("teamName")
            players: list[dict[str, Any]] = []
            players.extend(self._lineup_players(side_payload.get("startingXI") or side_payload.get("starters"), "starting"))
            players.extend(self._lineup_players(side_payload.get("substitutes") or side_payload.get("bench"), "substitute"))
            if not players:
                players.extend(self._lineup_players(side_payload.get("players") or side_payload.get("squad"), "squad"))
            if team_name and players:
                rosters.append(
                    {
                        "team": str(team_name),
                        "source": self.name,
                        "source_priority": SOURCE_PRIORITIES[self.name],
                        "source_url": source_url,
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                        "players": players,
                    }
                )
        return rosters

    def _lineup_players(self, value: Any, role: str) -> list[dict[str, Any]]:
        rows = value if isinstance(value, list) else []
        players = []
        for index, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                continue
            name = row.get("name") or row.get("playerName") or _name(row.get("player"))
            if not name:
                continue
            player_id = row.get("id") or row.get("playerId") or row.get("player_id") or f"{role}-{index}-{name}"
            players.append(
                {
                    "player_id": str(player_id),
                    "source_id": str(player_id),
                    "name": str(name),
                    "position": row.get("position") or row.get("role"),
                    "number": row.get("number") or row.get("shirtNumber"),
                    "lineup_role": role,
                    "fifa_power_rating": _float(row.get("rating") or row.get("powerRating") or row.get("score")),
                    "source": self.name,
                    "source_priority": SOURCE_PRIORITIES[self.name],
                }
            )
        return players

    def _normalize_news(self, item: dict[str, Any], source_url: str) -> dict[str, Any] | None:
        title = item.get("title")
        if not title:
            return None
        return {
            "title": title,
            "url": item.get("url") or item.get("slug") or source_url,
            "source": self.name,
            "source_priority": SOURCE_PRIORITIES[self.name],
        }


def merge_field_sources(rows: list[dict[str, Any]]) -> dict[str, Any]:
    fields: dict[str, dict[str, Any]] = {}
    warnings: list[dict[str, Any]] = []
    for row in rows:
        source = str(row.get("source") or "other")
        priority = int(row.get("source_priority") or SOURCE_PRIORITIES.get(source, SOURCE_PRIORITIES["other"]))
        for field, value in row.items():
            if field in {"source", "source_priority", "source_id", "updated_at", "payload"} or value is None:
                continue
            current = fields.get(field)
            if current and current["value"] != value:
                warnings.append(
                    {
                        "field": field,
                        "kept_source": current["source"] if current["source_priority"] <= priority else source,
                        "discarded_source": source if current["source_priority"] <= priority else current["source"],
                        "kept_value": current["value"] if current["source_priority"] <= priority else value,
                        "discarded_value": value if current["source_priority"] <= priority else current["value"],
                    }
                )
            if not current or priority < current["source_priority"]:
                fields[field] = {
                    "value": value,
                    "source": source,
                    "source_priority": priority,
                    "source_id": row.get("source_id"),
                    "updated_at": row.get("updated_at") or datetime.now(timezone.utc).isoformat(),
                }
    return {"fields": fields, "warnings": warnings}


def _name(value: Any) -> str | None:
    if isinstance(value, dict):
        return value.get("name") or value.get("displayName") or value.get("shortName")
    return str(value) if value else None


def _score(score: dict[str, Any], side: str) -> int | None:
    if not isinstance(score, dict):
        return None
    value = score.get(side) or score.get(f"{side}Score") or score.get(f"{side}_score")
    try:
        return int(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    try:
        return float(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None
