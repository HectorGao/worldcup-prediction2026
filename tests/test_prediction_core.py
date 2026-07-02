from math import isclose

from worldcup_predictor.prediction.calibration import calibrate_score_matrix_to_market
from worldcup_predictor.prediction.dixon_coles import (
    outcome_probabilities,
    score_matrix,
    top_scorelines,
)
from worldcup_predictor.prediction.elo import update_elo
from worldcup_predictor.prediction.metrics import evaluate_result
from worldcup_predictor.prediction.ensemble import blend_probabilities
from worldcup_predictor.prediction.handicap import handicap_outcome, handicap_probabilities
from worldcup_predictor.prediction.market import analyze_value, market_from_decimal_odds
from worldcup_predictor.prediction.monte_carlo import MonteCarloConfig, simulate_match
from worldcup_predictor.prediction.odds import convert_odds, devig
from worldcup_predictor.prediction.poisson_model import PoissonModelConfig, estimate_poisson_prediction
from worldcup_predictor.prediction.learning import rolling_worldcup_adjustment
from worldcup_predictor.prediction.xgboost_model import FEATURE_NAMES, build_features, estimate_xgboost_prediction, train_xgboost_layer
from worldcup_predictor.prediction.weight_calibration import calibrate_model_weights


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


def test_poisson_stage_pace_changes_lambdas_and_scorelines_are_diverse():
    fixture = {"home_team": "France", "away_team": "Senegal", "group": "小组赛第1轮"}
    home_profile = {"team": "France", "elo": 1960, "attack_rating": 1.85, "defense_rating": 0.78, "recent_weighted_matches": 20}
    away_profile = {"team": "Senegal", "elo": 1760, "attack_rating": 1.35, "defense_rating": 1.08, "recent_weighted_matches": 14}
    recent = [
        {"date": "2026-06-20", "home_team": "France", "away_team": "Canada", "home_score": 3, "away_score": 1, "tournament": "World Cup"},
        {"date": "2026-06-18", "home_team": "Senegal", "away_team": "Japan", "home_score": 1, "away_score": 1, "tournament": "World Cup"},
    ]

    conservative = estimate_poisson_prediction(fixture, home_profile, away_profile, recent)
    open_fixture = dict(fixture, group="小组赛第3轮")
    open_match = estimate_poisson_prediction(open_fixture, home_profile, away_profile, recent)

    assert conservative["lambda_home"] < open_match["lambda_home"]
    assert conservative["lambda_away"] < open_match["lambda_away"]
    assert conservative["scorelines"][0]["score"] != "1-1"


def test_monte_carlo_distribution_aligns_with_lambdas():
    result = simulate_match(1.55, 0.95, config=MonteCarloConfig(simulations=2500, seed=12))

    assert result["simulations"] == 2500
    assert isclose(result["home_win"] + result["draw"] + result["away_win"], 1.0, rel_tol=1e-9)
    assert result["home_win"] > result["away_win"]
    assert abs(result["average_goals"]["home"] - 1.55) < 0.18
    assert abs(result["average_goals"]["away"] - 0.95) < 0.18


def test_market_value_analysis_uses_no_vig_and_kelly():
    market = market_from_decimal_odds({"home": 2.2, "draw": 3.4, "away": 3.6})
    analysis = analyze_value({"home": 0.55, "draw": 0.25, "away": 0.20}, market)
    home = next(item for item in analysis["items"] if item["outcome"] == "home")

    assert isclose(sum(market["implied_probability_no_vig"].values()), 1.0, rel_tol=1e-9)
    assert market["market_probability_no_vig"] == market["implied_probability_no_vig"]
    assert market["overround"] > 0
    assert home["label"] == "有价值"
    assert home["edge_label"] == "value bet"
    assert home["kelly"]["full"] > 0


