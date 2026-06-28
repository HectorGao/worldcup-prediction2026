from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from .ensemble import normalize_outcomes


@dataclass(frozen=True)
class XGBoostConfig:
    correction_strength: float = 0.22
    trained_correction_strength: float = 0.42


FEATURE_NAMES = [
    "elo_delta",
    "lambda_diff",
    "lambda_total",
    "low_total_goals",
    "market_home_edge",
    "market_draw_edge",
    "mc_home_edge",
    "mc_goal_variance",
    "roster_attack_edge",
    "form_home_edge",
]

CLASS_ORDER = ("home", "draw", "away")


@dataclass
class TrainedXGBoostLayer:
    engine: str
    feature_names: list[str]
    sample_count: int
    class_order: tuple[str, str, str]
    model: Any
    means: list[float] | None = None
    stds: list[float] | None = None
    training_loss: float | None = None

    def predict(self, features: dict[str, float]) -> dict[str, float]:
        vector = np.array([[float(features.get(name, 0.0)) for name in self.feature_names]], dtype=float)
        if self.engine == "xgboost.XGBClassifier":
            probabilities = self.model.predict_proba(vector)[0]
            return normalize_outcomes({label: float(probabilities[index]) for index, label in enumerate(self.class_order)})

        means = np.array(self.means or [0.0] * len(self.feature_names), dtype=float)
        stds = np.array(self.stds or [1.0] * len(self.feature_names), dtype=float)
        transformed = np.column_stack([np.ones(vector.shape[0]), (vector - means) / stds])
        logits = transformed @ self.model
        exp_values = np.exp(logits[0] - np.max(logits[0]))
        probabilities = exp_values / np.sum(exp_values)
        return normalize_outcomes({label: float(probabilities[index]) for index, label in enumerate(self.class_order)})

    def metadata(self) -> dict[str, Any]:
        return {
            "engine": self.engine,
            "sample_count": self.sample_count,
            "feature_count": len(self.feature_names),
            "training_loss": self.training_loss,
            "class_order": list(self.class_order),
        }


def estimate_xgboost_prediction(
    *,
    fixture: dict[str, Any],
    home_profile: dict[str, Any],
    away_profile: dict[str, Any],
    poisson: dict[str, Any],
    monte_carlo: dict[str, Any],
    market: dict[str, Any] | None,
    roster_strength: dict[str, dict[str, Any]],
    learning_adjustment: dict[str, Any] | None = None,
    trained_model: TrainedXGBoostLayer | None = None,
    config: XGBoostConfig | None = None,
) -> dict[str, Any]:
    config = config or XGBoostConfig()
    features = build_features(
        fixture=fixture,
        home_profile=home_profile,
        away_profile=away_profile,
        poisson=poisson,
        monte_carlo=monte_carlo,
        market=market,
        roster_strength=roster_strength,
        learning_adjustment=learning_adjustment or {},
    )
    base = normalize_outcomes(
        {
            "home": float(poisson.get("home_win", 0.0)),
            "draw": float(poisson.get("draw", 0.0)),
            "away": float(poisson.get("away_win", 0.0)),
        }
    )
    mc = normalize_outcomes(
        {
            "home": float(monte_carlo.get("home_win", 0.0)),
            "draw": float(monte_carlo.get("draw", 0.0)),
            "away": float(monte_carlo.get("away_win", 0.0)),
        }
    )
    market_probs = (
        normalize_outcomes(market.get("market_probability_no_vig") or market.get("implied_probability_no_vig", {}))
        if market and market.get("available")
        else None
    )

    if trained_model:
        boosted = trained_model.predict(features)
        correction_strength = config.trained_correction_strength
        engine = trained_model.engine
        training = trained_model.metadata()
    else:
        boosted = deterministic_boosted_probabilities(base, mc, market_probs, features)
        correction_strength = config.correction_strength
        engine = "deterministic_xgboost_adapter"
        training = {
            "engine": engine,
            "sample_count": 0,
            "note": "No trainable xgboost layer was available for this fixture date.",
        }

    probabilities = {
        outcome: (1 - correction_strength) * base[outcome] + correction_strength * boosted[outcome]
        for outcome in ("home", "draw", "away")
    }
    probabilities = normalize_outcomes(probabilities)
    return {
        "engine": engine,
        "model_version": "xgb-trainable-v2",
        "home_win": probabilities["home"],
        "draw": probabilities["draw"],
        "away_win": probabilities["away"],
        "features": {key: round(value, 6) for key, value in features.items()},
        "correction_strength": correction_strength,
        "training": training,
        "scoreline_correction": {
            "enabled": False,
            "note": "XGBoost 层修正 1X2 概率；比分矩阵仍以 Poisson / Dixon-Coles 为主。",
        },
    }


