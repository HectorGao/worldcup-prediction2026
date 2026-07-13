from __future__ import annotations

import argparse
import csv
import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any


CLEAR_TABLES = (
    "club_standings",
    "fifa_match_contexts",
    "lyihub_players",
    "player_club_stats",
    "player_power_rankings",
    "raw_provider_payloads",
    "roster_sync_queue",
    "source_conflict_warnings",
    "source_field_values",
    "sporttery_odds_snapshots",
    "squad_players",
    "squad_strength_runs",
    "team_squads",
)

BLOCKED_KEYS = {
    "api_key",
    "api_token",
    "bookmaker",
    "crest",
    "detail",
    "detail_json",
    "headshot",
    "lineup",
    "lineups",
    "logo",
    "lottery_market",
    "market",
    "market_source",
    "odds",
    "odds_markets",
    "payload",
    "payload_json",
    "photo",
    "player",
    "players",
    "provider",
    "provider_payload",
    "raw",
    "roster",
    "source",
    "source_id",
    "source_url",
    "source_urls",
    "sportmonks_features",
    "squad",
    "squads",
    "team_source_id",
}

BLOCKED_KEY_FRAGMENTS = (
    "api_football",
    "betfair",
    "bookmaker",
    "espn",
    "fifa_",
    "football_data",
    "footballdata",
    "lyihub",
    "market_",
    "odds_",
    "player",
    "roster",
    "squad",
    "lineup",
    "sportmonks",
    "sporttery",
)


def blocked_key(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_")
    if normalized in BLOCKED_KEYS:
        return True
    if normalized.startswith("source_") or normalized.endswith("_source") or "_source_" in normalized:
        return True
    return any(fragment in normalized for fragment in BLOCKED_KEY_FRAGMENTS)


def sanitize_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: sanitize_json(item)
            for key, item in value.items()
            if not blocked_key(str(key))
        }
    if isinstance(value, list):
        return [sanitize_json(item) for item in value]
    return value


def table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')}


def update_existing_columns(
    connection: sqlite3.Connection,
    table: str,
    values: dict[str, Any],
) -> None:
    if not table_exists(connection, table):
        return
    columns = table_columns(connection, table)
    selected = {column: value for column, value in values.items() if column in columns}
    if not selected:
        return
    assignments = ", ".join(f'"{column}" = ?' for column in selected)
    connection.execute(
        f'UPDATE "{table}" SET {assignments}',
        tuple(selected.values()),
    )