def test_handicap_probability_regions_for_minus_one_and_plus_one():
    matrix = {
        (1, 0): 0.20,
        (2, 1): 0.15,
        (2, 0): 0.10,
        (0, 1): 0.25,
        (1, 1): 0.20,
        ("8+", "8+"): 0.10,
    }

    minus_one = handicap_probabilities(matrix, -1)
    plus_one = handicap_probabilities(matrix, 1)

    assert handicap_outcome(1, 0, -1) == "draw"
    assert handicap_outcome(2, 1, -1) == "draw"
    assert minus_one["probabilities"]["draw"] > minus_one["probabilities"]["home"]
    assert plus_one["probabilities"]["home"] > plus_one["probabilities"]["draw"]
    assert minus_one["tail_probability"] == 0.1
    assert minus_one["tail_note"].startswith("8+ tail")
    assert abs(sum(minus_one["probabilities"].values()) - 1.0) < 1e-9


def test_ensemble_renormalizes_when_market_is_unavailable():
    blended = blend_probabilities(
        elo={"home": 0.45, "draw": 0.28, "away": 0.27},
        poisson={"home_win": 0.50, "draw": 0.25, "away_win": 0.25},
        monte_carlo={"home_win": 0.48, "draw": 0.27, "away_win": 0.25},
        market=None,
    )

    assert isclose(blended["home"] + blended["draw"] + blended["away"], 1.0, rel_tol=1e-9)
    assert "market" not in blended["weights"]
    assert isclose(sum(blended["weights"].values()), 1.0, rel_tol=1e-9)


def test_xgboost_adapter_is_stable_and_consistent_with_base_models():
    fixture = {"home_team": "France", "away_team": "Senegal", "date": "2026-06-23"}
    home_profile = {"team": "France", "elo": 1960}
    away_profile = {"team": "Senegal", "elo": 1760}
    poisson = {"lambda_home": 1.8, "lambda_away": 0.9, "home_win": 0.58, "draw": 0.25, "away_win": 0.17}
    monte_carlo = {"home_win": 0.60, "draw": 0.24, "away_win": 0.16, "goal_variance": {"total": 2.4}}
    roster = {
        "home": {"attack_strength": 86, "defense_gk_strength": 82},
        "away": {"attack_strength": 74, "defense_gk_strength": 72},
    }

    first = estimate_xgboost_prediction(
        fixture=fixture,
        home_profile=home_profile,
        away_profile=away_profile,
        poisson=poisson,
        monte_carlo=monte_carlo,
        market=None,
        roster_strength=roster,
    )
    second = estimate_xgboost_prediction(
        fixture=fixture,
        home_profile=home_profile,
        away_profile=away_profile,
        poisson=poisson,
        monte_carlo=monte_carlo,
        market=None,
        roster_strength=roster,
    )

    assert first == second
    assert isclose(first["home_win"] + first["draw"] + first["away_win"], 1.0, rel_tol=1e-9)
    assert first["home_win"] > first["away_win"]
    assert first["engine"] == "deterministic_xgboost_adapter"


