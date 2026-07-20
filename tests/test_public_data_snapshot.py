from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.build_public_data_snapshot import (
    build_public_database,
    build_public_json_tree,
    sanitize_json,
)


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def create_source_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE historical_matches (
            id INTEGER PRIMARY KEY,
            date TEXT,
            home_team TEXT,
            away_team TEXT,
            home_score INTEGER,
            away_score INTEGER,
            tournament TEXT,
            neutral INTEGER
        );
        CREATE TABLE raw_provider_payloads (
            id INTEGER PRIMARY KEY,
            provider TEXT,
            payload_json TEXT
        );
        CREATE TABLE sporttery_odds_snapshots (
            id INTEGER PRIMARY KEY,
            payload_json TEXT
        );
        CREATE TABLE lyihub_players (
            player_id TEXT PRIMARY KEY,
            player_name TEXT,
            payload_json TEXT
        );
        CREATE TABLE fixtures (
            id TEXT PRIMARY KEY,
            date TEXT,
            home_team TEXT,
            away_team TEXT,
            market_home REAL,
            market_draw REAL,
            market_away REAL,
            market_source TEXT,
            sportmonks_features TEXT
        );
        CREATE TABLE finished_match_results (
            match_id TEXT PRIMARY KEY,
            date TEXT,
            home_team TEXT,
            away_team TEXT,
            home_goals_90 INTEGER,
            away_goals_90 INTEGER,
            source TEXT,
            source_url TEXT,
            payload_json TEXT
        );
        CREATE TABLE predictions (
            fixture_id TEXT PRIMARY KEY,
            payload_json TEXT,
            created_at TEXT
        );
        """
    )
    connection.execute(
        "INSERT INTO historical_matches VALUES (1, '2026-06-01', 'A', 'B', 2, 1, 'Friendly', 0)"
    )
    connection.execute(
        "INSERT INTO raw_provider_payloads VALUES (1, 'ESPN', ?)",
        (json.dumps({"provider": "ESPN", "raw": {"copyrighted": True}}),),
    )
    connection.execute(
        "INSERT INTO sporttery_odds_snapshots VALUES (1, ?)",
        (
            json.dumps(
                {
                    "match_num": "001",
                    "date": "2026-06-01",
                    "home_team": "A",
                    "away_team": "B",
                    "source": "China Sporttery",
                    "h2h": {"home": 1.9, "draw": 3.1, "away": 4.2},
                    "api_key": "must-not-publish",
                }
            ),
        ),
    )
    connection.execute(
        "INSERT INTO lyihub_players VALUES ('p1', 'Restricted Player', ?)",
        (json.dumps({"ability": 90}),),
    )
    connection.execute(
        "INSERT INTO fixtures VALUES ('m1', '2026-06-01', 'A', 'B', 1.9, 3.1, 4.2, 'The Odds API', ?)",
        (json.dumps({"lineups": ["Restricted Player"]}),),
    )
    connection.execute(
        "INSERT INTO finished_match_results VALUES ('m1', '2026-06-01', 'A', 'B', 2, 1, 'ESPN', 'https://espn.example/m1', ?)",
        (json.dumps({"raw": {"provider": "ESPN"}}),),
    )
    connection.execute(
        "INSERT INTO predictions VALUES ('m1', ?, '2026-06-01T00:00:00Z')",
        (
            json.dumps(
                {
                    "probabilities": {"home": 0.6, "draw": 0.25, "away": 0.15},
                    "odds_markets": {"home": 1.9},
                    "players": [{"name": "Restricted Player"}],
                    "source_url": "https://provider.example/m1",
                }
            ),
        ),
    )
    connection.commit()
    connection.close()


def test_sanitize_json_keeps_model_outputs_and_removes_restricted_fields():
    payload = {
        "probabilities": {"home": 0.6, "draw": 0.25, "away": 0.15},
        "top_scorelines": [{"score": "2-1", "probability": 0.12}],
        "odds_markets": {"home": 1.9},
        "source_url": "https://provider.example/match",
        "players": [{"name": "Restricted Player"}],
        "roster_strength": {"home": 92.0},
        "result_data_source": "ESPN",
        "nested": {"market_source": "The Odds API", "expected_goals": 1.7},
    }

    sanitized = sanitize_json(payload)

    assert sanitized["probabilities"]["home"] == 0.6
    assert sanitized["top_scorelines"][0]["score"] == "2-1"
    assert sanitized["nested"] == {"expected_goals": 1.7}
    assert "odds_markets" not in sanitized
    assert "source_url" not in sanitized
    assert "players" not in sanitized
    assert "roster_strength" not in sanitized
    assert "result_data_source" not in sanitized


def test_build_public_database_preserves_cc0_and_project_outputs(tmp_path: Path):
    source = tmp_path / "private.sqlite3"
    destination = tmp_path / "public.sqlite3"
    create_source_database(source)
    original_hash = file_hash(source)

    summary = build_public_database(source, destination)

    assert file_hash(source) == original_hash
    assert summary["cleared_rows"]["raw_provider_payloads"] == 1
    assert summary["cleared_rows"]["lyihub_players"] == 1

    connection = sqlite3.connect(destination)
    assert connection.execute("SELECT COUNT(*) FROM historical_matches").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM raw_provider_payloads").fetchone()[0] == 0
    sporttery = json.loads(connection.execute("SELECT payload_json FROM sporttery_odds_snapshots").fetchone()[0])
    assert sporttery == {
        "match_num": "001",
        "date": "2026-06-01",
        "home_team": "A",
        "away_team": "B",
        "source": "China Sporttery",
        "h2h": {"home": 1.9, "draw": 3.1, "away": 4.2},
    }
    assert connection.execute("SELECT COUNT(*) FROM lyihub_players").fetchone()[0] == 0
    fixture = connection.execute(
        "SELECT market_home, market_draw, market_away, market_source, sportmonks_features FROM fixtures"
    ).fetchone()
    assert fixture == (None, None, None, None, None)
    finished = connection.execute(
        "SELECT source, source_url, payload_json FROM finished_match_results"
    ).fetchone()
    assert finished == ("public_fact_snapshot", None, "{}")
    prediction = json.loads(connection.execute("SELECT payload_json FROM predictions").fetchone()[0])
    connection.close()
    assert prediction == {"probabilities": {"home": 0.6, "draw": 0.25, "away": 0.15}}


def test_build_public_json_tree_sanitizes_without_modifying_private_source(tmp_path: Path):
    source = tmp_path / "private"
    destination = tmp_path / "public"
    source.mkdir()
    payload_path = source / "prediction.json"
    payload_path.write_text(
        json.dumps(
            {
                "match": {"home_team": "A", "away_team": "B"},
                "probabilities": {"home": 0.55},
                "odds": {"home": 1.8},
                "squad": [{"name": "Restricted Player"}],
                "provider": "ESPN",
            }
        ),
        encoding="utf-8",
    )
    original_hash = file_hash(payload_path)

    summary = build_public_json_tree(source, destination)

    assert file_hash(payload_path) == original_hash
    assert summary == {"json_files": 1, "copied_files": 0}
    public_payload = json.loads((destination / "prediction.json").read_text(encoding="utf-8"))
    assert public_payload == {
        "match": {"home_team": "A", "away_team": "B"},
        "probabilities": {"home": 0.55},
    }
