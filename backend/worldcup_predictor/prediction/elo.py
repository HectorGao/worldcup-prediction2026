from __future__ import annotations

import math


def expected_score(home_rating: float, away_rating: float, home_advantage: float = 60) -> float:
    rating_delta = (away_rating - (home_rating + home_advantage)) / 400
    return 1 / (1 + math.pow(10, rating_delta))


def match_score(home_goals: int, away_goals: int) -> float:
    if home_goals > away_goals:
        return 1.0
    if home_goals == away_goals:
        return 0.5
    return 0.0


def update_elo(
    home_rating: float,
    away_rating: float,
    home_goals: int,
    away_goals: int,
    k: float = 24,
    home_advantage: float = 60,
) -> tuple[float, float]:
    expected_home = expected_score(home_rating, away_rating, home_advantage=home_advantage)
    actual_home = match_score(home_goals, away_goals)
    goal_delta = abs(home_goals - away_goals)
    margin_multiplier = math.log(goal_delta + 1) + 1 if goal_delta else 1.0
    change = k * margin_multiplier * (actual_home - expected_home)
    return home_rating + change, away_rating - change