def test_xgboost_features_include_new_source_and_squad_inputs():
    fixture = {
        "home_team": "France",
        "away_team": "Senegal",
        "date": "2026-07-01",
        "fifa_match_stats": {"home_xg": 1.8, "away_xg": 0.9},
        "footballdata_io_stats": {"home_shots": 14, "away_shots": 7},
        "lineup_context": {"home_missing_starters": 1, "away_missing_starters": 3},
    }
    features = build_features(
        fixture=fixture,
        home_profile={"team": "France", "elo": 1960, "form_rating": 0.3},
        away_profile={"team": "Senegal", "elo": 1760, "form_rating": -0.1},
        poisson={"lambda_home": 1.8, "lambda_away": 0.9, "home_win": 0.58, "draw": 0.25, "away_win": 0.17},
        monte_carlo={"home_win": 0.60, "draw": 0.24, "away_win": 0.16, "goal_variance": {"total": 2.4}},
        market={"available": True, "market_probability_no_vig": {"home": 0.53, "draw": 0.25, "away": 0.22}},
        roster_strength={
            "home": {
                "attack_line_strength": 88,
                "midfield_line_strength": 85,
                "defense_line_strength": 82,
                "starting_xi_strength": 86,
                "bench_strength": 78,
                "squad_depth": 80,
                "coverage": 0.9,
            },
            "away": {
                "attack_line_strength": 74,
                "midfield_line_strength": 72,
                "defense_line_strength": 70,
                "starting_xi_strength": 73,
                "bench_strength": 68,
                "squad_depth": 69,
                "coverage": 0.7,
            },
        },
        learning_adjustment={"completed_match_count": 8},
    )

    for name in [
        "attack_line_edge",
        "midfield_line_edge",
        "defense_line_edge",
        "starting_xi_edge",
        "bench_strength_edge",
        "squad_depth_edge",
        "fifa_stats_xg_edge",
        "footballdata_shot_edge",
        "lineup_missing_edge",
        "roster_coverage_edge",
    ]:
        assert name in FEATURE_NAMES
        assert name in features
    assert features["attack_line_edge"] > 0
    assert features["lineup_missing_edge"] > 0


def test_xgboost_features_and_output_include_over25_inputs():
    fixture = {"home_team": "France", "away_team": "Senegal", "date": "2026-07-01"}
    poisson = {
        "lambda_home": 1.8,
        "lambda_away": 1.2,
        "home_win": 0.52,
        "draw": 0.24,
        "away_win": 0.24,
        "over_2_5": 0.58,
        "under_2_5": 0.42,
    }
    monte_carlo = {
        "home_win": 0.54,
        "draw": 0.23,
        "away_win": 0.23,
        "over_2_5": 0.61,
        "under_2_5": 0.39,
        "goal_variance": {"total": 3.0},
    }
    features = build_features(
        fixture=fixture,
        home_profile={
            "team": "France",
            "elo": 1960,
            "over25_attack_tendency": 0.72,
            "over25_defense_tendency": 0.44,
            "over25_recent_rate": 0.67,
            "over25_adjusted_rating": 0.69,
            "under25_stability": 0.31,
        },
        away_profile={
            "team": "Senegal",
            "elo": 1760,
            "over25_attack_tendency": 0.38,
            "over25_defense_tendency": 0.56,
            "over25_recent_rate": 0.33,
            "over25_adjusted_rating": 0.45,
            "under25_stability": 0.55,
        },
        poisson=poisson,
        monte_carlo=monte_carlo,
        market={
            "available": True,
            "market_probability_no_vig": {"home": 0.50, "draw": 0.26, "away": 0.24},
            "totals_probability_no_vig": {"over": 0.53, "under": 0.47},
        },
        roster_strength={"home": {}, "away": {}},
        learning_adjustment={},
    )
    result = estimate_xgboost_prediction(
        fixture=fixture,
        home_profile={"team": "France", "elo": 1960, "over25_adjusted_rating": 0.69},
        away_profile={"team": "Senegal", "elo": 1760, "over25_adjusted_rating": 0.45},
        poisson=poisson,
        monte_carlo=monte_carlo,
        market=None,
        roster_strength={"home": {}, "away": {}},
    )

    for name in [
        "over25_attack_edge",
        "over25_defense_edge",
        "over25_recent_edge",
        "over25_adjusted_edge",
        "under25_stability_edge",
        "over25_market_edge",
    ]:
        assert name in FEATURE_NAMES
        assert name in features
    assert features["over25_attack_edge"] > 0
    assert 0 <= result["over25_prob"] <= 1
    assert result["under25_prob"] == 1 - result["over25_prob"]


