from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from datetime import date, datetime
from io import StringIO
from typing import Iterable


@dataclass(frozen=True)
class HistoricalMatch:
    date: str
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    tournament: str
    neutral: bool = False


def parse_bool(value: str | bool) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def parse_historical_csv(text: str) -> list[HistoricalMatch]:
    rows = csv.DictReader(StringIO(text))
    matches: list[HistoricalMatch] = []
    for row in rows:
        if not row.get("home_score") or not row.get("away_score"):
            continue
        if not str(row["home_score"]).isdigit() or not str(row["away_score"]).isdigit():
            continue
        matches.append(
            HistoricalMatch(
                date=row["date"],
                home_team=row["home_team"],
                away_team=row["away_team"],
                home_score=int(row["home_score"]),
                away_score=int(row["away_score"]),
                tournament=row.get("tournament") or "Unknown",
                neutral=parse_bool(row.get("neutral", "false")),
            )
        )
    return matches


def time_decay_weight(match_date: str, as_of: str, half_life_years: float = 5.0) -> float:
    played = date.fromisoformat(match_date)
    current = date.fromisoformat(as_of)
    age_years = max(0, (current - played).days / 365.25)
    return math.pow(0.5, age_years / half_life_years)


def _result(home_score: int, away_score: int) -> float:
    if home_score > away_score:
        return 1.0
    if home_score == away_score:
        return 0.5
    return 0.0


def _goal_multiplier(home_score: int, away_score: int) -> float:
    margin = abs(home_score - away_score)
    if margin <= 1:
        return 1.0
    if margin == 2:
        return 1.5
    return (11 + margin) / 8


def _importance(tournament: str) -> float:
    lower = tournament.lower()
    if "world cup" in lower and "qualification" not in lower:
        return 60
    if "continental" in lower or "euro" in lower or "copa" in lower or "african cup" in lower:
        return 50
    if "qualification" in lower or "qualifier" in lower:
        return 40
    if "friendly" in lower:
        return 20
    return 30


def build_team_profiles(
    matches: Iterable[HistoricalMatch],
    as_of: str | None = None,
    half_life_years: float = 5.0,
    max_age_years: float | None = None,
) -> dict[str, dict]:
    as_of = as_of or datetime.utcnow().date().isoformat()
    ratings: dict[str, float] = {}
    profiles: dict[str, dict] = {}

    for match in sorted(matches, key=lambda item: item.date):
        if max_age_years is not None and _age_years(match.date, as_of) > max_age_years:
            continue
        ratings.setdefault(match.home_team, 1500.0)
        ratings.setdefault(match.away_team, 1500.0)
        profiles.setdefault(match.home_team, _empty_profile())
        profiles.setdefault(match.away_team, _empty_profile())

        weight = time_decay_weight(match.date, as_of, half_life_years)
        expected_home = 1 / (1 + math.pow(10, (ratings[match.away_team] - ratings[match.home_team]) / 400))
        actual_home = _result(match.home_score, match.away_score)
        k = _importance(match.tournament) * weight
        change = k * _goal_multiplier(match.home_score, match.away_score) * (actual_home - expected_home)
        ratings[match.home_team] += change
        ratings[match.away_team] -= change

        _accumulate_profile(profiles[match.home_team], match, weight, is_home=True)
        _accumulate_profile(profiles[match.away_team], match, weight, is_home=False)
        _accumulate_h2h(profiles[match.home_team], match.away_team, match, weight, is_home=True)
        _accumulate_h2h(profiles[match.away_team], match.home_team, match, weight, is_home=False)

    for team, profile in profiles.items():
        profile["team"] = team
        profile["elo"] = round(ratings.get(team, 1500.0), 1)
        matches_weight = profile["recent_weighted_matches"] or 1.0
        profile["attack_rating"] = round(profile["weighted_goals_for"] / matches_weight, 3)
        profile["defense_rating"] = round(profile["weighted_goals_against"] / matches_weight, 3)
    return profiles


def _age_years(match_date: str, as_of: str) -> float:
    played = date.fromisoformat(match_date)
    current = date.fromisoformat(as_of)
    return max(0, (current - played).days / 365.25)


def _empty_profile() -> dict:
    return {
        "recent_weighted_matches": 0.0,
        "weighted_goals_for": 0.0,
        "weighted_goals_against": 0.0,
        "wins": 0,
        "draws": 0,
        "losses": 0,
        "h2h": {},
    }


def _accumulate_profile(profile: dict, match: HistoricalMatch, weight: float, is_home: bool) -> None:
    gf = match.home_score if is_home else match.away_score
    ga = match.away_score if is_home else match.home_score
    profile["recent_weighted_matches"] += weight
    profile["weighted_goals_for"] += gf * weight
    profile["weighted_goals_against"] += ga * weight
    if gf > ga:
        profile["wins"] += 1
    elif gf == ga:
        profile["draws"] += 1
    else:
        profile["losses"] += 1


def _accumulate_h2h(
    profile: dict,
    opponent: str,
    match: HistoricalMatch,
    weight: float,
    is_home: bool,
) -> None:
    record = profile["h2h"].setdefault(
        opponent,
        {"matches": 0, "weighted_matches": 0.0, "wins": 0, "draws": 0, "losses": 0},
    )
    gf = match.home_score if is_home else match.away_score
    ga = match.away_score if is_home else match.home_score
    record["matches"] += 1
    record["weighted_matches"] += weight
    if gf > ga:
        record["wins"] += 1
    elif gf == ga:
        record["draws"] += 1
    else:
        record["losses"] += 1
