from __future__ import annotations

import math


def actual_outcome(home_goals: int, away_goals: int) -> str:
    if home_goals > away_goals:
        return "home"
    if home_goals == away_goals:
        return "draw"
    return "away"


def evaluate_result(
    probabilities: dict[str, float],
    home_goals: int,
    away_goals: int,
) -> dict[str, float | str]:
    outcome = actual_outcome(home_goals, away_goals)
    targets = {key: 1.0 if key == outcome else 0.0 for key in ("home", "draw", "away")}
    brier = sum((probabilities[key] - targets[key]) ** 2 for key in targets) / 3
    predicted = max(min(probabilities[outcome], 1 - 1e-15), 1e-15)
    return {
        "actual_outcome": outcome,
        "brier_score": brier,
        "log_loss": -math.log(predicted),
    }
