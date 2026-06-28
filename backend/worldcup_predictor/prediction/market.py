from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .odds import convert_odds


@dataclass(frozen=True)
class MarketConfig:
    value_threshold: float = 0.05
    consensus_threshold: float = 0.03


def market_from_decimal_odds(
    odds_by_outcome: dict[str, float],
    *,
    provider: str = "the_odds_api",
    bookmaker: str | None = None,
    market_key: str = "h2h",
) -> dict[str, Any]:
    decimal_odds = {
        outcome: convert_odds(float(odds)).decimal
        for outcome, odds in odds_by_outcome.items()
        if odds is not None
    }
    implied_raw = {
        outcome: 1 / odds
        for outcome, odds in decimal_odds.items()
        if odds > 1
    }
    overround = sum(implied_raw.values())
    if overround <= 0:
        raise ValueError("Market odds imply a non-positive probability mass")
    no_vig = {outcome: probability / overround for outcome, probability in implied_raw.items()}
    return {
        "available": True,
        "provider": provider,
        "bookmaker": bookmaker,
        "market_key": market_key,
        "odds": decimal_odds,
        "implied_probability_raw": implied_raw,
        "implied_probability_no_vig": no_vig,
        "market_probability_no_vig": no_vig,
        "overround": round(overround - 1, 6),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def unavailable_market(reason: str) -> dict[str, Any]:
    return {
        "available": False,
        "provider": "the_odds_api",
        "reason": reason,
        "odds": {},
        "implied_probability_raw": {},
        "implied_probability_no_vig": {},
        "market_probability_no_vig": {},
        "overround": None,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def market_bundle(
    *,
    h2h: dict[str, Any] | None = None,
    handicap: dict[str, Any] | None = None,
    totals: dict[str, Any] | None = None,
) -> dict[str, Any]:
    h2h = h2h or unavailable_market("1X2盘口不可用")
    handicap = handicap or unavailable_market("让球胜平负盘口不可用")
    totals = totals or unavailable_market("大小球盘口不可用")
    return {
        "market_available": bool(h2h.get("available")),
        "h2h": h2h,
        "handicap": handicap,
        "totals": totals,
    }


def kelly_fraction(probability: float, decimal_odds: float) -> float:
    probability = max(0.0, min(1.0, float(probability)))
    odds = float(decimal_odds)
    if odds <= 1:
        return 0.0
    edge_odds = odds - 1
    fraction = (edge_odds * probability - (1 - probability)) / edge_odds
    return max(0.0, fraction)


def analyze_value(
    model_probabilities: dict[str, float],
    market: dict[str, Any] | None,
    *,
    config: MarketConfig | None = None,
    risk_warnings: list[str] | None = None,
) -> dict[str, Any]:
    config = config or MarketConfig()
    if not market or not market.get("available"):
        return {
            "available": False,
            "summary": "盘口未配置，当前仅基于模型评估。",
            "items": [],
            "recommended_options": [],
            "confidence": "低",
            "risk_warnings": risk_warnings or ["盘口缺失"],
            "risk_warning": "；".join(risk_warnings or ["盘口缺失", "本系统只做数据分析，不构成投注建议。"]),
        }

    market_probs = market.get("market_probability_no_vig") or market.get("implied_probability_no_vig") or {}
    odds = market.get("odds") or {}
    items = []
    for outcome in ("home", "draw", "away"):
        model_probability = float(model_probabilities.get(outcome, 0.0))
        market_probability = float(market_probs.get(outcome, 0.0))
        edge = model_probability - market_probability
        full_kelly = kelly_fraction(model_probability, float(odds.get(outcome, 0.0)))
        if edge >= config.value_threshold and full_kelly > 0:
            label = "有价值"
            edge_label = "value bet"
        elif abs(edge) <= config.consensus_threshold:
            label = "市场共识"
            edge_label = "market efficient"
        else:
            label = "不建议"
            edge_label = "no edge"
        items.append(
            {
                "outcome": outcome,
                "model_probability": round(model_probability, 6),
                "market_probability": round(market_probability, 6),
                "edge": round(edge, 6),
                "decimal_odds": odds.get(outcome),
                "label": label,
                "edge_label": edge_label,
                "kelly": {
                    "full": round(full_kelly, 6),
                    "half": round(full_kelly * 0.5, 6),
                    "quarter": round(full_kelly * 0.25, 6),
                },
                "recommendation": "不下注" if full_kelly <= 0 or label == "不建议" else "小仓位观察",
            }
        )

    best = max(items, key=lambda item: item["edge"], default=None)
    summary = "盘口未发现明显价值。"
    if best and best["label"] == "有价值":
        summary = f"{outcome_label(best['outcome'])}相对市场有 {best['edge']:.1%} 正向差异。"
    elif best and best["label"] == "市场共识":
        summary = "模型与市场接近，属于市场共识区间。"
    recommended = [item for item in items if item["label"] == "有价值"]
    confidence = "高" if recommended and max(item["edge"] for item in recommended) >= 0.08 else "中" if recommended else "低"
    warnings = risk_warnings or []
    return {
        "available": True,
        "summary": summary,
        "items": items,
        "recommended_options": recommended,
        "confidence": confidence,
        "risk_warnings": warnings,
        "risk_warning": "；".join(warnings + ["Kelly 仅用于仓位上限估算，本系统只做数据分析，不构成投注建议。"]),
    }


def outcome_label(outcome: str) -> str:
    return {"home": "主胜", "draw": "平局", "away": "客胜"}.get(outcome, outcome)
