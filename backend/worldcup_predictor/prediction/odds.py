from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ConvertedOdds:
    raw: float
    decimal: float
    implied_probability: float


def convert_odds(value: float) -> ConvertedOdds:
    """Convert American or decimal odds to implied probability."""
    raw = float(value)
    if raw <= -100:
        probability = abs(raw) / (abs(raw) + 100)
        decimal = 1 + 100 / abs(raw)
    elif raw >= 100:
        probability = 100 / (raw + 100)
        decimal = 1 + raw / 100
    elif raw > 1:
        decimal = raw
        probability = 1 / raw
    else:
        raise ValueError(f"Unsupported odds value: {value}")
    return ConvertedOdds(raw=raw, decimal=decimal, implied_probability=probability)


def devig(odds_by_outcome: dict[str, float]) -> dict[str, float]:
    implied = {
        outcome: convert_odds(odds).implied_probability for outcome, odds in odds_by_outcome.items()
    }
    total = sum(implied.values())
    if total <= 0:
        raise ValueError("Odds imply a non-positive probability mass")
    return {outcome: probability / total for outcome, probability in implied.items()}
