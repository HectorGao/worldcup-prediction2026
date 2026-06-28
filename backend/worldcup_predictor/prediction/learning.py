from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class LearningConfig:
    max_lambda_shift: float = 0.18
    min_weighted_matches: float = 1.0


def rolling_worldcup_adjustment(
    *,
    fixture: dict[str, Any],
    completed_matches: list[dict[str, Any]],
    config: LearningConfig | None = None,
) -> dict[str, Any]:
    config = config or LearningConfig()
    fixture_date = parse_date(fixture.get("date") or fixture.get("kickoff"))
    usable = [
        match
        for match in completed_matches
        if match.get("home_score") is not None
        and match.get("away_score") is not None
        and is_worldcup_match(match)
        and parse_date(match.get("date") or match.get("kickoff")) is not None
        and fixture_date is not None
        and parse_date(match.get("date") or match.get("kickoff")) < fixture_date
    ]
    team_form = {}
    for match in usable:
        date = parse_date(match.get("date") or match.get("kickoff"))
        recency_weight = 1.0
        if date and fixture_date:
            recency_weight = 0.5 ** (max(0, (fixture_date - date).days) / 12)
        add_team_form(team_form, match["home_team"], float(match["home_score"]), float(match["away_score"]), recency_weight)
        add_team_form(team_form, match["away_team"], float(match["away_score"]), float(match["home_score"]), recency_weight)

    for team, form in team_form.items():
        weight = form["weighted_matches"]
        if weight <= 0:
            form["goal_delta"] = 0.0
            form["lambda_multiplier"] = 1.0
            continue
        goals_for = form["goals_for"] / weight
        goals_against = form["goals_against"] / weight
        goal_delta = (goals_for - goals_against) / 2.2
        confidence = min(1.0, weight / 4)
        shift = max(-config.max_lambda_shift, min(config.max_lambda_shift, goal_delta * confidence))
        form["goals_for_per_match"] = round(goals_for, 4)
        form["goals_against_per_match"] = round(goals_against, 4)
        form["goal_delta"] = round(goal_delta * confidence, 4)
        form["lambda_multiplier"] = round(1 + shift, 4)

    home_team = fixture.get("home_team")
    away_team = fixture.get("away_team")
    home_multiplier = (team_form.get(home_team) or {}).get("lambda_multiplier", 1.0)
    away_multiplier = (team_form.get(away_team) or {}).get("lambda_multiplier", 1.0)
    return {
        "available": bool(usable),
        "fixture_date": fixture.get("date"),
        "completed_match_count": len(usable),
        "data_leakage_guard": "uses completed World Cup matches strictly before fixture date",
        "team_form_adjustment": team_form,
        "home_lambda_multiplier": home_multiplier,
        "away_lambda_multiplier": away_multiplier,
    }


def apply_learning_to_lambdas(
    home_lambda: float,
    away_lambda: float,
    adjustment: dict[str, Any],
    *,
    min_lambda: float,
    max_lambda: float,
) -> tuple[float, float]:
    home_multiplier = float(adjustment.get("home_lambda_multiplier") or 1.0)
    away_multiplier = float(adjustment.get("away_lambda_multiplier") or 1.0)
    return (
        max(min_lambda, min(max_lambda, home_lambda * home_multiplier)),
        max(min_lambda, min(max_lambda, away_lambda * away_multiplier)),
    )


def add_team_form(
    team_form: dict[str, dict[str, float]],
    team: str,
    goals_for: float,
    goals_against: float,
    weight: float,
) -> None:
    form = team_form.setdefault(
        team,
        {"weighted_matches": 0.0, "goals_for": 0.0, "goals_against": 0.0},
    )
    form["weighted_matches"] += weight
    form["goals_for"] += goals_for * weight
    form["goals_against"] += goals_against * weight


def is_worldcup_match(match: dict[str, Any]) -> bool:
    label = f"{match.get('tournament', '')} {match.get('source_name', '')} {match.get('stage', '')}".lower()
    return "world cup" in label or "世界杯" in label or "lyihub" in label


def parse_date(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        try:
            return datetime.fromisoformat(f"{value}T00:00:00").replace(tzinfo=None)
        except ValueError:
            return None