def test_xgboost_trainable_layer_fits_fixed_samples_and_is_stable():
    samples = []
    for index in range(10):
        samples.append(
            {
                "outcome": "home",
                "features": {
                    "elo_delta": 0.7 + index * 0.01,
                    "lambda_diff": 0.8,
                    "lambda_total": 2.8,
                    "low_total_goals": 0.0,
                    "market_home_edge": 0.05,
                    "market_draw_edge": -0.02,
                    "mc_home_edge": 0.03,
                    "mc_goal_variance": 2.6,
                    "roster_attack_edge": 0.4,
                    "form_home_edge": 0.3,
                },
            }
        )
        samples.append(
            {
                "outcome": "away",
                "features": {
                    "elo_delta": -0.7 - index * 0.01,
                    "lambda_diff": -0.8,
                    "lambda_total": 2.7,
                    "low_total_goals": 0.0,
                    "market_home_edge": -0.05,
                    "market_draw_edge": -0.01,
                    "mc_home_edge": -0.03,
                    "mc_goal_variance": 2.5,
                    "roster_attack_edge": -0.4,
                    "form_home_edge": -0.3,
                },
            }
        )
        samples.append(
            {
                "outcome": "draw",
                "features": {
                    "elo_delta": 0.02,
                    "lambda_diff": 0.01,
                    "lambda_total": 1.9,
                    "low_total_goals": 1.0,
                    "market_home_edge": 0.0,
                    "market_draw_edge": 0.05,
                    "mc_home_edge": 0.0,
                    "mc_goal_variance": 1.8,
                    "roster_attack_edge": 0.0,
                    "form_home_edge": 0.0,
                },
            }
        )

    trained = train_xgboost_layer(samples, min_samples=9)
    assert trained is not None
    assert trained.sample_count == 30
    assert trained.engine in {"xgboost.XGBClassifier", "trainable_softmax_fallback"}

    fixture = {"home_team": "France", "away_team": "Senegal", "date": "2026-06-23"}
    poisson = {"lambda_home": 1.8, "lambda_away": 0.9, "home_win": 0.58, "draw": 0.25, "away_win": 0.17}
    monte_carlo = {"home_win": 0.60, "draw": 0.24, "away_win": 0.16, "goal_variance": {"total": 2.4}}
    result = estimate_xgboost_prediction(
        fixture=fixture,
        home_profile={"team": "France", "elo": 1960},
        away_profile={"team": "Senegal", "elo": 1760},
        poisson=poisson,
        monte_carlo=monte_carlo,
        market=None,
        roster_strength={
            "home": {"attack_strength": 86, "defense_gk_strength": 82},
            "away": {"attack_strength": 74, "defense_gk_strength": 72},
        },
        trained_model=trained,
    )
    repeat = estimate_xgboost_prediction(
        fixture=fixture,
        home_profile={"team": "France", "elo": 1960},
        away_profile={"team": "Senegal", "elo": 1760},
        poisson=poisson,
        monte_carlo=monte_carlo,
        market=None,
        roster_strength={
            "home": {"attack_strength": 86, "defense_gk_strength": 82},
            "away": {"attack_strength": 74, "defense_gk_strength": 72},
        },
        trained_model=trained,
    )

    assert result == repeat
    assert result["training"]["sample_count"] == 30
    assert result["engine"] == trained.engine
    assert isclose(result["home_win"] + result["draw"] + result["away_win"], 1.0, rel_tol=1e-9)


def test_xgboost_trainable_layer_accepts_sample_weights_and_reports_summary():
    samples = []
    for index in range(6):
        samples.append(
            {
                "outcome": "home",
                "sample_weight": 5.0,
                "features": {name: 0.6 for name in [
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
                ]},
            }
        )
        samples.append(
            {
                "outcome": "away",
                "sample_weight": 0.75,
                "features": {name: -0.6 for name in [
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
                ]},
            }
        )
        samples.append(
            {
                "outcome": "draw",
                "sample_weight": 1.0,
                "features": {name: 0.0 for name in [
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
                ]},
            }
        )

    trained = train_xgboost_layer(samples, min_samples=9)

    assert trained is not None
    assert trained.metadata()["sample_weight_summary"]["max"] == 5.0
    assert trained.metadata()["sample_weight_summary"]["min"] == 0.75
    assert trained.metadata()["sample_weight_summary"]["weighted_sample_count"] > trained.sample_count