def train_xgboost_layer(
    samples: list[dict[str, Any]],
    *,
    min_samples: int = 12,
) -> TrainedXGBoostLayer | None:
    usable = [
        sample
        for sample in samples
        if sample.get("outcome") in CLASS_ORDER and isinstance(sample.get("features"), dict)
    ]
    if len(usable) < min_samples:
        return None
    x = np.array(
        [
            [float((sample["features"] or {}).get(name, 0.0)) for name in FEATURE_NAMES]
            for sample in usable
        ],
        dtype=float,
    )
    y = np.array([CLASS_ORDER.index(sample["outcome"]) for sample in usable], dtype=int)
    if len(set(y.tolist())) < 2:
        return None

    xgb_model = _try_train_real_xgboost(x, y)
    if xgb_model is not None:
        return TrainedXGBoostLayer(
            engine="xgboost.XGBClassifier",
            feature_names=list(FEATURE_NAMES),
            sample_count=len(usable),
            class_order=CLASS_ORDER,
            model=xgb_model,
            training_loss=_multiclass_log_loss(xgb_model.predict_proba(x), y),
        )

    return _train_softmax_fallback(x, y, sample_count=len(usable))


def deterministic_boosted_probabilities(
    base: dict[str, float],
    mc: dict[str, float],
    market_probs: dict[str, float] | None,
    features: dict[str, float],
) -> dict[str, float]:
    home_logit = logit(base["home"]) + 0.32 * features["elo_delta"] + 0.26 * features["lambda_diff"]
    away_logit = logit(base["away"]) - 0.32 * features["elo_delta"] - 0.26 * features["lambda_diff"]
    draw_logit = logit(base["draw"]) - 0.16 * abs(features["lambda_diff"]) + 0.14 * features["low_total_goals"]

    home_logit += 0.20 * features["roster_attack_edge"] + 0.16 * features["form_home_edge"]
    away_logit -= 0.20 * features["roster_attack_edge"] + 0.16 * features["form_home_edge"]
    home_logit += 0.12 * (mc["home"] - base["home"])
    draw_logit += 0.10 * (mc["draw"] - base["draw"])
    away_logit += 0.12 * (mc["away"] - base["away"])

    if market_probs:
        home_logit += 0.18 * (market_probs["home"] - base["home"])
        draw_logit += 0.18 * (market_probs["draw"] - base["draw"])
        away_logit += 0.18 * (market_probs["away"] - base["away"])

    return softmax({"home": home_logit, "draw": draw_logit, "away": away_logit})


def monte_carlo_feature_proxy(poisson: dict[str, Any]) -> dict[str, Any]:
    lambda_home = float(poisson.get("lambda_home") or 1.32)
    lambda_away = float(poisson.get("lambda_away") or 1.32)
    return {
        "home_win": float(poisson.get("home_win", 0.0)),
        "draw": float(poisson.get("draw", 0.0)),
        "away_win": float(poisson.get("away_win", 0.0)),
        "goal_variance": {
            "home": lambda_home,
            "away": lambda_away,
            "total": lambda_home + lambda_away,
        },
    }


def _try_train_real_xgboost(x: np.ndarray, y: np.ndarray) -> Any | None:
    try:
        from xgboost import XGBClassifier  # type: ignore
    except Exception:
        return None
    try:
        model = XGBClassifier(
            n_estimators=80,
            max_depth=3,
            learning_rate=0.08,
            subsample=0.9,
            colsample_bytree=0.9,
            objective="multi:softprob",
            num_class=3,
            eval_metric="mlogloss",
            random_state=20260627,
            n_jobs=1,
        )
        model.fit(x, y)
        return model
    except Exception:
        return None


