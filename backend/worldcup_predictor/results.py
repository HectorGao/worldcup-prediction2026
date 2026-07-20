"""Canonical result identities, score presentation, and evaluation helpers."""

from __future__ import annotations

from typing import Any

from .team_metadata import canonical_team_name


def canonical_match_key(date: str, home_team: str, away_team: str) -> str:
    """Return the stable identity used to collapse equivalent source records."""
    return "|".join(
        (
            str(date or "").strip(),
            canonical_team_name(home_team),
            canonical_team_name(away_team),
        )
    )


def score_value(result: dict[str, Any], field: str, legacy_field: str) -> int | None:
    value = result.get(field, result.get(legacy_field))
    if value is None or value == "":
        return None
    return int(value)


def normalized_result_fields(result: dict[str, Any]) -> dict[str, Any]:
    """Expose the public score field names while accepting legacy database names."""
    home_90 = score_value(result, "home_score_90", "home_goals_90")
    away_90 = score_value(result, "away_score_90", "away_goals_90")
    home_extra = score_value(result, "home_score_extra_time", "home_goals_extra_time")
    away_extra = score_value(result, "away_score_extra_time", "away_goals_extra_time")
    home_penalties = score_value(result, "home_score_penalties", "home_penalties")
    away_penalties = score_value(result, "away_score_penalties", "away_penalties")
    return {
        "home_score_90": home_90,
        "away_score_90": away_90,
        "home_score_extra_time": home_extra,
        "away_score_extra_time": away_extra,
        "home_score_penalties": home_penalties,
        "away_score_penalties": away_penalties,
    }


def format_result_display(result: dict[str, Any]) -> str | None:
    """Present the 90-minute score, then extra-time and penalty detail if recorded."""
    fields = normalized_result_fields(result)
    home_90 = fields["home_score_90"]
    away_90 = fields["away_score_90"]
    if home_90 is None or away_90 is None:
        return None
    text = f"{result.get('home_team', '')} {home_90}-{away_90} {result.get('away_team', '')}".strip()
    details: list[str] = []
    if fields["home_score_extra_time"] is not None and fields["away_score_extra_time"] is not None:
        details.append(f"加时 {fields['home_score_extra_time']}-{fields['away_score_extra_time']}")
    if fields["home_score_penalties"] is not None and fields["away_score_penalties"] is not None:
        details.append(f"点球 {fields['home_score_penalties']}-{fields['away_score_penalties']}")
    return f"{text}（{'，'.join(details)}）" if details else text


def outcome_for_score(home_score: int, away_score: int) -> str:
    if home_score > away_score:
        return "home"
    if home_score < away_score:
        return "away"
    return "draw"


def parse_score(score: str | None) -> tuple[int, int] | None:
    if not score or "-" not in score:
        return None
    try:
        home, away = (int(part.strip()) for part in score.split("-", 1))
    except ValueError:
        return None
    return home, away


def prediction_evaluation(predicted_score: str | None, result: dict[str, Any]) -> dict[str, Any] | None:
    """Evaluate a scoreline strictly against the regular-time result."""
    predicted = parse_score(predicted_score)
    fields = normalized_result_fields(result)
    actual_home = fields["home_score_90"]
    actual_away = fields["away_score_90"]
    if predicted is None or actual_home is None or actual_away is None:
        return None
    predicted_home, predicted_away = predicted
    predicted_outcome = outcome_for_score(predicted_home, predicted_away)
    actual_outcome = outcome_for_score(actual_home, actual_away)
    exact_score_hit = predicted_home == actual_home and predicted_away == actual_away
    outcome_hit = predicted_outcome == actual_outcome
    return {
        "predicted_score": f"{predicted_home}-{predicted_away}",
        "actual_score_90": f"{actual_home}-{actual_away}",
        "predicted_home_score": predicted_home,
        "predicted_away_score": predicted_away,
        "actual_home_score": actual_home,
        "actual_away_score": actual_away,
        "predicted_outcome": predicted_outcome,
        "actual_outcome": actual_outcome,
        "outcome_hit": outcome_hit,
        "exact_score_hit": exact_score_hit,
        "score_hit": exact_score_hit,
        "partial_hit": outcome_hit and not exact_score_hit,
        "goal_diff_error": abs((predicted_home - predicted_away) - (actual_home - actual_away)),
        "total_goal_error": abs((predicted_home + predicted_away) - (actual_home + actual_away)),
        "home_goal_error": abs(predicted_home - actual_home),
        "away_goal_error": abs(predicted_away - actual_away),
        "evaluation_basis": "90_minute_score",
    }