def sanitize_database_json(connection: sqlite3.Connection) -> int:
    updated = 0
    tables = [
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    for table in tables:
        json_columns = [column for column in table_columns(connection, table) if column.endswith("_json")]
        for column in json_columns:
            rows = connection.execute(
                f'SELECT rowid, "{column}" FROM "{table}" WHERE "{column}" IS NOT NULL'
            ).fetchall()
            for rowid, raw_value in rows:
                try:
                    parsed = json.loads(raw_value)
                except (TypeError, json.JSONDecodeError):
                    continue
                sanitized = json.dumps(sanitize_json(parsed), ensure_ascii=False, separators=(",", ":"))
                connection.execute(
                    f'UPDATE "{table}" SET "{column}" = ? WHERE rowid = ?',
                    (sanitized, rowid),
                )
                updated += 1
    return updated


def build_public_database(source: Path, destination: Path) -> dict[str, Any]:
    if not source.is_file():
        raise FileNotFoundError(f"Private database not found: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)

    connection = sqlite3.connect(destination)
    cleared_rows: dict[str, int] = {}
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ValueError(f"Private database integrity check failed: {integrity}")

        for table in CLEAR_TABLES:
            if not table_exists(connection, table):
                continue
            count = int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            cleared_rows[table] = count
            connection.execute(f'DELETE FROM "{table}"')

        update_existing_columns(
            connection,
            "fixtures",
            {
                "market_home": None,
                "market_draw": None,
                "market_away": None,
                "market_over_2_5": None,
                "market_under_2_5": None,
                "market_source": None,
                "market_handicap": None,
                "market_handicap_line": None,
                "sportmonks_features": None,
            },
        )
        update_existing_columns(
            connection,
            "finished_match_results",
            {"source": "public_fact_snapshot", "source_url": None, "payload_json": "{}"},
        )
        update_existing_columns(
            connection,
            "web_fixtures",
            {"source_name": "public_fact_snapshot", "source_url": None, "payload_json": "{}"},
        )
        update_existing_columns(
            connection,
            "lyihub_match_details",
            {
                "home_team_source_id": None,
                "away_team_source_id": None,
                "source_url": None,
                "payload_json": "{}",
                "detail_json": "{}",
            },
        )
        update_existing_columns(
            connection,
            "knockout_nodes",
            {"source_url": None},
        )
        update_existing_columns(
            connection,
            "national_team_maps",
            {"api_football_team_id": None, "provider": "public_fact_snapshot"},
        )

        sanitized_json_values = sanitize_database_json(connection)
        connection.commit()
        public_integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if public_integrity != "ok":
            raise ValueError(f"Public database integrity check failed: {public_integrity}")
        connection.execute("VACUUM")
    finally:
        connection.close()

    return {
        "cleared_rows": cleared_rows,
        "sanitized_json_values": sanitized_json_values,
        "destination_size": destination.stat().st_size,
    }


def write_sanitized_json(source: Path, destination: Path) -> None:
    payload = json.loads(source.read_text(encoding="utf-8"))
    destination.write_text(
        json.dumps(sanitize_json(payload), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_sanitized_csv(source: Path, destination: Path) -> None:
    with source.open("r", encoding="utf-8", newline="") as source_handle:
        reader = csv.DictReader(source_handle)
        fieldnames = [field for field in (reader.fieldnames or []) if not blocked_key(field)]
        rows = [{field: row.get(field, "") for field in fieldnames} for row in reader]
    with destination.open("w", encoding="utf-8", newline="") as destination_handle:
        writer = csv.DictWriter(
            destination_handle,
            fieldnames=fieldnames,
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def build_public_json_tree(source: Path, destination: Path) -> dict[str, int]:
    if not source.is_dir():
        raise FileNotFoundError(f"Private data directory not found: {source}")
    json_files = 0
    copied_files = 0
    for source_path in sorted(path for path in source.rglob("*") if path.is_file()):
        if source_path.name.startswith("."):
            continue
        relative = source_path.relative_to(source)
        destination_path = destination / relative
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        if source_path.suffix.lower() == ".json":
            write_sanitized_json(source_path, destination_path)
            json_files += 1
        elif source_path.suffix.lower() == ".csv":
            write_sanitized_csv(source_path, destination_path)
            copied_files += 1
        else:
            shutil.copy2(source_path, destination_path)
            copied_files += 1
    return {"json_files": json_files, "copied_files": copied_files}


def build_snapshot(private_root: Path, repository_root: Path) -> dict[str, Any]:
    private_database = private_root / "data" / "worldcup.sqlite3"
    public_database = repository_root / "data" / "worldcup.sqlite3"
    summary = {
        "policy_version": 1,
        "database": build_public_database(private_database, public_database),
        "outputs": build_public_json_tree(private_root / "outputs", repository_root / "outputs"),
        "precomputed": build_public_json_tree(
            private_root / "precomputed" / "api",
            repository_root / "precomputed" / "api",
        ),
    }
    manifest = repository_root / "data" / "public_snapshot_manifest.json"
    manifest.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a compliant public data snapshot from ignored private source data."
    )
    parser.add_argument(
        "--private-root",
        type=Path,
        default=Path(".private_data"),
        help="Ignored directory containing data/, outputs/, and precomputed/api/.",
    )
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path("."),
        help="Repository root that receives the public snapshot.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = build_snapshot(args.private_root.resolve(), args.repository_root.resolve())
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
