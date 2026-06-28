from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .dixon_coles import btts_probability, outcome_probabilities, top_scorelines, totals_probability


@dataclass(frozen=True)
class PoissonModelConfig:
    avg_team_goals: float = 1.32
    max_goals: int = 6
    lookback_days: int = 365
    min_lambda: float = 0.25
    max_lambda: float = 3.8
    neutral_home_advantage: float = 0.0
    stage_pace: dict[str, float] = field(
        default_factory=lambda: {
            "group_round_1": 0.9,
            "group_round_2": 1.0,
            "group_round_3": 1.05,
            "round_of_32": 0.98,
            "round_of_16": 0.96,
            "quarter_final": 0.95,
            "semi_final": 0.94,
            "third_place": 1.08,
            "final": 0.92,
            "default": 1.0,
        }
    )


def estimate_poisson_prediction(
    fixture: dict[str, Any],
    home_profile: dict[str, Any],
    away_profile: dict[str, Any],
    recent_matches: list[dict[str, Any]],
    config: PoissonModelConfig | None = None,
) -> dict[str, Any]:
    config = config or PoissonModelConfig()
    home_team = fixture["home_team"]
    away_team = fixture["away_team"]
    stage_key = stage_bucket(str(fixture.get("group") or fixture.get("stage") or ""))
    pace = config.stage_pace.get(stage_key, config.stage_pace["default"])

    home_recent = team_recent_rates(home_team, recent_matches, config)
    away_recent = team_recent_rates(away_team, recent_matches, config)

    home_attack = blended_rate(
        home_recent["goals_for"],
        profile_rate(home_profile, "attack_rating", 1.45),
        home_recent["confidence"],
        config.avg_team_goals,
    )
    away_attack = blended_rate(
        away_recent["goals_for"],
        profile_rate(away_profile, "attack_rating", 1.45),
        away_recent["confidence"],
        config.avg_team_goals,
    )
    home_defense_allowed = blended_rate(
        home_recent["goals_against"],
        profile_rate(home_profile, "defense_rating", 1.05),
        home_recent["confidence"],
        config.avg_team_goals,
    )
    away_defense_allowed = blended_rate(
        away_recent["goals_against"],
        profile_rate(away_profile, "defense_rating", 1.05),
        away_recent["confidence"],
        config.avg_team_goals,
    )

    home_elo = float(home_profile.get("elo") or fixture.get("home_elo") or 1700)
    away_elo = float(away_profile.get("elo") or fixture.get("away_elo") or 1700)
    home_elo_factor = clamp(math.exp((home_elo + config.neutral_home_advantage - away_elo) / 950), 0.78, 1.28)
    away_elo_factor = clamp(math.exp((away_elo - home_elo - config.neutral_home_advantage) / 950), 0.78, 1.28)

    home_lambda = clamp(
        config.avg_team_goals
        * (home_attack / config.avg_team_goals)
        * (away_defense_allowed / config.avg_team_goals)
        * home_elo_factor
        * pace,
        config.min_lambda,
        config.max_lambda,
    )
    away_lambda = clamp(
        config.avg_team_goals
        * (away_attack / config.avg_team_goals)
        * (home_defense_allowed / config.avg_team_goals)
        * away_elo_factor
        * pace,
        config.min_lambda,
        config.max_lambda,
    )

    matrix = poisson_score_matrix(home_lambda, away_lambda, max_goals=config.max_goals)
    outcomes = outcome_probabilities(matrix)
    totals = totals_probability(matrix, 2.5)

    return {
        "lambda_home": round(home_lambda, 4),
        "lambda_away": round(away_lambda, 4),
        "home_win": outcomes.home,
        "draw": outcomes.draw,
        "away_win": outcomes.away,
        "over_2_5": totals["over"],
        "under_2_5": totals["under"],
        "btts": btts_probability(matrix),
        "scorelines": top_scorelines(matrix, limit=6),
        "score_matrix": serialize_matrix(matrix),
        "tail_probability": matrix.get((f"{config.max_goals + 1}+", f"{config.max_goals + 1}+"), 0.0),
        "model_explanation": {
            "stage_bucket": stage_key,
            "stage_pace": pace,
            "home_recent_sample": home_recent["weighted_matches"],
            "away_recent_sample": away_recent["weighted_matches"],
            "home_attack_rate": round(home_attack, 4),
            "away_attack_rate": round(away_attack, 4),
            "home_defense_allowed": round(home_defense_allowed, 4),
            "away_defense_allowed": round(away_defense_allowed, 4),
            "home_elo_factor": round(home_elo_factor, 4),
            "away_elo_factor": round(away_elo_factor, 4),
            "drivers": lambda_drivers(home_lambda, away_lambda, pace, home_elo_factor, away_elo_factor),
        },
    }


