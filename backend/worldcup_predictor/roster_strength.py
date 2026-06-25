from __future__ import annotations

from statistics import mean
from typing import Any


TIER_ONE_LEAGUES = {
    "premier league",
    "la liga",
    "bundesliga",
    "serie a",
    "ligue 1",
    "uefa champions league",
    "champions league",
}

TIER_TWO_LEAGUES = {
    "primeira liga",
    "eredivisie",
    "jupiler pro league",
    "championship",
    "major league soccer",
    "mls",
    "serie a brazil",
    "brasileiro serie a",
    "primera division",
    "argentine primera division",
    "saudi pro league",
    "super lig",
}

USABLE_STATUSES = {"complete", "enriched"}


def league_tier_score(league: str | None) -> float:
    normalized = (league or "").strip().lower()
    if normalized in TIER_ONE_LEAGUES:
        return 95.0
    if normalized in TIER_TWO_LEAGUES:
        return 80.0
    if normalized:
        return 65.0
    return 50.0


def player_strength(stats: dict[str, Any]) -> float:
    rating = _float(stats.get("rating"))
    rating_score = _clamp((rating or 6.2) * 10, 45, 95)
    league_score = league_tier_score(stats.get("league"))
    rank_score = _club_rank_score(stats.get("club_rank"))
    stability_score = _stability_score(stats)
    position_score = _position_specific_score(stats)
    value = (
        rating_score * 0.35
        + league_score * 0.25
        + rank_score * 0.15
        + stability_score * 0.15
        + position_score * 0.10
    )
    return round(_clamp(value, 0, 100), 2)


def aggregate_team_strength(team: str, players: list[dict[str, Any]]) -> dict[str, Any]:
    attack = [_strength(player) for player in players if position_bucket(player.get("position")) == "attack"]
    midfield = [_strength(player) for player in players if position_bucket(player.get("position")) == "midfield"]
    defense = [_strength(player) for player in players if position_bucket(player.get("position")) == "defense_gk"]
    completed = [player for player in players if player.get("stats_status") in USABLE_STATUSES]
    total = len(players)
    return {
        "team": team,
        "attack_strength": round(mean(attack), 2) if attack else 65.0,
        "midfield_control_strength": round(mean(midfield), 2) if midfield else 65.0,
        "defense_gk_strength": round(mean(defense), 2) if defense else 65.0,
        "coverage": round(len(completed) / total, 4) if total else 0.0,
        "missing_player_stats": max(0, total - len(completed)),
        "model_version": "roster-strength-v1",
    }


def position_bucket(position: str | None) -> str:
    normalized = (position or "").strip().lower()
    if any(token in normalized for token in ("attacker", "forward", "winger", "striker")):
        return "attack"
    if any(token in normalized for token in ("midfielder", "midfield", "centre mid", "defensive mid")):
        return "midfield"
    if any(token in normalized for token in ("defender", "back", "goalkeeper", "keeper")):
        return "defense_gk"
    return "midfield"


def _position_specific_score(stats: dict[str, Any]) -> float:
    bucket = position_bucket(stats.get("position"))
    minutes = max(_float(stats.get("minutes")) or 0, 1)
    per90 = 90 / minutes
    if bucket == "attack":
        output = ((_float(stats.get("goals")) or 0) + 0.7 * (_float(stats.get("assists")) or 0)) * per90
        return _clamp(55 + output * 55, 45, 100)
    if bucket == "midfield":
        passes = (_float(stats.get("passes")) or 0) * per90
        assists = (_float(stats.get("assists")) or 0) * per90
        return _clamp(55 + passes * 0.55 + assists * 35, 45, 100)
    saves = (_float(stats.get("saves")) or 0) * per90
    defensive_actions = ((_float(stats.get("tackles")) or 0) + (_float(stats.get("interceptions")) or 0)) * per90
    return _clamp(55 + saves * 8 + defensive_actions * 10, 45, 100)


def _club_rank_score(value: Any) -> float:
    rank = _float(value)
    if not rank:
        return 58.0
    return _clamp(102 - (rank * 3.0), 45, 100)


def _stability_score(stats: dict[str, Any]) -> float:
    appearances = _float(stats.get("appearances")) or 0
    starts = _float(stats.get("starts")) or 0
    minutes = _float(stats.get("minutes")) or 0
    return round(
        _clamp(
            45 + min(1, appearances / 34) * 20 + min(1, starts / 30) * 18 + min(1, minutes / 2700) * 17,
            40,
            100,
        ),
        2,
    )


def _strength(player: dict[str, Any]) -> float:
    if player.get("player_strength") is not None:
        return float(player["player_strength"])
    if player.get("stats_status") not in USABLE_STATUSES:
        return 65.0
    return player_strength(player)


def _float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))
