from __future__ import annotations

import math
import random
from collections import Counter
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MonteCarloConfig:
    simulations: int = 10_000
    seed: int = 20260625


SEGMENTS = [
    (0, 15, 0.82),
    (16, 45, 1.0),
    (46, 75, 1.08),
    (76, 94, 1.12),
]


def simulate_match(
    lambda_home: float,
    lambda_away: float,
    config: MonteCarloConfig | None = None,
) -> dict[str, Any]:
    config = config or MonteCarloConfig()
    simulations = max(100, int(config.simulations))
    rng = random.Random(config.seed + int(lambda_home * 1000) * 31 + int(lambda_away * 1000) * 17)
    scores: Counter[tuple[int, int]] = Counter()
    outcomes = Counter()
    totals_over = 0
    btts = 0
    home_goals_total = 0
    away_goals_total = 0
    home_goal_values: list[int] = []
    away_goal_values: list[int] = []
    scripts = Counter()
    example_events: list[dict[str, Any]] = []

    for sample_index in range(simulations):
        home_goals, away_goals, events, script = simulate_event_sequence(lambda_home, lambda_away, rng)
        scores[(home_goals, away_goals)] += 1
        home_goals_total += home_goals
        away_goals_total += away_goals
        home_goal_values.append(home_goals)
        away_goal_values.append(away_goals)
        totals_over += 1 if home_goals + away_goals > 2.5 else 0
        btts += 1 if home_goals > 0 and away_goals > 0 else 0
        scripts[script] += 1
        if sample_index == 0:
            example_events = events
        if home_goals > away_goals:
            outcomes["home"] += 1
        elif home_goals == away_goals:
            outcomes["draw"] += 1
        else:
            outcomes["away"] += 1

    home_probability = outcomes["home"] / simulations
    draw_probability = outcomes["draw"] / simulations
    away_probability = outcomes["away"] / simulations
    top_scores = [
        {"score": f"{home}-{away}", "probability": count / simulations}
        for (home, away), count in scores.most_common(6)
    ]
    return {
        "simulations": simulations,
        "home_win": home_probability,
        "draw": draw_probability,
        "away_win": away_probability,
        "confidence_interval": {
            "home_win": proportion_interval(home_probability, simulations),
            "draw": proportion_interval(draw_probability, simulations),
            "away_win": proportion_interval(away_probability, simulations),
        },
        "over_2_5": totals_over / simulations,
        "under_2_5": 1 - totals_over / simulations,
        "btts": btts / simulations,
        "scorelines": top_scores,
        "average_goals": {
            "home": home_goals_total / simulations,
            "away": away_goals_total / simulations,
            "total": (home_goals_total + away_goals_total) / simulations,
        },
        "goal_variance": {
            "home": variance(home_goal_values),
            "away": variance(away_goal_values),
            "total": variance([home + away for home, away in zip(home_goal_values, away_goal_values)]),
        },
        "typical_script": scripts.most_common(1)[0][0] if scripts else "balanced",
        "script_distribution": {key: value / simulations for key, value in scripts.items()},
        "sample_event_sequence": example_events,
    }


def simulate_event_sequence(lambda_home: float, lambda_away: float, rng: random.Random) -> tuple[int, int, list[dict[str, Any]], str]:
    events: list[dict[str, Any]] = []
    home_goals = away_goals = 0
    for start, end, base_multiplier in SEGMENTS:
        segment_minutes = max(1, end - start + 1)
        state_multiplier_home, state_multiplier_away, rhythm = state_multipliers(home_goals, away_goals, start)
        home_segment_lambda = lambda_home * (segment_minutes / 95) * base_multiplier * state_multiplier_home
        away_segment_lambda = lambda_away * (segment_minutes / 95) * base_multiplier * state_multiplier_away
        for side, count in (
            ("home", poisson_sample(home_segment_lambda, rng)),
            ("away", poisson_sample(away_segment_lambda, rng)),
        ):
            for _ in range(count):
                minute = rng.randint(start, end)
                events.append({"minute": minute, "team": side, "rhythm": rhythm})
                if side == "home":
                    home_goals += 1
                else:
                    away_goals += 1
    events.sort(key=lambda event: event["minute"])
    return home_goals, away_goals, events, classify_script(home_goals, away_goals, events)


def state_multipliers(home_goals: int, away_goals: int, minute: int) -> tuple[float, float, str]:
    goal_delta = home_goals - away_goals
    if minute < 75 or goal_delta == 0:
        return 1.0, 1.0, "balanced"
    if goal_delta > 0:
        return 0.86, 1.18, "conservative"
    return 1.18, 0.86, "aggressive"


def poisson_sample(expected_goals: float, rng: random.Random) -> int:
    threshold = math.exp(-max(0.0, expected_goals))
    count = 0
    product = 1.0
    while product > threshold:
        count += 1
        product *= rng.random()
    return max(0, count - 1)


def classify_script(home_goals: int, away_goals: int, events: list[dict[str, Any]]) -> str:
    total = home_goals + away_goals
    if total <= 1:
        return "低比分僵持"
    if events and events[0]["minute"] <= 20 and abs(home_goals - away_goals) >= 2:
        return "强队早进球后控场"
    if events and events[-1]["minute"] >= 76 and abs(home_goals - away_goals) <= 1:
        return "后段追分"
    if total >= 4:
        return "开放对攻"
    return "均衡拉锯"


def proportion_interval(probability: float, samples: int) -> dict[str, float]:
    standard_error = math.sqrt(max(0.0, probability * (1 - probability) / samples))
    return {
        "low": max(0.0, probability - 1.96 * standard_error),
        "high": min(1.0, probability + 1.96 * standard_error),
    }


def variance(values: list[int]) -> float:
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    return sum((value - mean) ** 2 for value in values) / len(values)
