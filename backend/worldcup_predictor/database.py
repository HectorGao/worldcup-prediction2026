from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


SCHEMA = """
CREATE TABLE IF NOT EXISTS fixtures (
  id TEXT PRIMARY KEY,
  date TEXT NOT NULL,
  kickoff TEXT NOT NULL,
  home_team TEXT NOT NULL,
  away_team TEXT NOT NULL,
  group_name TEXT,
  venue TEXT,
  status TEXT NOT NULL,
  home_score INTEGER,
  away_score INTEGER,
  home_elo REAL NOT NULL,
  away_elo REAL NOT NULL,
  market_home REAL,
  market_draw REAL,
  market_away REAL,
  market_over_2_5 REAL,
  market_under_2_5 REAL,
  market_source TEXT,
  market_handicap TEXT,
  market_handicap_line TEXT,
  updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS predictions (
  fixture_id TEXT PRIMARY KEY,
  payload_json TEXT NOT NULL,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(fixture_id) REFERENCES fixtures(id)
);

CREATE TABLE IF NOT EXISTS model_weight_runs (
  date TEXT PRIMARY KEY,
  payload_json TEXT NOT NULL,
  updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS raw_provider_payloads (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  provider TEXT NOT NULL,
  fixture_id TEXT,
  payload_json TEXT NOT NULL,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS web_fixtures (
  id TEXT PRIMARY KEY,
  date TEXT NOT NULL,
  kickoff TEXT NOT NULL,
  home_team TEXT NOT NULL,
  away_team TEXT NOT NULL,
  group_name TEXT,
  venue TEXT,
  status TEXT NOT NULL,
  home_score INTEGER,
  away_score INTEGER,
  source_name TEXT NOT NULL,
  source_url TEXT,
  payload_json TEXT NOT NULL,
  scraped_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS historical_matches (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  date TEXT NOT NULL,
  home_team TEXT NOT NULL,
  away_team TEXT NOT NULL,
  home_score INTEGER NOT NULL,
  away_score INTEGER NOT NULL,
  tournament TEXT NOT NULL,
  neutral INTEGER NOT NULL,
  UNIQUE(date, home_team, away_team, home_score, away_score, tournament)
);

CREATE TABLE IF NOT EXISTS team_profiles (
  team TEXT PRIMARY KEY,
  payload_json TEXT NOT NULL,
  updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS knockout_nodes (
  id TEXT PRIMARY KEY,
  payload_json TEXT NOT NULL,
  source_url TEXT,
  updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS national_team_maps (
  canonical_team TEXT PRIMARY KEY,
  team_zh TEXT,
  api_football_team_id INTEGER,
  provider TEXT,
  updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS team_squads (
  team TEXT PRIMARY KEY,
  coach_json TEXT,
  source_name TEXT NOT NULL,
  source_url TEXT,
  fetched_at TEXT,
  updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS squad_players (
  team TEXT NOT NULL,
  player_id TEXT NOT NULL,
  name TEXT NOT NULL,
  age INTEGER,
  number INTEGER,
  position TEXT,
  photo TEXT,
  source_name TEXT,
  stats_status TEXT NOT NULL DEFAULT 'queued',
  player_strength REAL,
  updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(team, player_id)
);

CREATE TABLE IF NOT EXISTS player_club_stats (
  player_id TEXT NOT NULL,
  season INTEGER,
  payload_json TEXT NOT NULL,
  player_strength REAL,
  stats_status TEXT NOT NULL,
  last_error TEXT,
  updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(player_id, season)
);

CREATE TABLE IF NOT EXISTS club_standings (
  league_id INTEGER,
  season INTEGER,
  club_id INTEGER,
  club_name TEXT,
  rank INTEGER,
  points INTEGER,
  payload_json TEXT NOT NULL,
  updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(league_id, season, club_id)
);

CREATE TABLE IF NOT EXISTS squad_strength_runs (
  team TEXT PRIMARY KEY,
  payload_json TEXT NOT NULL,
  model_version TEXT NOT NULL,
  updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS roster_sync_queue (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  team TEXT NOT NULL,
  player_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(team, player_id)
);

CREATE TABLE IF NOT EXISTS lyihub_match_details (
  match_id TEXT PRIMARY KEY,
  fixture_id TEXT NOT NULL,
  date TEXT NOT NULL,
  kickoff TEXT NOT NULL,
  stage TEXT,
  home_team TEXT NOT NULL,
  away_team TEXT NOT NULL,
  home_team_zh TEXT,
  away_team_zh TEXT,
  home_team_source_id TEXT,
  away_team_source_id TEXT,
  venue TEXT,
  status TEXT NOT NULL,
  home_score INTEGER,
  away_score INTEGER,
  has_predict INTEGER NOT NULL DEFAULT 0,
  source_url TEXT,
  payload_json TEXT NOT NULL,
  detail_json TEXT,
  synced_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS lyihub_players (
  team TEXT NOT NULL,
  team_zh TEXT NOT NULL,
  team_source_id TEXT,
  player_id TEXT NOT NULL,
  player_name TEXT NOT NULL,
  shirt_number INTEGER,
  position TEXT,
  ability REAL,
  score10_json TEXT,
  fitness_json TEXT,
  payload_json TEXT NOT NULL,
  first_match_id TEXT,
  updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(team, player_id)
);

CREATE TABLE IF NOT EXISTS finished_match_results (
  match_id TEXT PRIMARY KEY,
  date TEXT NOT NULL,
  stage TEXT,
  home_team TEXT NOT NULL,
  away_team TEXT NOT NULL,
  home_goals_90 INTEGER,
  away_goals_90 INTEGER,
  home_goals_extra_time INTEGER,
  away_goals_extra_time INTEGER,
  home_penalties INTEGER,
  away_penalties INTEGER,
  winner TEXT,
  loser TEXT,
  decided_by_extra_time INTEGER NOT NULL DEFAULT 0,
  decided_by_penalties INTEGER NOT NULL DEFAULT 0,
  source TEXT NOT NULL,
  source_url TEXT,
  fetched_at TEXT,
  payload_json TEXT NOT NULL,
  synced_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS rating_update_log (
  match_id TEXT PRIMARY KEY,
  processed_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            self._ensure_column(connection, "fixtures", "market_over_2_5", "REAL")
            self._ensure_column(connection, "fixtures", "market_under_2_5", "REAL")
            self._ensure_column(connection, "fixtures", "market_source", "TEXT")
            self._ensure_column(connection, "fixtures", "market_handicap", "TEXT")
            self._ensure_column(connection, "fixtures", "market_handicap_line", "TEXT")

    def upsert_finished_match_result(self, match: dict[str, Any]) -> dict[str, Any]:
        existing = self.get_finished_match(str(match["match_id"]))
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO finished_match_results (
                  match_id, date, stage, home_team, away_team, home_goals_90, away_goals_90,
                  home_goals_extra_time, away_goals_extra_time, home_penalties, away_penalties,
                  winner, loser, decided_by_extra_time, decided_by_penalties,
                  source, source_url, fetched_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(match_id) DO UPDATE SET
                  date=excluded.date,
                  stage=excluded.stage,
                  home_team=excluded.home_team,
                  away_team=excluded.away_team,
                  home_goals_90=excluded.home_goals_90,
                  away_goals_90=excluded.away_goals_90,
                  home_goals_extra_time=excluded.home_goals_extra_time,
                  away_goals_extra_time=excluded.away_goals_extra_time,
                  home_penalties=excluded.home_penalties,
                  away_penalties=excluded.away_penalties,
                  winner=excluded.winner,
                  loser=excluded.loser,
                  decided_by_extra_time=excluded.decided_by_extra_time,
                  decided_by_penalties=excluded.decided_by_penalties,
                  source=excluded.source,
                  source_url=excluded.source_url,
                  fetched_at=excluded.fetched_at,
                  payload_json=excluded.payload_json,
                  synced_at=CURRENT_TIMESTAMP
                """,
                (
                    match["match_id"],
                    match["date"],
                    match.get("stage"),
                    match["home_team"],
                    match["away_team"],
                    match.get("home_goals_90"),
                    match.get("away_goals_90"),
                    match.get("home_goals_extra_time"),
                    match.get("away_goals_extra_time"),
                    match.get("home_penalties"),
                    match.get("away_penalties"),
                    match.get("winner"),
                    match.get("loser"),
                    1 if match.get("decided_by_extra_time") else 0,
                    1 if match.get("decided_by_penalties") else 0,
                    match.get("source") or "unknown",
                    match.get("source_url"),
                    match.get("fetched_at"),
                    json.dumps(match, ensure_ascii=False),
                ),
            )
        return {"inserted": existing is None, "previous": existing}

    def get_finished_match(self, match_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM finished_match_results WHERE match_id = ?",
                (match_id,),
            ).fetchone()
        return self._finished_match_row(row) if row else None

    def list_finished_matches(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM finished_match_results ORDER BY date, match_id"
            ).fetchall()
        return [self._finished_match_row(row) for row in rows]

    def rating_update_processed_ids(self) -> set[str]:
        with self.connect() as connection:
            rows = connection.execute("SELECT match_id FROM rating_update_log").fetchall()
        return {str(row["match_id"]) for row in rows}

    def mark_rating_update_processed(self, match_id: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO rating_update_log (match_id) VALUES (?)",
                (match_id,),
            )

    def find_fixture_for_match_result(self, match: dict[str, Any]) -> dict[str, Any] | None:
        params = (match.get("date"), match.get("home_team"), match.get("away_team"))
        with self.connect() as connection:
            for table in ("fixtures", "web_fixtures"):
                row = connection.execute(
                    f"""
                    SELECT *, '{table}' AS table_name FROM {table}
                    WHERE date = ? AND home_team = ? AND away_team = ?
                    LIMIT 1
                    """,
                    params,
                ).fetchone()
                if row:
                    return dict(row)
            row = connection.execute(
                """
                SELECT *, 'lyihub_match_details' AS table_name FROM lyihub_match_details
                WHERE date = ? AND home_team = ? AND away_team = ?
                LIMIT 1
                """,
                params,
            ).fetchone()
            return dict(row) if row else None

    def mark_fixture_final_from_result(self, match: dict[str, Any]) -> str:
        row = self.find_fixture_for_match_result(match)
        fixture_id = str(row["id"] if row and row.get("table_name") != "lyihub_match_details" else row["fixture_id"]) if row else f"web-{match['match_id']}"
        score = (match.get("home_goals_90"), match.get("away_goals_90"))
        if row and row.get("table_name") == "fixtures":
            with self.connect() as connection:
                connection.execute(
                    """
                    UPDATE fixtures
                    SET status = 'final', home_score = ?, away_score = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (score[0], score[1], row["id"]),
                )
        elif row and row.get("table_name") == "lyihub_match_details":
            with self.connect() as connection:
                connection.execute(
                    """
                    UPDATE lyihub_match_details
                    SET status = 'final', home_score = ?, away_score = ?, source_url = COALESCE(?, source_url),
                        synced_at = CURRENT_TIMESTAMP
                    WHERE fixture_id = ?
                    """,
                    (score[0], score[1], match.get("source_url"), row["fixture_id"]),
                )
        else:
            self.upsert_web_fixture(
                {
                    "id": fixture_id,
                    "date": match["date"],
                    "kickoff": match.get("kickoff") or match["date"],
                    "home_team": match["home_team"],
                    "away_team": match["away_team"],
                    "group": match.get("stage"),
                    "venue": None if not row else row.get("venue"),
                    "status": "final",
                    "home_score": score[0],
                    "away_score": score[1],
                    "source_url": match.get("source_url"),
                    "result_sync": match,
                },
                source_name=str(match.get("source") or "online_result_sync"),
            )
        self.delete_prediction(fixture_id)
        return fixture_id

    def delete_prediction(self, fixture_id: str) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM predictions WHERE fixture_id = ?", (fixture_id,))

    def clear_predictions(self) -> int:
        with self.connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM predictions").fetchone()
            connection.execute("DELETE FROM predictions")
        return int(row["count"] or 0)

    def _ensure_column(
        self,
        connection: sqlite3.Connection,
        table: str,
        column: str,
        definition: str,
    ) -> None:
        columns = {
            row["name"]
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def upsert_fixture(self, fixture: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO fixtures (
                  id, date, kickoff, home_team, away_team, group_name, venue, status,
                  home_score, away_score, home_elo, away_elo, market_home, market_draw, market_away,
                  market_over_2_5, market_under_2_5, market_source, market_handicap, market_handicap_line
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  date=excluded.date,
                  kickoff=excluded.kickoff,
                  home_team=excluded.home_team,
                  away_team=excluded.away_team,
                  group_name=excluded.group_name,
                  venue=excluded.venue,
                  status=excluded.status,
                  home_score=excluded.home_score,
                  away_score=excluded.away_score,
                  home_elo=excluded.home_elo,
                  away_elo=excluded.away_elo,
                  market_home=excluded.market_home,
                  market_draw=excluded.market_draw,
                  market_away=excluded.market_away,
                  market_over_2_5=excluded.market_over_2_5,
                  market_under_2_5=excluded.market_under_2_5,
                  market_source=excluded.market_source,
                  market_handicap=excluded.market_handicap,
                  market_handicap_line=excluded.market_handicap_line,
                  updated_at=CURRENT_TIMESTAMP
                """,
                (
                    fixture["id"],
                    fixture["date"],
                    fixture["kickoff"],
                    fixture["home_team"],
                    fixture["away_team"],
                    fixture.get("group"),
                    fixture.get("venue"),
                    fixture["status"],
                    fixture.get("home_score"),
                    fixture.get("away_score"),
                    fixture["home_elo"],
                    fixture["away_elo"],
                    fixture.get("market_home"),
                    fixture.get("market_draw"),
                    fixture.get("market_away"),
                    fixture.get("market_over_2_5"),
                    fixture.get("market_under_2_5"),
                    fixture.get("market_source"),
                    json.dumps(fixture.get("market_handicap"), ensure_ascii=False)
                    if fixture.get("market_handicap") is not None
                    else None,
                    fixture.get("market_handicap_line"),
                ),
            )

    def list_fixtures(self, date: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM fixtures WHERE date = ? ORDER BY kickoff, id",
                (date,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_market_fixtures(
        self,
        start_date: str,
        *,
        source_keyword: str = "China Sporttery",
        limit: int = 6,
    ) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM fixtures
                WHERE date >= ?
                  AND market_source LIKE ?
                ORDER BY kickoff, id
                LIMIT ?
                """,
                (start_date, f"%{source_keyword}%", int(limit)),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_fixture(self, fixture_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM fixtures WHERE id = ?", (fixture_id,)).fetchone()
        return dict(row) if row else None

    def count_fixtures(self, date: str) -> int:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT
                  (SELECT COUNT(*) FROM fixtures WHERE date = ?) +
                  (SELECT COUNT(*) FROM web_fixtures WHERE date = ?) AS fixture_count
                """,
                (date, date),
            ).fetchone()
        return int(row["fixture_count"])

    def upsert_web_fixture(self, fixture: dict[str, Any], source_name: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO web_fixtures (
                  id, date, kickoff, home_team, away_team, group_name, venue, status,
                  home_score, away_score, source_name, source_url, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  date=excluded.date,
                  kickoff=excluded.kickoff,
                  home_team=excluded.home_team,
                  away_team=excluded.away_team,
                  group_name=excluded.group_name,
                  venue=excluded.venue,
                  status=excluded.status,
                  home_score=excluded.home_score,
                  away_score=excluded.away_score,
                  source_name=excluded.source_name,
                  source_url=excluded.source_url,
                  payload_json=excluded.payload_json,
                  scraped_at=CURRENT_TIMESTAMP
                """,
                (
                    fixture["id"],
                    fixture["date"],
                    fixture.get("kickoff") or fixture["date"],
                    fixture["home_team"],
                    fixture["away_team"],
                    fixture.get("group"),
                    fixture.get("venue"),
                    fixture.get("status", "scheduled"),
                    fixture.get("home_score"),
                    fixture.get("away_score"),
                    source_name,
                    fixture.get("source_url"),
                    json.dumps(fixture, ensure_ascii=False),
                ),
            )

    def list_web_fixtures(self, date: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM web_fixtures WHERE date = ? ORDER BY kickoff, id",
                (date,),
            ).fetchall()
        return [self._web_fixture_row(row) for row in rows]

    def get_web_fixture(self, fixture_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM web_fixtures WHERE id = ?",
                (fixture_id,),
            ).fetchone()
        return self._web_fixture_row(row) if row else None

    def available_dates(self) -> list[str]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT date FROM fixtures
                UNION
                SELECT date FROM web_fixtures
                ORDER BY date
                """
            ).fetchall()
        return [row["date"] for row in rows]

    def save_historical_match(self, match: Any) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO historical_matches
                  (date, home_team, away_team, home_score, away_score, tournament, neutral)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    match.date,
                    match.home_team,
                    match.away_team,
                    match.home_score,
                    match.away_score,
                    match.tournament,
                    1 if match.neutral else 0,
                ),
            )

    def save_team_profile(self, team: str, profile: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO team_profiles (team, payload_json)
                VALUES (?, ?)
                ON CONFLICT(team) DO UPDATE SET
                  payload_json=excluded.payload_json,
                  updated_at=CURRENT_TIMESTAMP
                """,
                (team, json.dumps(profile, ensure_ascii=False)),
            )

    def list_team_profiles(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute("SELECT payload_json FROM team_profiles").fetchall()
        profiles = [json.loads(row["payload_json"]) for row in rows]
        return sorted(profiles, key=lambda profile: profile.get("elo", 0), reverse=True)

    def web_fixture_count(self) -> int:
        with self.connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM web_fixtures").fetchone()
        return int(row["count"])

    def historical_match_count(self) -> int:
        with self.connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM historical_matches").fetchone()
        return int(row["count"])

    def list_historical_matches(self, since: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM historical_matches"
        params: list[Any] = []
        if since:
            query += " WHERE date >= ?"
            params.append(since)
        query += " ORDER BY date"
        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def save_prediction(self, fixture_id: str, payload: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO predictions (fixture_id, payload_json)
                VALUES (?, ?)
                ON CONFLICT(fixture_id) DO UPDATE SET
                  payload_json=excluded.payload_json,
                  created_at=CURRENT_TIMESTAMP
                """,
                (fixture_id, json.dumps(payload, ensure_ascii=False)),
            )

    def get_prediction(self, fixture_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM predictions WHERE fixture_id = ?",
                (fixture_id,),
            ).fetchone()
        return json.loads(row["payload_json"]) if row else None

    def save_model_weight_run(self, date: str, payload: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO model_weight_runs (date, payload_json)
                VALUES (?, ?)
                ON CONFLICT(date) DO UPDATE SET
                  payload_json=excluded.payload_json,
                  updated_at=CURRENT_TIMESTAMP
                """,
                (date, json.dumps(payload, ensure_ascii=False)),
            )

    def get_model_weight_run(self, date: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM model_weight_runs WHERE date = ?",
                (date,),
            ).fetchone()
        return json.loads(row["payload_json"]) if row else None

    def latest_model_weight_run(self, as_of: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM model_weight_runs
                WHERE date <= ?
                ORDER BY date DESC
                LIMIT 1
                """,
                (as_of,),
            ).fetchone()
        return json.loads(row["payload_json"]) if row else None

    def completed_prediction_samples_before(self, as_of: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT
                  p.fixture_id,
                  p.payload_json,
                  f.date,
                  f.status,
                  f.home_score,
                  f.away_score
                FROM predictions p
                JOIN fixtures f ON f.id = p.fixture_id
                WHERE f.date < ?
                  AND f.status = 'final'
                  AND f.home_score IS NOT NULL
                  AND f.away_score IS NOT NULL
                UNION ALL
                SELECT
                  p.fixture_id,
                  p.payload_json,
                  w.date,
                  w.status,
                  w.home_score,
                  w.away_score
                FROM predictions p
                JOIN web_fixtures w ON w.id = p.fixture_id
                WHERE w.date < ?
                  AND w.status = 'final'
                  AND w.home_score IS NOT NULL
                  AND w.away_score IS NOT NULL
                UNION ALL
                SELECT
                  p.fixture_id,
                  p.payload_json,
                  l.date,
                  l.status,
                  l.home_score,
                  l.away_score
                FROM predictions p
                JOIN lyihub_match_details l ON l.fixture_id = p.fixture_id
                WHERE l.date < ?
                  AND l.status = 'final'
                  AND l.home_score IS NOT NULL
                  AND l.away_score IS NOT NULL
                ORDER BY date, fixture_id
                """,
                (as_of, as_of, as_of),
            ).fetchall()
        samples = []
        for row in rows:
            sample = dict(row)
            sample["prediction"] = json.loads(sample.pop("payload_json"))
            samples.append(sample)
        return samples

    def upsert_lyihub_match(self, match: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO lyihub_match_details (
                  match_id, fixture_id, date, kickoff, stage, home_team, away_team,
                  home_team_zh, away_team_zh, home_team_source_id, away_team_source_id,
                  venue, status, home_score, away_score, has_predict, source_url, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(match_id) DO UPDATE SET
                  fixture_id=excluded.fixture_id,
                  date=excluded.date,
                  kickoff=excluded.kickoff,
                  stage=excluded.stage,
                  home_team=excluded.home_team,
                  away_team=excluded.away_team,
                  home_team_zh=excluded.home_team_zh,
                  away_team_zh=excluded.away_team_zh,
                  home_team_source_id=excluded.home_team_source_id,
                  away_team_source_id=excluded.away_team_source_id,
                  venue=excluded.venue,
                  status=excluded.status,
                  home_score=excluded.home_score,
                  away_score=excluded.away_score,
                  has_predict=excluded.has_predict,
                  source_url=excluded.source_url,
                  payload_json=excluded.payload_json,
                  synced_at=CURRENT_TIMESTAMP
                """,
                (
                    match["match_id"],
                    match["id"],
                    match["date"],
                    match["kickoff"],
                    match.get("stage") or match.get("group"),
                    match["home_team"],
                    match["away_team"],
                    match.get("home_team_zh_source"),
                    match.get("away_team_zh_source"),
                    match.get("home_team_id_source"),
                    match.get("away_team_id_source"),
                    match.get("venue"),
                    match.get("status", "scheduled"),
                    match.get("home_score"),
                    match.get("away_score"),
                    1 if match.get("has_predict") else 0,
                    match.get("source_url"),
                    json.dumps(match.get("payload") or match, ensure_ascii=False),
                ),
            )

    def save_lyihub_match_detail(self, match: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE lyihub_match_details
                SET detail_json = ?, synced_at = CURRENT_TIMESTAMP
                WHERE match_id = ?
                """,
                (json.dumps(match.get("detail") or {}, ensure_ascii=False), match["match_id"]),
            )
            for player in match.get("players", []):
                connection.execute(
                    """
                    INSERT INTO lyihub_players (
                      team, team_zh, team_source_id, player_id, player_name, shirt_number,
                      position, ability, score10_json, fitness_json, payload_json, first_match_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(team, player_id) DO UPDATE SET
                      team_zh=excluded.team_zh,
                      team_source_id=excluded.team_source_id,
                      player_name=excluded.player_name,
                      shirt_number=excluded.shirt_number,
                      position=excluded.position,
                      ability=excluded.ability,
                      score10_json=excluded.score10_json,
                      fitness_json=excluded.fitness_json,
                      payload_json=excluded.payload_json,
                      updated_at=CURRENT_TIMESTAMP
                    """,
                    (
                        player["team"],
                        player["team_zh"],
                        player.get("team_id_source"),
                        player["player_id"],
                        player["player_name"],
                        player.get("shirt_number"),
                        player.get("position"),
                        player.get("ability"),
                        json.dumps(player.get("score10") or {}, ensure_ascii=False),
                        json.dumps(player.get("fitness") or {}, ensure_ascii=False),
                        json.dumps(player.get("payload") or player, ensure_ascii=False),
                        player.get("match_id"),
                    ),
                )

    def list_lyihub_matches(self, date: str | None = None, stage: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM lyihub_match_details"
        clauses: list[str] = []
        params: list[Any] = []
        if date:
            clauses.append("date = ?")
            params.append(date)
        if stage and stage != "all":
            clauses.append("stage = ?")
            params.append(stage)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY kickoff, match_id"
        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._lyihub_match_row(row) for row in rows]

    def get_lyihub_match_by_fixture(self, fixture_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM lyihub_match_details WHERE fixture_id = ? OR match_id = ?",
                (fixture_id, fixture_id.replace("lyihub-", "")),
            ).fetchone()
        return self._lyihub_match_row(row) if row else None

    def lyihub_stages(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT stage, COUNT(*) AS match_count,
                  SUM(CASE WHEN status = 'final' THEN 1 ELSE 0 END) AS finished_count,
                  MIN(date) AS start_date,
                  MAX(date) AS end_date
                FROM lyihub_match_details
                GROUP BY stage
                ORDER BY MIN(kickoff)
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def lyihub_group_teams(self) -> list[str]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                    SELECT home_team AS team FROM lyihub_match_details WHERE stage LIKE '%小组赛%'
                    UNION
                    SELECT away_team AS team FROM lyihub_match_details WHERE stage LIKE '%小组赛%'
                    ORDER BY team
                """
            ).fetchall()
        return [row["team"] for row in rows]

    def lyihub_team_matches(self, team: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM lyihub_match_details
                WHERE home_team = ? OR away_team = ? OR home_team_zh = ? OR away_team_zh = ?
                ORDER BY kickoff, match_id
                """,
                (team, team, team, team),
            ).fetchall()
        return [self._lyihub_match_row(row) for row in rows]

    def lyihub_team_players(self, team: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM lyihub_players
                WHERE team = ? OR team_zh = ?
                ORDER BY shirt_number IS NULL, shirt_number, player_name
                """,
                (team, team),
            ).fetchall()
        return [self._lyihub_player_row(row) for row in rows]

    def lyihub_player_coverage(self) -> dict[str, Any]:
        teams = self.lyihub_group_teams()
        per_team = []
        with self.connect() as connection:
            for team in teams:
                row = connection.execute(
                    """
                    SELECT COUNT(*) AS players,
                      SUM(CASE WHEN ability IS NOT NULL THEN 1 ELSE 0 END) AS with_ability
                    FROM lyihub_players
                    WHERE team = ?
                    """,
                    (team,),
                ).fetchone()
                per_team.append(
                    {
                        "team": team,
                        "players": int(row["players"] or 0),
                        "players_with_ability": int(row["with_ability"] or 0),
                    }
                )
                per_team[-1]["roster_complete"] = per_team[-1]["players"] >= 26
                per_team[-1]["ability_complete"] = (
                    per_team[-1]["players"] > 0
                    and per_team[-1]["players_with_ability"] == per_team[-1]["players"]
                )
                per_team[-1]["complete"] = (
                    per_team[-1]["roster_complete"] and per_team[-1]["players_with_ability"] >= 20
                )
        return {
            "expected_teams": 48,
            "teams_found": len(teams),
            "complete_teams": len([item for item in per_team if item["complete"]]),
            "roster_complete_teams": len([item for item in per_team if item["roster_complete"]]),
            "ability_complete_teams": len([item for item in per_team if item["ability_complete"]]),
            "missing_ability_players": sum(item["players"] - item["players_with_ability"] for item in per_team),
            "teams": per_team,
        }

    def save_team_squad(self, team: str, squad: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO national_team_maps (canonical_team, api_football_team_id, provider)
                VALUES (?, ?, ?)
                ON CONFLICT(canonical_team) DO UPDATE SET
                  api_football_team_id=excluded.api_football_team_id,
                  provider=excluded.provider,
                  updated_at=CURRENT_TIMESTAMP
                """,
                (team, squad.get("team_id"), squad.get("source")),
            )
            connection.execute(
                """
                INSERT INTO team_squads (team, coach_json, source_name, source_url, fetched_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(team) DO UPDATE SET
                  coach_json=excluded.coach_json,
                  source_name=excluded.source_name,
                  source_url=excluded.source_url,
                  fetched_at=excluded.fetched_at,
                  updated_at=CURRENT_TIMESTAMP
                """,
                (
                    team,
                    json.dumps(squad.get("coach") or {}, ensure_ascii=False),
                    squad.get("source") or "unknown",
                    squad.get("source_url"),
                    squad.get("fetched_at"),
                ),
            )
            for player in squad.get("players", []):
                player_id = str(player["player_id"])
                connection.execute(
                    """
                    INSERT INTO squad_players (
                      team, player_id, name, age, number, position, photo, source_name, stats_status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued')
                    ON CONFLICT(team, player_id) DO UPDATE SET
                      name=excluded.name,
                      age=excluded.age,
                      number=excluded.number,
                      position=excluded.position,
                      photo=excluded.photo,
                      source_name=excluded.source_name,
                      updated_at=CURRENT_TIMESTAMP
                    """,
                    (
                        team,
                        player_id,
                        player["name"],
                        player.get("age"),
                        player.get("number"),
                        player.get("position"),
                        player.get("photo"),
                        player.get("source") or squad.get("source"),
                    ),
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO roster_sync_queue (team, player_id, status)
                    VALUES (?, ?, 'pending')
                    """,
                    (team, player_id),
                )

    def get_team_squad(self, team: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            squad_row = connection.execute(
                "SELECT * FROM team_squads WHERE team = ?",
                (team,),
            ).fetchone()
            if not squad_row:
                return None
            rows = connection.execute(
                """
                SELECT * FROM squad_players
                WHERE team = ?
                ORDER BY
                  CASE position
                    WHEN 'Goalkeeper' THEN 1
                    WHEN 'Defender' THEN 2
                    WHEN 'Midfielder' THEN 3
                    WHEN 'Attacker' THEN 4
                    ELSE 5
                  END,
                  number IS NULL,
                  number,
                  name
                """,
                (team,),
            ).fetchall()
            players = []
            for row in rows:
                player = dict(row)
                stats = self._latest_player_stats(connection, player["player_id"])
                if stats:
                    player.update(stats)
                players.append(player)
        return {
            "team": team,
            "coach": json.loads(squad_row["coach_json"] or "{}"),
            "source": squad_row["source_name"],
            "source_url": squad_row["source_url"],
            "fetched_at": squad_row["fetched_at"],
            "players": players,
        }

    def get_next_roster_queue(self, limit: int) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM roster_sync_queue
                WHERE status IN ('pending', 'failed') AND attempts < 3
                ORDER BY updated_at, id
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_player_club_stats(self, player_id: str | int, stats: dict[str, Any]) -> None:
        season = stats.get("season") or 0
        player_strength = stats.get("player_strength")
        status = stats.get("stats_status") or ("complete" if season else "failed")
        last_error = stats.get("last_error")
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO player_club_stats (
                  player_id, season, payload_json, player_strength, stats_status, last_error
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(player_id, season) DO UPDATE SET
                  payload_json=excluded.payload_json,
                  player_strength=excluded.player_strength,
                  stats_status=excluded.stats_status,
                  last_error=excluded.last_error,
                  updated_at=CURRENT_TIMESTAMP
                """,
                (
                    str(player_id),
                    season,
                    json.dumps(stats, ensure_ascii=False),
                    player_strength,
                    status,
                    last_error,
                ),
            )
            connection.execute(
                """
                UPDATE squad_players
                SET stats_status = ?, player_strength = ?, updated_at = CURRENT_TIMESTAMP
                WHERE player_id = ?
                """,
                (status, player_strength, str(player_id)),
            )

    def mark_roster_queue_item(self, team: str, player_id: str | int, status: str, last_error: str | None = None) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE roster_sync_queue
                SET status = ?, attempts = attempts + 1, last_error = ?, updated_at = CURRENT_TIMESTAMP
                WHERE team = ? AND player_id = ?
                """,
                (status, last_error, team, str(player_id)),
            )

    def save_squad_strength(self, team: str, payload: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO squad_strength_runs (team, payload_json, model_version)
                VALUES (?, ?, ?)
                ON CONFLICT(team) DO UPDATE SET
                  payload_json=excluded.payload_json,
                  model_version=excluded.model_version,
                  updated_at=CURRENT_TIMESTAMP
                """,
                (
                    team,
                    json.dumps(payload, ensure_ascii=False),
                    payload.get("model_version") or "roster-strength-v1",
                ),
            )

    def get_squad_strength(self, team: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM squad_strength_runs WHERE team = ?",
                (team,),
            ).fetchone()
        return json.loads(row["payload_json"]) if row else None

    def roster_health(self) -> dict[str, Any]:
        with self.connect() as connection:
            queue = connection.execute(
                """
                SELECT
                  SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending,
                  SUM(CASE WHEN status = 'done' THEN 1 ELSE 0 END) AS done,
                  SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed
                FROM roster_sync_queue
                """
            ).fetchone()
            players = connection.execute(
                """
                SELECT
                  COUNT(*) AS total,
                  SUM(CASE WHEN stats_status IN ('complete', 'enriched') THEN 1 ELSE 0 END) AS complete
                FROM squad_players
                """
            ).fetchone()
        return {
            "queue_pending": int(queue["pending"] or 0),
            "queue_done": int(queue["done"] or 0),
            "queue_failed": int(queue["failed"] or 0),
            "players_total": int(players["total"] or 0),
            "players_with_stats": int(players["complete"] or 0),
        }

    def _latest_player_stats(self, connection: sqlite3.Connection, player_id: str) -> dict[str, Any] | None:
        row = connection.execute(
            """
            SELECT payload_json, player_strength, stats_status
            FROM player_club_stats
            WHERE player_id = ?
            ORDER BY season DESC
            LIMIT 1
            """,
            (str(player_id),),
        ).fetchone()
        if not row:
            return None
        payload = json.loads(row["payload_json"])
        payload["player_strength"] = row["player_strength"]
        payload["stats_status"] = row["stats_status"]
        return payload

    def _lyihub_match_row(self, row: sqlite3.Row) -> dict[str, Any]:
        payload = json.loads(row["payload_json"] or "{}")
        detail = json.loads(row["detail_json"]) if row["detail_json"] else None
        return {
            "id": row["fixture_id"],
            "match_id": row["match_id"],
            "date": row["date"],
            "kickoff": row["kickoff"],
            "stage": row["stage"],
            "group": row["stage"],
            "home_team": row["home_team"],
            "away_team": row["away_team"],
            "home_team_zh": row["home_team_zh"],
            "away_team_zh": row["away_team_zh"],
            "home_team_source_id": row["home_team_source_id"],
            "away_team_source_id": row["away_team_source_id"],
            "venue": row["venue"],
            "status": row["status"],
            "home_score": row["home_score"],
            "away_score": row["away_score"],
            "has_predict": bool(row["has_predict"]),
            "source_url": row["source_url"],
            "source_name": "lyihub_worldcup_static_json",
            "payload": payload,
            "detail": detail,
        }

    def _lyihub_player_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "team": row["team"],
            "team_zh": row["team_zh"],
            "team_source_id": row["team_source_id"],
            "player_id": row["player_id"],
            "player_name": row["player_name"],
            "shirt_number": row["shirt_number"],
            "position": row["position"],
            "ability": row["ability"],
            "score10": json.loads(row["score10_json"] or "{}"),
            "fitness": json.loads(row["fitness_json"] or "{}"),
            "payload": json.loads(row["payload_json"] or "{}"),
        }

    def _web_fixture_row(self, row: sqlite3.Row) -> dict[str, Any]:
        payload = json.loads(row["payload_json"])
        payload.update(
            {
                "id": row["id"],
                "date": row["date"],
                "kickoff": row["kickoff"],
                "home_team": row["home_team"],
                "away_team": row["away_team"],
                "group": row["group_name"],
                "venue": row["venue"],
                "status": row["status"],
                "home_score": row["home_score"],
                "away_score": row["away_score"],
                "source_name": row["source_name"],
                "source_url": row["source_url"],
            }
        )
        return payload

    def _finished_match_row(self, row: sqlite3.Row) -> dict[str, Any]:
        payload = json.loads(row["payload_json"] or "{}")
        payload.update(
            {
                "match_id": row["match_id"],
                "date": row["date"],
                "stage": row["stage"],
                "home_team": row["home_team"],
                "away_team": row["away_team"],
                "home_goals_90": row["home_goals_90"],
                "away_goals_90": row["away_goals_90"],
                "home_goals_extra_time": row["home_goals_extra_time"],
                "away_goals_extra_time": row["away_goals_extra_time"],
                "home_penalties": row["home_penalties"],
                "away_penalties": row["away_penalties"],
                "winner": row["winner"],
                "loser": row["loser"],
                "is_finished": True,
                "decided_by_extra_time": bool(row["decided_by_extra_time"]),
                "decided_by_penalties": bool(row["decided_by_penalties"]),
                "source": row["source"],
                "source_url": row["source_url"],
                "fetched_at": row["fetched_at"],
                "synced_at": row["synced_at"],
            }
        )
        return payload
