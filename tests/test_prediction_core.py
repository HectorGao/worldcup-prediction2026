from math import isclose

from worldcup_predictor.prediction.calibration import calibrate_score_matrix_to_market
from worldcup_predictor.prediction.dixon_coles import (
    outcome_probabilities,
    score_matrix,
    top_scorelines,
)
from worldcup_predictor.prediction.elo import update_elo
from worldcup_predictor.prediction.metrics import evaluate_result
from worldcup_predictor.prediction.odds import convert_odds, devig


def test_odds_conversion_and_devig_normalize_three_way_market():
    assert isclose(convert_odds(-125).implied_probability, 0.555555, rel_tol=1e-5)
    assert isclose(convert_odds(2.5).implied_probability, 0.4, rel_tol=1e-5)

    fair = devig({"home": -125, "draw": 260, "away": 340})

    assert set(fair) == {"home", "draw", "away"}
    assert isclose(sum(fair.values()), 1.0, rel_tol=1e-9)
    assert fair["home"] > fair["draw"] > fair["away"]


def test_elo_update_rewards_winner_and_penalizes_loser():
    updated_home, updated_away = update_elo(
        home_rating=1900,
        away_rating=1500,
        home_goals=2,
        away_goals=0,
        k=30,
    )

    assert updated_home > 1900
    assert updated_away < 1500


def test_dixon_coles_matrix_sums_to_one_and_marginals_are_consistent():
    matrix = score_matrix(home_xg=1.72, away_xg=0.92, rho=-0.08, max_goals=7)
    outcomes = outcome_probabilities(matrix)

    assert isclose(sum(matrix.values()), 1.0, rel_tol=1e-9)
    assert isclose(outcomes.home + outcomes.draw + outcomes.away, 1.0, rel_tol=1e-9)
    assert outcomes.home > outcomes.draw
    assert outcomes.home > outcomes.away


def test_market_calibration_preserves_probabilities_and_moves_toward_market():
    matrix = score_matrix(home_xg=1.4, away_xg=1.1, rho=-0.05, max_goals=7)
    before = outcome_probabilities(matrix)
    market = {"home": 0.62, "draw": 0.22, "away": 0.16}

    calibrated = calibrate_score_matrix_to_market(matrix, market, weight=0.35)
    after = outcome_probabilities(calibrated)

    assert isclose(sum(calibrated.values()), 1.0, rel_tol=1e-9)
    assert abs(after.home - market["home"]) < abs(before.home - market["home"])
    assert after.home > after.away


def test_market_calibration_can_move_totals_toward_over_under_market():
    matrix = score_matrix(home_xg=1.1, away_xg=0.8, rho=-0.02, max_goals=7)
    before = sum(
        probability
        for (home, away), probability in matrix.items()
        if isinstance(home, int) and isinstance(away, int) and home + away > 2.5
    )

    calibrated = calibrate_score_matrix_to_market(
        matrix,
        {"home": 0.44, "draw": 0.28, "away": 0.28},
        totals_market={"over": 0.62, "under": 0.38, "line": 2.5},
        weight=0.35,
    )
    after = sum(
        probability
        for (home, away), probability in calibrated.items()
        if isinstance(home, int) and isinstance(away, int) and home + away > 2.5
    )

    assert isclose(sum(calibrated.values()), 1.0, rel_tol=1e-9)
    assert abs(after - 0.62) < abs(before - 0.62)


def test_top_scorelines_and_completed_result_metrics_are_stable():
    matrix = score_matrix(home_xg=1.55, away_xg=1.0, rho=-0.06, max_goals=7)
    ranked = top_scorelines(matrix, limit=6)

    assert len(ranked) == 6
    assert ranked[0]["probability"] >= ranked[-1]["probability"]
    assert "score" in ranked[0]

    metrics = evaluate_result({"home": 0.52, "draw": 0.27, "away": 0.21}, home_goals=2, away_goals=1)

    assert 0 < metrics["brier_score"] < 1
    assert metrics["log_loss"] > 0
    assert metrics["actual_outcome"] == "home"
