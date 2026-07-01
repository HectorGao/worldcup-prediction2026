from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


OUTCOMES = ("home", "draw", "away")


@dataclass(frozen=True)
class EnsembleConfig:
    weights: dict[str, float] = field(
        default_factory=lambda: {
            "elo": 0.20,
            "poisson": 0.20,
            "monte_carlo": 0.20,
            "market": 0.20,
            "xgboost": 0.20,
        }
    )


def blend_probabilities(
    *,
    elo: dict[str, float],
    poisson: dict[str, Any],
    monte_carlo: dict[str, Any],
    market: dict[str, Any] | None = None,
    xgboost: dict[str, Any] | None = None,
    config: EnsembleConfig | None = None,
) -> dict[str, Any]:
    config = config or EnsembleConfig()
    sources = {
        "elo": normalize_outcomes(elo),
        "poisson": normalize_outcomes(extract_probabilities(poisson)),
        "monte_carlo": normalize_outcomes(extract_probabilities(monte_carlo)),
    }
    market_probabilities = (
        market.get("market_probability_no_vig") or market.get("implied_probability_no_vig") or {}
        if market
        else {}
    )
    if market and market.get("available") and market_probabilities:
        sources["market"] = normalize_outcomes(market_probabilities)
    if xgboost:
        sources["xgboost"] = normalize_outcomes(extract_probabilities(xgboost))

    weights = {
        name: float(config.weights.get(name, 0.0))
        for name in sources
        if float(config.weights.get(name, 0.0)) > 0
    }
    weight_total = sum(weights.values())
    if weight_total <= 0:
        weights = {name: 1.0 for name in sources}
        weight_total = float(len(weights))
    normalized_weights = {name: weight / weight_total for name, weight in weights.items()}

    blended = {
        outcome: sum(sources[name].get(outcome, 0.0) * weight for name, weight in normalized_weights.items())
        for outcome in OUTCOMES
    }
    blended = normalize_outcomes(blended)
    ranked = sorted(blended.items(), key=lambda item: item[1], reverse=True)
    confidence = confidence_label(ranked)
    return {
        **blended,
        "recommended_result": outcome_label(ranked[0][0]) if ranked else "未知",
        "confidence": confidence,
        "weights": normalized_weights,
        "source_probabilities": sources,
        "market_included": "market" in sources,
    }


def blend_model_probabilities(
    poisson_prob: dict[str, float] | None,
    monte_carlo_prob: dict[str, float] | None,
    xgboost_prob: dict[str, float] | None,
    weights: dict[str, float] | None = None,
) -> dict[str, float]:
    configured = weights or {"poisson": 0.4, "monte_carlo": 0.3, "xgboost": 0.3}
    sources = {
        "poisson": normalize_outcomes(extract_probabilities(poisson_prob or {})) if poisson_prob else None,
        "monte_carlo": normalize_outcomes(extract_probabilities(monte_carlo_prob or {})) if monte_carlo_prob else None,
        "xgboost": normalize_outcomes(extract_probabilities(xgboost_prob or {})) if xgboost_prob else None,
    }
    available = {name: probs for name, probs in sources.items() if probs}
    if not available:
        return normalize_outcomes({})
    active_weights = {
        name: max(0.0, float(configured.get(name, 0.0)))
        for name in available
    }
    total = sum(active_weights.values())
    if total <= 0:
        active_weights = {name: 1.0 for name in available}
        total = float(len(available))
    return normalize_outcomes(
        {
            outcome: sum(available[name][outcome] * (active_weights[name] / total) for name in available)
            for outcome in OUTCOMES
        }
    )


def extract_probabilities(payload: dict[str, Any]) -> dict[str, float]:
    if "home" in payload:
        return {outcome: float(payload.get(outcome, 0.0)) for outcome in OUTCOMES}
    if "home_win" in payload:
        return {
            "home": float(payload.get("home_win", 0.0)),
            "draw": float(payload.get("draw", 0.0)),
            "away": float(payload.get("away_win", 0.0)),
        }
    probabilities = payload.get("probabilities")
    if isinstance(probabilities, dict):
        return extract_probabilities(probabilities)
    return {outcome: 0.0 for outcome in OUTCOMES}


def normalize_outcomes(probabilities: dict[str, float]) -> dict[str, float]:
    total = sum(max(0.0, float(probabilities.get(outcome, 0.0))) for outcome in OUTCOMES)
    if total <= 0:
        return {outcome: 1 / len(OUTCOMES) for outcome in OUTCOMES}
    return {
        outcome: max(0.0, float(probabilities.get(outcome, 0.0))) / total
        for outcome in OUTCOMES
    }


def confidence_label(ranked: list[tuple[str, float]]) -> str:
    if not ranked:
        return "低"
    top = ranked[0][1]
    spread = top - (ranked[1][1] if len(ranked) > 1 else 0.0)
    if top >= 0.55 and spread >= 0.12:
        return "高"
    if top >= 0.45 and spread >= 0.06:
        return "中"
    return "低"


def outcome_label(outcome: str) -> str:
    return {"home": "主胜", "draw": "平局", "away": "客胜"}.get(outcome, outcome)