def test_learning_adjustment_uses_only_matches_before_fixture_date():
    fixture = {"home_team": "France", "away_team": "Senegal", "date": "2026-06-20"}
    matches = [
        {"date": "2026-06-18", "home_team": "France", "away_team": "Canada", "home_score": 3, "away_score": 0, "tournament": "World Cup"},
        {"date": "2026-06-25", "home_team": "France", "away_team": "Japan", "home_score": 0, "away_score": 5, "tournament": "World Cup"},
    ]

    adjustment = rolling_worldcup_adjustment(fixture=fixture, completed_matches=matches)

    assert adjustment["completed_match_count"] == 1
    assert adjustment["team_form_adjustment"]["France"]["lambda_multiplier"] > 1.0


def test_monte_carlo_simulation_count_changes_sample_size_and_distribution():
    low = simulate_match(1.4, 1.1, config=MonteCarloConfig(simulations=500, seed=99))
    high = simulate_match(1.4, 1.1, config=MonteCarloConfig(simulations=5000, seed=99))

    assert low["simulations"] == 500
    assert high["simulations"] == 5000
    assert low["confidence_interval"]["home_win"]["high"] - low["confidence_interval"]["home_win"]["low"] > high["confidence_interval"]["home_win"]["high"] - high["confidence_interval"]["home_win"]["low"]


def test_model_weight_calibration_rewards_better_completed_models():
    samples = [
        {
            "home_score": 2,
            "away_score": 0,
            "prediction": {
                "ensemble": {
                    "source_probabilities": {
                        "elo": {"home": 0.52, "draw": 0.25, "away": 0.23},
                        "poisson": {"home": 0.74, "draw": 0.16, "away": 0.10},
                        "monte_carlo": {"home": 0.70, "draw": 0.18, "away": 0.12},
                        "market": {"home": 0.32, "draw": 0.30, "away": 0.38},
                        "xgboost": {"home": 0.68, "draw": 0.20, "away": 0.12},
                    }
                }
            },
        },
        {
            "home_score": 1,
            "away_score": 0,
            "prediction": {
                "ensemble": {
                    "source_probabilities": {
                        "elo": {"home": 0.51, "draw": 0.27, "away": 0.22},
                        "poisson": {"home": 0.71, "draw": 0.18, "away": 0.11},
                        "monte_carlo": {"home": 0.67, "draw": 0.19, "away": 0.14},
                        "market": {"home": 0.29, "draw": 0.31, "away": 0.40},
                        "xgboost": {"home": 0.69, "draw": 0.19, "away": 0.12},
                    }
                }
            },
        },
        {
            "home_score": 0,
            "away_score": 2,
            "prediction": {
                "ensemble": {
                    "source_probabilities": {
                        "elo": {"home": 0.35, "draw": 0.27, "away": 0.38},
                        "poisson": {"home": 0.12, "draw": 0.18, "away": 0.70},
                        "monte_carlo": {"home": 0.15, "draw": 0.17, "away": 0.68},
                        "market": {"home": 0.41, "draw": 0.30, "away": 0.29},
                        "xgboost": {"home": 0.13, "draw": 0.20, "away": 0.67},
                    }
                }
            },
        },
    ]

    run = calibrate_model_weights(samples, as_of="2026-06-27")

    assert run["sample_count"] == 3
    assert isclose(sum(run["weights"].values()), 1.0, rel_tol=1e-9)
    assert run["weights"]["poisson"] > run["weights"]["market"]
    assert run["model_losses"]["poisson"]["brier_score"] < run["model_losses"]["market"]["brier_score"]
