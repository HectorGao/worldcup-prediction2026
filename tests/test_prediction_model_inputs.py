from worldcup_predictor.prediction.dixon_coles import top_scorelines
from worldcup_predictor.service import WorldCupService


def test_profile_based_expected_goals_use_attack_defense_and_confidence(tmp_path):
    service = WorldCupService(db_path=tmp_path / "worldcup.sqlite3")
    strong_attack = {
        "team": "France",
        "elo": 1900,
        "attack_rating": 2.3,
        "defense_rating": 0.7,
        "recent_weighted_matches": 70,
    }
    weak_defense = {
        "team": "Senegal",
        "elo": 1700,
        "attack_rating": 1.1,
        "defense_rating": 1.5,
        "recent_weighted_matches": 40,
    }

    home_xg, away_xg, inputs = service._expected_goals_from_profiles(strong_attack, weak_defense)

    assert home_xg > away_xg
    assert inputs["home"]["attack_factor"] > 1
    assert inputs["away"]["attack_factor"] < inputs["home"]["attack_factor"]
    assert inputs["rho"] > -0.05


def test_profile_based_scorelines_are_not_mechanically_one_one(tmp_path):
    service = WorldCupService(db_path=tmp_path / "worldcup.sqlite3")
    fixtures = [
        (
            {
                "team": "Spain",
                "elo": 1960,
                "attack_rating": 2.4,
                "defense_rating": 0.65,
                "recent_weighted_matches": 90,
            },
            {
                "team": "Jordan",
                "elo": 1580,
                "attack_rating": 1.0,
                "defense_rating": 1.4,
                "recent_weighted_matches": 55,
            },
        ),
        (
            {
                "team": "Norway",
                "elo": 1760,
                "attack_rating": 1.65,
                "defense_rating": 1.0,
                "recent_weighted_matches": 60,
            },
            {
                "team": "Iraq",
                "elo": 1710,
                "attack_rating": 1.35,
                "defense_rating": 0.9,
                "recent_weighted_matches": 60,
            },
        ),
    ]

    best_scores = []
    for home, away in fixtures:
        home_xg, away_xg, inputs = service._expected_goals_from_profiles(home, away)
        matrix = service._score_matrix_from_inputs(home_xg, away_xg, inputs)
        best_scores.append(top_scorelines(matrix, limit=1)[0]["score"])

    assert len(set(best_scores)) == 2
    assert best_scores[0] != "1-1"
