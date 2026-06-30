from __future__ import annotations

from typing import Any

from .market import kelly_fraction, market_from_decimal_odds


OUTCOMES = ("home", "draw", "away")


def parse_handicap_line(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("[", "").replace("]", "")
    if not text or text in {"--", "未"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def handicap_outcome(home_goals: int, away_goals: int, handicap: float) -> str:
    adjusted_goal_diff = home_goals + handicap - away_goals
    if abs(adjusted_goal_diff) < 1e-9:
        return "draw"
    return "home" if adjusted_goal_diff > 0 else "away"


def handicap_probabilities(
    matrix: dict[tuple[Any, Any], float],
    handicap: float,
) -> dict[str, Any]:
    probabilities = {outcome: 0.0 for outcome in OUTCOMES}
    tail_probability = 0.0
    visible_probability = 0.0
    regions: list[dict[str, Any]] = []

    for (home_goals, away_goals), probability in matrix.items():
        probability = float(probability)
        if isinstance(home_goals, int) and isinstance(away_goals, int):
            visible_probability += probability
            outcome = handicap_outcome(home_goals, away_goals, handicap)
            probabilities[outcome] += probability
            regions.append(
                {
                    "home_goals": home_goals,
                    "away_goals": away_goals,
                    "outcome": outcome,
                    "probability": round(probability, 8),
                }
            )
        else:
            tail_probability += probability

    if tail_probability and visible_probability:
        for outcome in OUTCOMES:
            probabilities[outcome] += tail_probability * probabilities[outcome] / visible_probability

    return {
        "available": True,
        "line": handicap,
        "probabilities": {key: round(value, 6) for key, value in probabilities.items()},
        "tail_probability": round(tail_probability, 6),
        "tail_note": "8+ tail 按可见比分让球分布比例分摊。" if tail_probability else None,
        "regions": regions,
    }


def handicap_market_from_odds(market: dict[str, Any]) -> dict[str, Any] | None:
    if not market or not market.get("available"):
        return None
    odds = market.get("odds") or {}
    if not all(odds.get(outcome) for outcome in OUTCOMES):
        return None
    return market_from_decimal_odds(
        {outcome: odds[outcome] for outcome in OUTCOMES},
        provider=str(market.get("provider") or "sporttery"),
        bookmaker=market.get("bookmaker"),
        market_key=str(market.get("market_key") or "handicap_1x2"),
    )


def handicap_value_analysis(
    model_probabilities: dict[str, float],
    market: dict[str, Any] | None,
) -> dict[str, Any]:
    if not market or not market.get("available"):
        return {
            "available": False,
            "items": [],
            "recommended_options": [],
            "summary": "让球胜平负盘口不可用。",
        }
    market_probabilities = market.get("market_probability_no_vig") or market.get("implied_probability_no_vig") or {}
    odds = market.get("odds") or {}
    items = []
    for outcome in OUTCOMES:
        model_probability = float(model_probabilities.get(outcome, 0.0))
        market_probability = float(market_probabilities.get(outcome, 0.0))
        edge = model_probability - market_probability
        full_kelly = kelly_fraction(model_probability, float(odds.get(outcome, 0.0)))
        if edge > 0.08 and full_kelly > 0:
            label = "强推荐"
            recommendation = "小仓位候选"
        elif edge > 0.05 and full_kelly > 0:
            label = "候选推荐"
            recommendation = "观察"
        elif edge < 0.03 or full_kelly <= 0:
            label = "不推荐"
            recommendation = "不下注"
        else:
            label = "市场共识"
            recommendation = "不下注"
        items.append(
            {
                "outcome": outcome,
                "model_probability": round(model_probability, 6),
                "market_probability": round(market_probability, 6),
                "edge": round(edge, 6),
                "decimal_odds": odds.get(outcome),
                "label": label,
                "recommendation": recommendation,
                "kelly": {
                    "full": round(full_kelly, 6),
                    "half": round(full_kelly * 0.5, 6),
                    "quarter": round(full_kelly * 0.25, 6),
                },
            }
        )
    recommended = [item for item in items if item["label"] in {"强推荐", "候选推荐"}]
    summary = "暂无明显价值，模型与市场基本一致。"
    if recommended:
        best = max(recommended, key=lambda item: item["edge"])
        summary = f"让球{_cn_outcome(best['outcome'])} Edge {best['edge']:.1%}，Kelly {best['kelly']['full']:.1%}。"
    return {
        "available": True,
        "line": market.get("line"),
        "items": items,
        "recommended_options": recommended,
        "summary": summary,
    }


def _cn_outcome(outcome: str) -> str:
    return {"home": "胜", "draw": "平", "away": "负"}.get(outcome, outcome)
