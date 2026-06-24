from __future__ import annotations

from .dixon_coles import ScoreKey, outcome_probabilities


def _outcome_for_score(score: ScoreKey) -> str | None:
    home_goals, away_goals = score
    if not isinstance(home_goals, int) or not isinstance(away_goals, int):
        return None
    if home_goals > away_goals:
        return "home"
    if home_goals == away_goals:
        return "draw"
    return "away"


def calibrate_score_matrix_to_market(
    matrix: dict[ScoreKey, float],
    market_probabilities: dict[str, float],
    totals_market: dict[str, float] | None = None,
    weight: float = 0.35,
) -> dict[ScoreKey, float]:
    current = outcome_probabilities(matrix)
    current_by_outcome = {"home": current.home, "draw": current.draw, "away": current.away}
    market_total = sum(market_probabilities.values())
    if market_total <= 0:
        return matrix
    market = {key: value / market_total for key, value in market_probabilities.items()}

    adjusted: dict[ScoreKey, float] = {}
    for score, probability in matrix.items():
        outcome = _outcome_for_score(score)
        if outcome is None:
            adjusted[score] = probability
            continue
        target = (1 - weight) * current_by_outcome[outcome] + weight * market[outcome]
        ratio = target / current_by_outcome[outcome] if current_by_outcome[outcome] else 1.0
        adjusted[score] = probability * ratio

    if totals_market:
        line = float(totals_market.get("line", 2.5))
        over_target = float(totals_market.get("over", 0))
        under_target = float(totals_market.get("under", 0))
        target_total = over_target + under_target
        if target_total > 0:
            over_target = over_target / target_total
            current_over = sum(
                probability
                for (home, away), probability in adjusted.items()
                if isinstance(home, int) and isinstance(away, int) and home + away > line
            )
            current_under = max(0.0, 1 - current_over)
            blended_over = (1 - weight) * current_over + weight * over_target
            blended_under = 1 - blended_over
            for score, probability in list(adjusted.items()):
                home, away = score
                if not isinstance(home, int) or not isinstance(away, int):
                    continue
                if home + away > line and current_over:
                    adjusted[score] = probability * (blended_over / current_over)
                elif home + away <= line and current_under:
                    adjusted[score] = probability * (blended_under / current_under)

    total = sum(adjusted.values())
    return {score: probability / total for score, probability in adjusted.items()}
