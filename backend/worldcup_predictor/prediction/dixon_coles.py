from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Hashable

ScoreKey = tuple[Hashable, Hashable]


@dataclass(frozen=True)
class OutcomeProbabilities:
    home: float
    draw: float
    away: float


def _poisson(goal_count: int, expected_goals: float) -> float:
    return math.exp(-expected_goals) * (expected_goals**goal_count) / math.factorial(goal_count)


def _dc_tau(home_goals: int, away_goals: int, home_xg: float, away_xg: float, rho: float) -> float:
    if home_goals == 0 and away_goals == 0:
        return 1 - home_xg * away_xg * rho
    if home_goals == 0 and away_goals == 1:
        return 1 + home_xg * rho
    if home_goals == 1 and away_goals == 0:
        return 1 + away_xg * rho
    if home_goals == 1 and away_goals == 1:
        return 1 - rho
    return 1.0


def score_matrix(
    home_xg: float,
    away_xg: float,
    rho: float = -0.06,
    max_goals: int = 7,
) -> dict[ScoreKey, float]:
    finite: dict[ScoreKey, float] = {}
    for home_goals in range(max_goals + 1):
        for away_goals in range(max_goals + 1):
            base = _poisson(home_goals, home_xg) * _poisson(away_goals, away_xg)
            corrected = base * max(0.01, _dc_tau(home_goals, away_goals, home_xg, away_xg, rho))
            finite[(home_goals, away_goals)] = corrected

    finite_mass = sum(finite.values())
    independent_finite_mass = sum(
        _poisson(h, home_xg) * _poisson(a, away_xg)
        for h in range(max_goals + 1)
        for a in range(max_goals + 1)
    )
    tail_mass = max(0.0, 1 - independent_finite_mass)
    total = finite_mass + tail_mass
    matrix = {score: probability / total for score, probability in finite.items()}
    if tail_mass:
        matrix[(f"{max_goals + 1}+", f"{max_goals + 1}+")] = tail_mass / total
    return matrix


def outcome_probabilities(matrix: dict[ScoreKey, float]) -> OutcomeProbabilities:
    home = draw = away = 0.0
    tail = 0.0
    for (home_goals, away_goals), probability in matrix.items():
        if not isinstance(home_goals, int) or not isinstance(away_goals, int):
            tail += probability
        elif home_goals > away_goals:
            home += probability
        elif home_goals == away_goals:
            draw += probability
        else:
            away += probability

    visible_total = home + draw + away
    if tail and visible_total:
        home += tail * home / visible_total
        draw += tail * draw / visible_total
        away += tail * away / visible_total
    return OutcomeProbabilities(home=home, draw=draw, away=away)


def expected_goals(matrix: dict[ScoreKey, float]) -> dict[str, float]:
    home_xg = away_xg = visible_mass = 0.0
    for (home_goals, away_goals), probability in matrix.items():
        if isinstance(home_goals, int) and isinstance(away_goals, int):
            home_xg += home_goals * probability
            away_xg += away_goals * probability
            visible_mass += probability
    scale = 1 / visible_mass if visible_mass else 1.0
    return {"home": home_xg * scale, "away": away_xg * scale}


def top_scorelines(matrix: dict[ScoreKey, float], limit: int = 6) -> list[dict[str, float | str]]:
    finite_scores = [
        {"score": f"{home}-{away}", "probability": probability}
        for (home, away), probability in matrix.items()
        if isinstance(home, int) and isinstance(away, int)
    ]
    return sorted(finite_scores, key=lambda row: row["probability"], reverse=True)[:limit]


def btts_probability(matrix: dict[ScoreKey, float]) -> float:
    return sum(
        probability
        for (home, away), probability in matrix.items()
        if isinstance(home, int) and isinstance(away, int) and home > 0 and away > 0
    )


def totals_probability(matrix: dict[ScoreKey, float], line: float) -> dict[str, float]:
    over = sum(
        probability
        for (home, away), probability in matrix.items()
        if isinstance(home, int) and isinstance(away, int) and home + away > line
    )
    return {"over": over, "under": max(0.0, 1 - over)}