def _train_softmax_fallback(x: np.ndarray, y: np.ndarray, *, sample_count: int) -> TrainedXGBoostLayer:
    means = x.mean(axis=0)
    stds = x.std(axis=0)
    stds = np.where(stds < 1e-6, 1.0, stds)
    transformed = np.column_stack([np.ones(x.shape[0]), (x - means) / stds])
    weights = np.zeros((transformed.shape[1], len(CLASS_ORDER)), dtype=float)
    targets = np.zeros((len(y), len(CLASS_ORDER)), dtype=float)
    targets[np.arange(len(y)), y] = 1.0
    learning_rate = 0.16
    regularization = 0.015
    for _ in range(360):
        logits = transformed @ weights
        logits -= logits.max(axis=1, keepdims=True)
        probabilities = np.exp(logits)
        probabilities /= probabilities.sum(axis=1, keepdims=True)
        gradient = (transformed.T @ (probabilities - targets)) / len(y)
        gradient[1:] += regularization * weights[1:]
        weights -= learning_rate * gradient
    training_loss = _multiclass_log_loss(_softmax_matrix(transformed @ weights), y)
    return TrainedXGBoostLayer(
        engine="trainable_softmax_fallback",
        feature_names=list(FEATURE_NAMES),
        sample_count=sample_count,
        class_order=CLASS_ORDER,
        model=weights,
        means=means.tolist(),
        stds=stds.tolist(),
        training_loss=training_loss,
    )


def _softmax_matrix(logits: np.ndarray) -> np.ndarray:
    logits = logits - logits.max(axis=1, keepdims=True)
    probabilities = np.exp(logits)
    return probabilities / probabilities.sum(axis=1, keepdims=True)


def _multiclass_log_loss(probabilities: np.ndarray, y: np.ndarray) -> float:
    clipped = np.clip(probabilities[np.arange(len(y)), y], 1e-15, 1 - 1e-15)
    return round(float(-np.log(clipped).mean()), 6)


def build_features(
    *,
    fixture: dict[str, Any],
    home_profile: dict[str, Any],
    away_profile: dict[str, Any],
    poisson: dict[str, Any],
    monte_carlo: dict[str, Any],
    market: dict[str, Any] | None,
    roster_strength: dict[str, dict[str, Any]],
    learning_adjustment: dict[str, Any],
) -> dict[str, float]:
    home_elo = float(home_profile.get("elo") or fixture.get("home_elo") or 1700)
    away_elo = float(away_profile.get("elo") or fixture.get("away_elo") or 1700)
    home_lambda = float(poisson.get("lambda_home") or 1.32)
    away_lambda = float(poisson.get("lambda_away") or 1.32)
    market_probs = (
        market.get("market_probability_no_vig") or market.get("implied_probability_no_vig", {})
        if market and market.get("available")
        else {}
    )
    home_strength = roster_strength.get("home") or {}
    away_strength = roster_strength.get("away") or {}
    form = learning_adjustment.get("team_form_adjustment") or {}
    home_form = float((form.get(fixture.get("home_team")) or {}).get("goal_delta", 0.0))
    away_form = float((form.get(fixture.get("away_team")) or {}).get("goal_delta", 0.0))
    return {
        "elo_delta": clamp((home_elo - away_elo) / 450, -1.5, 1.5),
        "lambda_diff": clamp(home_lambda - away_lambda, -2.0, 2.0),
        "lambda_total": clamp(home_lambda + away_lambda, 0.4, 6.5),
        "low_total_goals": 1.0 if home_lambda + away_lambda < 2.35 else 0.0,
        "market_home_edge": float(market_probs.get("home", 0.0)) - float(poisson.get("home_win", 0.0)),
        "market_draw_edge": float(market_probs.get("draw", 0.0)) - float(poisson.get("draw", 0.0)),
        "mc_home_edge": float(monte_carlo.get("home_win", 0.0)) - float(poisson.get("home_win", 0.0)),
        "mc_goal_variance": float((monte_carlo.get("goal_variance") or {}).get("total", 0.0)),
        "roster_attack_edge": clamp(
            (
                float(home_strength.get("attack_strength", 70)) - float(away_strength.get("defense_gk_strength", 70))
                - float(away_strength.get("attack_strength", 70)) + float(home_strength.get("defense_gk_strength", 70))
            )
            / 60,
            -1.5,
            1.5,
        ),
        "form_home_edge": clamp(home_form - away_form, -1.5, 1.5),
    }


def logit(probability: float) -> float:
    probability = clamp(probability, 0.001, 0.999)
    return math.log(probability / (1 - probability))


def softmax(logits: dict[str, float]) -> dict[str, float]:
    max_logit = max(logits.values())
    exps = {key: math.exp(value - max_logit) for key, value in logits.items()}
    total = sum(exps.values())
    return {key: value / total for key, value in exps.items()}


def clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))