def poisson_score_matrix(home_lambda: float, away_lambda: float, max_goals: int = 6) -> dict[tuple[Any, Any], float]:
    matrix: dict[tuple[Any, Any], float] = {}
    finite_mass = 0.0
    for home_goals in range(max_goals + 1):
        for away_goals in range(max_goals + 1):
            probability = poisson_pmf(home_goals, home_lambda) * poisson_pmf(away_goals, away_lambda)
            matrix[(home_goals, away_goals)] = probability
            finite_mass += probability
    tail = max(0.0, 1 - finite_mass)
    if tail:
        matrix[(f"{max_goals + 1}+", f"{max_goals + 1}+")] = tail
    total = sum(matrix.values())
    return {score: probability / total for score, probability in matrix.items()}


def poisson_pmf(goal_count: int, expected_goals: float) -> float:
    return math.exp(-expected_goals) * (expected_goals**goal_count) / math.factorial(goal_count)


def team_recent_rates(team: str, matches: list[dict[str, Any]], config: PoissonModelConfig) -> dict[str, float]:
    now = latest_match_datetime(matches) or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=config.lookback_days)
    goals_for = goals_against = weight_sum = 0.0
    for match in matches:
        date = parse_date(match.get("date"))
        if not date or date < cutoff:
            continue
        if team not in {match.get("home_team"), match.get("away_team")}:
            continue
        home_score = match.get("home_score")
        away_score = match.get("away_score")
        if home_score is None or away_score is None:
            continue
        base_weight = competition_weight(str(match.get("tournament") or match.get("source_name") or ""))
        recency_days = max(0.0, (now - date).days)
        recency_weight = 0.5 ** (recency_days / 180)
        weight = base_weight * recency_weight
        if match.get("home_team") == team:
            goals_for += float(home_score) * weight
            goals_against += float(away_score) * weight
        else:
            goals_for += float(away_score) * weight
            goals_against += float(home_score) * weight
        weight_sum += weight
    if weight_sum <= 0:
        return {
            "goals_for": config.avg_team_goals,
            "goals_against": config.avg_team_goals,
            "weighted_matches": 0.0,
            "confidence": 0.0,
        }
    return {
        "goals_for": goals_for / weight_sum,
        "goals_against": goals_against / weight_sum,
        "weighted_matches": weight_sum,
        "confidence": min(1.0, weight_sum / 8),
    }


def competition_weight(label: str) -> float:
    normalized = label.lower()
    if "lyihub" in normalized or "world cup" in normalized or "世界杯" in normalized:
        return 3.0
    if "qualif" in normalized or "nations" in normalized:
        return 1.6
    if "friendly" in normalized:
        return 1.0
    return 1.25


def blended_rate(recent_rate: float, profile_rate_value: float, confidence: float, fallback: float) -> float:
    profile = profile_rate_value if profile_rate_value > 0 else fallback
    return confidence * recent_rate + (1 - confidence) * profile


def profile_rate(profile: dict[str, Any], key: str, default: float) -> float:
    value = float(profile.get(key) or default)
    if key == "attack_rating":
        return clamp(value, 0.4, 3.2)
    return clamp(value, 0.35, 2.8)


def stage_bucket(stage: str) -> str:
    if "第1轮" in stage:
        return "group_round_1"
    if "第2轮" in stage:
        return "group_round_2"
    if "第3轮" in stage:
        return "group_round_3"
    if "1/16" in stage:
        return "round_of_32"
    if "1/8" in stage:
        return "round_of_16"
    if "1/4" in stage:
        return "quarter_final"
    if "半决赛" in stage:
        return "semi_final"
    if "季军" in stage:
        return "third_place"
    if "决赛" in stage or "final" in stage.lower():
        return "final"
    return "default"


def lambda_drivers(
    home_lambda: float,
    away_lambda: float,
    pace: float,
    home_elo_factor: float,
    away_elo_factor: float,
) -> list[str]:
    drivers = []
    if pace < 0.96:
        drivers.append("赛事节奏系数偏保守，整体进球期望下调")
    elif pace > 1.03:
        drivers.append("赛事节奏系数偏开放，整体进球期望上调")
    if home_elo_factor > 1.05:
        drivers.append("主队 Elo 相对优势推高主队 λ")
    if away_elo_factor > 1.05:
        drivers.append("客队 Elo 相对优势推高客队 λ")
    if max(home_lambda, away_lambda) >= 2.0:
        drivers.append("至少一方进攻/对手防守组合支持较高比分尾部")
    if not drivers:
        drivers.append("双方近况、强弱和赛程节奏接近均衡")
    return drivers


def latest_match_datetime(matches: list[dict[str, Any]]) -> datetime | None:
    dates = [parse_date(match.get("date")) for match in matches]
    valid = [date for date in dates if date is not None]
    return max(valid) if valid else None


def parse_date(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        try:
            return datetime.fromisoformat(f"{value}T00:00:00+00:00").astimezone(timezone.utc)
        except ValueError:
            return None


def serialize_matrix(matrix: dict[tuple[Any, Any], float]) -> list[dict[str, Any]]:
    return [
        {"home_goals": home, "away_goals": away, "probability": probability}
        for (home, away), probability in matrix.items()
    ]


def clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))
