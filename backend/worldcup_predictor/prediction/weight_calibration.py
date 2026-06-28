from __future__ import annotations

import math
from typing import Any

from .ensemble import OUTCOMES, normalize_outcomes
from .metrics import actual_outcome


DEFAULT_WEIGHTS = {
    "elo": 0.20,
    "poisson": 0.20,
    "monte_carlo": 0.20,
    "market": 0.20,
    "xgboost": 0.20,
}


def default_weight_run(as_of: str, reason: str = "insufficient completed prediction samples") -> dict[str, Any]:
    return {
        "date": as_of,
        "weights": dict(DEFAULT_WEIGHTS),
        "sample_count": 0,
        "model_losses": {},
        "reason": reason,
        "model_version": "ensemble-weight-calibration-v1",
    }


def calibrate_model_weights(
    completed_samples: list[dict[str, Any]],
    *,
    as_of: str,
    minimum_samples: int = 3,
) -> dict[str, Any]:
    scored = []
    for sample in completed_samples:
        prediction = sample.get("prediction") or {}
        actual = actual_outcome(int(sample["home_score"]), int(sample["away_score"]))
        sources = _source_probabilities(prediction)
        if sources:
            scored.append({"actual": actual, "sources": sources})

    if len(scored) < minimum_samples:
        result = default_weight_run(as_of)
        result["sample_count"] = len(scored)
        return result

    losses: dict[str, dict[str, float]] = {}
    for model_name in DEFAULT_WEIGHTS:
        model_rows = [row for row in scored if model_name in row["sources"]]
        if not model_rows:
            continue
        brier = sum(_brier(row["sources"][model_name], row["actual"]) for row in model_rows) / len(model_rows)
        log_loss = sum(_log_loss(row["sources"][model_name], row["actual"]) for row in model_rows) / len(model_rows)
        coverage = len(model_rows) / len(scored)
        losses[model_name] = {
            "brier_score": round(brier, 6),
            "log_loss": round(log_loss, 6),
            "coverage": round(coverage, 6),
            "samples": len(model_rows),
        }

    if not losses:
        result = default_weight_run(as_of, reason="completed samples had no auditable source probabilities")
        result["sample_count"] = len(scored)
        return result

    raw_weights = {}
    for model_name, loss in losses.items():
        # Blend Brier and log loss so models are rewarded for both calibration and
        # assigning enough mass to the actual outcome. Coverage penalizes sparse sources.
        combined = float(loss["brier_score"]) + 0.18 * float(loss["log_loss"])
        raw_weights[model_name] = max(0.02, float(loss["coverage"]) / max(combined, 1e-6))

    calibrated = _bounded_normalize(raw_weights, minimum=0.05, maximum=0.45)
    return {
        "date": as_of,
        "weights": calibrated,
        "sample_count": len(scored),
        "model_losses": losses,
        "reason": "calibrated from completed predictions before target date",
        "model_version": "ensemble-weight-calibration-v1",
    }


def _source_probabilities(prediction: dict[str, Any]) -> dict[str, dict[str, float]]:
    ensemble_sources = ((prediction.get("ensemble") or {}).get("source_probabilities") or {})
    sources = {
        name: normalize_outcomes(probabilities)
        for name, probabilities in ensemble_sources.items()
        if isinstance(probabilities, dict)
    }
    if not sources:
        for model_name, payload in {
            "elo": prediction.get("elo"),
            "poisson": prediction.get("poisson"),
            "monte_carlo": prediction.get("monte_carlo"),
            "market": prediction.get("market"),
            "xgboost": prediction.get("xgboost"),
        }.items():
            probabilities = _extract_probabilities(payload)
            if probabilities:
                sources[model_name] = normalize_outcomes(probabilities)
    return sources


def _extract_probabilities(payload: Any) -> dict[str, float] | None:
    if not isinstance(payload, dict):
        return None
    if {"home", "draw", "away"} <= set(payload):
        return {outcome: float(payload[outcome]) for outcome in OUTCOMES}
    if {"home_win", "draw", "away_win"} <= set(payload):
        return {
            "home": float(payload["home_win"]),
            "draw": float(payload["draw"]),
            "away": float(payload["away_win"]),
        }
    if payload.get("available") and isinstance(payload.get("implied_probability_no_vig"), dict):
        return {
            outcome: float(payload["implied_probability_no_vig"].get(outcome, 0.0))
            for outcome in OUTCOMES
        }
    return None


def _brier(probabilities: dict[str, float], outcome: str) -> float:
    return sum((float(probabilities.get(key, 0.0)) - (1.0 if key == outcome else 0.0)) ** 2 for key in OUTCOMES) / 3


def _log_loss(probabilities: dict[str, float], outcome: str) -> float:
    probability = max(1e-15, min(1 - 1e-15, float(probabilities.get(outcome, 0.0))))
    return -math.log(probability)


def _bounded_normalize(
    raw_weights: dict[str, float],
    *,
    minimum: float,
    maximum: float,
) -> dict[str, float]:
    active = {name: max(0.0, float(weight)) for name, weight in raw_weights.items() if weight > 0}
    if not active:
        return dict(DEFAULT_WEIGHTS)
    normalized = normalize_outcomes({name: active.get(name, 0.0) for name in OUTCOMES})
    if set(active) - set(OUTCOMES):
        total = sum(active.values())
        normalized = {name: active[name] / total for name in active}
    clipped = {name: min(maximum, max(minimum, weight)) for name, weight in normalized.items()}
    total = sum(clipped.values())
    return {name: weight / total for name, weight in clipped.items()}
