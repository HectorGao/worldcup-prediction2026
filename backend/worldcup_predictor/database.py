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
  updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS predictions (
  fixture_id TEXT PRIMARY KEY,
  payload_json TEXT NOT NULL,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(fixture_id) REFERENCES fixtures(id)
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
                  market_over_2_5, market_under_2_5
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                ),
            )

    def list_fixtures(self, date: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM fixtures WHERE date = ? ORDER BY kickoff, id",
                (date,),
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
                  SUM(CASE WHEN stats_status = 'complete' THEN 1 ELSE 0 END) AS complete
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
