from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from ..team_metadata import TEAM_METADATA, canonical_team_name


LYIHUB_BASE = "https://worldcup.lyihub.com"
LYIHUB_INDEX_URL = f"{LYIHUB_BASE}/data/index.json"


ZH_TO_CANONICAL = {meta["zh"]: team for team, meta in TEAM_METADATA.items()}
ZH_TO_CANONICAL.update(
    {
        "波黑": "Bosnia and Herzegovina",
        "捷克": "Czech Republic",
        "刚果民主共和国": "DR Congo",
        "科特迪瓦": "Ivory Coast",
        "韩国": "South Korea",
        "美国": "United States",
        "库拉索": "Curacao",
    }
)


class LyihubWorldCupScraper:
    name = "worldcup.lyihub.com static JSON"

    def __init__(self, base_url: str = LYIHUB_BASE):
        self.base_url = base_url.rstrip("/")

    def fetch_index(self) -> dict[str, Any]:
        response = httpx.get(f"{self.base_url}/data/index.json", timeout=30, follow_redirects=True)
        response.raise_for_status()
        return response.json()

    def fetch_match_detail(self, match_id: str | int) -> dict[str, Any]:
        response = httpx.get(
            f"{self.base_url}/data/matches/{match_id}.json",
            timeout=30,
            follow_redirects=True,
        )
        response.raise_for_status()
        return response.json()

    def normalize_index_match(self, match: dict[str, Any]) -> dict[str, Any]:
        score = _score(match)
        team_a = str(match.get("team_a") or "")
        team_b = str(match.get("team_b") or "")
        return {
            "id": f"lyihub-{match['match_id']}",
            "match_id": str(match["match_id"]),
            "date": _beijing_date(match.get("kickoff_at")),
            "kickoff": match.get("kickoff_at") or "",
            "home_team": canonical_team(team_a),
            "away_team": canonical_team(team_b),
            "home_team_zh_source": team_a,
            "away_team_zh_source": team_b,
            "home_team_id_source": str(match.get("team_a_id") or ""),
            "away_team_id_source": str(match.get("team_b_id") or ""),
            "group": match.get("stage"),
            "stage": match.get("stage"),
            "venue": match.get("venue"),
            "status": _status(match),
            "home_score": score.get("team_a"),
            "away_score": score.get("team_b"),
            "has_predict": bool(match.get("has_predict")),
            "source_url": f"{self.base_url}/match.html?id={match['match_id']}",
            "source_name": "lyihub_worldcup_static_json",
            "payload": match,
        }

    def normalize_detail(self, detail: dict[str, Any]) -> dict[str, Any]:
        match = detail.get("match") or {}
        normalized = self.normalize_index_match(match)
        normalized["detail"] = detail
        normalized["players"] = self.normalize_players(detail)
        return normalized

    def normalize_players(self, detail: dict[str, Any]) -> list[dict[str, Any]]:
        match = detail.get("match") or {}
        side_to_team = {
            "A": {
                "team_zh": match.get("team_a"),
                "team": canonical_team(str(match.get("team_a") or "")),
                "team_id_source": str(match.get("team_a_id") or ""),
            },
            "B": {
                "team_zh": match.get("team_b"),
                "team": canonical_team(str(match.get("team_b") or "")),
                "team_id_source": str(match.get("team_b_id") or ""),
            },
        }
        raw_players = detail.get("players") or {}
        if isinstance(raw_players, dict):
            iterable = raw_players.values()
        elif isinstance(raw_players, list):
            iterable = raw_players
        else:
            iterable = []

        players = []
        for player in iterable:
            if not isinstance(player, dict):
                continue
            side = str(player.get("team_side") or "")
            team_info = side_to_team.get(side, {})
            score10 = player.get("score10") if isinstance(player.get("score10"), dict) else {}
            players.append(
                {
                    "match_id": str(match.get("match_id") or ""),
                    "team": team_info.get("team") or str(player.get("team_name") or ""),
                    "team_zh": team_info.get("team_zh") or str(player.get("team_name") or ""),
                    "team_id_source": team_info.get("team_id_source") or str(player.get("team_id") or ""),
                    "player_id": str(player.get("player_id") or ""),
                    "player_name": player.get("player_name") or "",
                    "shirt_number": player.get("shirt_number"),
                    "position": player.get("position") or "",
                    "ability": _ability(score10),
                    "score10": score10,
                    "fitness": player.get("fitness") if isinstance(player.get("fitness"), dict) else {},
                    "payload": player,
                }
            )
        return [player for player in players if player["player_id"] and player["team"]]

    def validate(self) -> dict[str, Any]:
        checked_at = datetime.now(timezone.utc).isoformat()
        try:
            payload = self.fetch_index()
            matches = payload.get("matches") or []
            return {
                "name": self.name,
                "configured": True,
                "reachable": True,
                "auth_valid": True,
                "quota_remaining": None,
                "sample_count": len(matches),
                "last_error": None,
                "checked_at": checked_at,
            }
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            return {
                "name": self.name,
                "configured": True,
                "reachable": False,
                "auth_valid": False,
                "quota_remaining": None,
                "sample_count": 0,
                "last_error": str(exc),
                "checked_at": checked_at,
            }


def canonical_team(team_zh: str) -> str:
    return canonical_team_name(ZH_TO_CANONICAL.get(team_zh, team_zh))


def _score(match: dict[str, Any]) -> dict[str, int | None]:
    for key in ("score_90min", "score_90", "score_full", "score"):
        score = match.get(key)
        if isinstance(score, dict) and isinstance(score.get("team_a"), int) and isinstance(score.get("team_b"), int):
            return {"team_a": score["team_a"], "team_b": score["team_b"]}
    return {"team_a": None, "team_b": None}


def _status(match: dict[str, Any]) -> str:
    if _score(match).get("team_a") is not None:
        return "final"
    kickoff = _parse_datetime(match.get("kickoff_at"))
    if kickoff and kickoff <= datetime.now(timezone.utc):
        return "live"
    return "scheduled"


def _beijing_date(value: str | None) -> str:
    parsed = _parse_datetime(value)
    if not parsed:
        return datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    return parsed.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ability(score10: dict[str, Any]) -> float | None:
    explicit = _number(score10.get("评分"))
    if explicit is not None:
        return explicit
    values = [_number(value) for value in score10.values()]
    numeric_values = [value for value in values if value is not None]
    if not numeric_values:
        return None
    return round(sum(numeric_values) / len(numeric_values), 2)
