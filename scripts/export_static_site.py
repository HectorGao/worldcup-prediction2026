from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from worldcup_predictor.service import WorldCupService
from worldcup_predictor.team_metadata import display_team


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = PROJECT_ROOT / "dist"
PRECOMPUTED_DIR = PROJECT_ROOT / "precomputed"
STATIC_BOOTSTRAP = """    <script>
      window.WORLDCUP_STATIC_BUILD = true;
      window.WORLDCUP_STATIC_META_URL = "/api/meta.json";
    </script>
"""


def json_default(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=json_default) + "\n",
        encoding="utf-8",
    )


def static_file_path(value: str) -> str:
    path = str(value)
    if not path or "\\" in path or any(part in {"", ".", ".."} for part in path.split("/")):
        raise ValueError(f"Unsafe static path: {value!r}")
    return path


def copy_frontend() -> None:
    if DIST_DIR.exists():
        shutil.rmtree(DIST_DIR)
    DIST_DIR.mkdir(parents=True)

    index = (PROJECT_ROOT / "index.html").read_text(encoding="utf-8")
    if "window.WORLDCUP_STATIC_BUILD" not in index:
        index = index.replace("    <script defer src=\"src/main.js", f"{STATIC_BOOTSTRAP}    <script defer src=\"src/main.js")
    (DIST_DIR / "index.html").write_text(index, encoding="utf-8")
    shutil.copytree(PROJECT_ROOT / "src", DIST_DIR / "src", ignore=shutil.ignore_patterns(".DS_Store"))


def matches_payload(service: WorldCupService, date: str) -> dict[str, Any]:
    effective_date = service.default_match_date(date)
    matches = service.list_matches_with_prediction_summary(effective_date)
    return {
        "date": effective_date,
        "requested_date": date,
        "display_mode": "static_export",
        "window_dates": sorted({match["date"] for match in matches}),
        "matches": matches,
    }


def static_team_detail(service: WorldCupService, team: str) -> dict[str, Any]:
    display = display_team(team)
    matches = [
        match
        for match in service.lyihub_matches().get("matches", [])
        if match.get("home_team") == team or match.get("away_team") == team
    ]
    squad = service.get_team_squad(team, allow_empty=True)
    players = [
        {
            "shirt_number": player.get("number"),
            "player_name": player.get("name"),
            "club": player.get("club"),
            "position": player.get("position"),
            "ability": player.get("player_strength"),
            "ability_estimated": player.get("stats_status") not in {"done", "manual"},
            "league": player.get("league"),
        }
        for player in squad.get("players", [])
    ]
    return {
        "team": team,
        "display": display,
        "coverage": {"matches": len(matches), "players": len(players)},
        "matches": matches,
        "players": players,
        "power_rankings": [],
        "static_lightweight": True,
    }


def select_export_dates(dates: list[str], default_date: str, max_dates: int | None) -> list[str]:
    if max_dates is None or max_dates <= 0 or len(dates) <= max_dates:
        return dates
    center = dates.index(default_date) if default_date in dates else len(dates) - 1
    before = max_dates // 2
    start = max(0, center - before)
    end = min(len(dates), start + max_dates)
    start = max(0, end - max_dates)
    return dates[start:end]


def export_static_data(
    service: WorldCupService,
    simulations: int,
    max_dates: int | None,
    full_team_details: bool,
    output_dir: Path = DIST_DIR,
) -> dict[str, Any]:
    now = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    all_dates = service.available_dates()
    default_date = service.default_match_date(today) if all_dates else today
    dates = select_export_dates(all_dates, default_date, max_dates)
    exported_default_date = default_date if default_date in dates else (dates[-1] if dates else default_date)

    exported_fixture_ids: set[str] = set()
    exported_teams: set[str] = set()

    write_json(
        output_dir / "api/matches/available-dates.json",
        {"dates": dates, "default_date": exported_default_date},
    )

    for date in dates:
        payload = matches_payload(service, date)
        write_json(output_dir / f"api/matches/{date}.json", payload)
        for match in payload.get("matches", []):
            fixture_id = str(match.get("id") or "")
            if fixture_id:
                exported_fixture_ids.add(fixture_id)
            for side in ("home_team", "away_team"):
                if match.get(side):
                    exported_teams.add(str(match[side]))

    rounds = service.lyihub_rounds()
    write_json(output_dir / "api/lyihub/rounds.json", rounds)
    all_lyihub_matches = service.lyihub_matches()
    write_json(output_dir / "api/lyihub/matches/all.json", all_lyihub_matches)
    for match in all_lyihub_matches.get("matches", []):
        fixture_id = str(match.get("id") or "")
        if fixture_id:
            exported_fixture_ids.add(fixture_id)
        for side in ("home_team", "away_team"):
            if match.get(side):
                exported_teams.add(str(match[side]))
    exported_fixture_ids.update(str(row["fixture_id"]) for row in service.db.list_predictions())
    stages = sorted({str(item.get("stage")) for item in rounds.get("rounds", []) if item.get("stage")})
    for stage in stages:
        write_json(
            output_dir / f"api/lyihub/matches/stage-{static_file_path(stage)}.json",
            service.lyihub_matches(stage=stage),
        )

    for fixture_id in sorted(exported_fixture_ids):
        try:
            prediction = service.get_prediction(fixture_id)
            write_json(output_dir / f"api/predictions/{static_file_path(fixture_id)}.json", prediction)
        except (KeyError, ValueError, TypeError) as exc:
            write_json(
                output_dir / f"api/predictions/{static_file_path(fixture_id)}.json",
                {"available": False, "fixture_id": fixture_id, "error": str(exc)},
            )
        try:
            write_json(
                output_dir / f"api/matches/{static_file_path(fixture_id)}/analysis.json",
                service.match_analysis(fixture_id),
            )
        except (KeyError, ValueError, TypeError) as exc:
            write_json(
                output_dir / f"api/matches/{static_file_path(fixture_id)}/analysis.json",
                {"available": False, "fixture_id": fixture_id, "error": str(exc)},
            )

    for date in dates:
        write_json(
            output_dir / f"api/reports/daily/{date}.json",
            {"date": date, "requested_date": date, "report": service.daily_report(date)},
        )

    rankings = service.team_rankings()
    write_json(output_dir / "api/teams/rankings.json", {"teams": rankings})

    for team in sorted(exported_teams):
        encoded = static_file_path(team)
        if full_team_details:
            try:
                detail = service.team_world_cup_detail(team)
            except (KeyError, ValueError, TypeError) as exc:
                detail = {"team": team, "available": False, "error": str(exc)}
        else:
            detail = static_team_detail(service, team)
        write_json(output_dir / f"api/teams/{encoded}/world-cup-detail.json", detail)
        write_json(output_dir / f"api/teams/{encoded}/squad.json", service.get_team_squad(team, allow_empty=True))

    write_json(output_dir / "api/health/data-sources.json", service.data_source_health())
    write_json(output_dir / "api/health/roster-data.json", service.roster_data_health())
    write_json(output_dir / "api/knockout.json", service.knockout())

    meta = service.meta()
    meta["static_export"] = {
        "generated_at": now,
        "timezone": "Asia/Shanghai",
        "default_date": exported_default_date,
        "exported_dates": dates,
        "available_dates_total": len(all_dates),
        "fixture_count": len(exported_fixture_ids),
        "team_count": len(exported_teams),
        "simulations_per_prediction": simulations,
        "full_team_details": full_team_details,
        "mode": "read_only_static_site",
    }
    meta["deployment"] = {
        "read_only": True,
        "use_precomputed": True,
        "precomputed_root": str(output_dir.relative_to(PROJECT_ROOT)) if output_dir.is_relative_to(PROJECT_ROOT) else str(output_dir),
    }
    write_json(output_dir / "api/meta.json", meta)
    return meta


def main() -> None:
    parser = argparse.ArgumentParser(description="Export the World Cup dashboard as a static site.")
    parser.add_argument("--db-path", default="data/worldcup.sqlite3", help="SQLite database path.")
    parser.add_argument("--simulations", type=int, default=10000, help="Monte Carlo simulations per exported prediction.")
    parser.add_argument("--max-dates", type=int, default=1, help="Maximum nearby match dates to export by default.")
    parser.add_argument("--all-dates", action="store_true", help="Export every available match date.")
    parser.add_argument("--full-team-details", action="store_true", help="Export expensive full team detail payloads.")
    parser.add_argument(
        "--precomputed",
        action="store_true",
        help="Export only API JSON into precomputed/ for Render read-only deployment.",
    )
    parser.add_argument(
        "--precomputed-dir",
        default=str(PRECOMPUTED_DIR),
        help="Directory used with --precomputed. JSON is written below <dir>/api.",
    )
    args = parser.parse_args()

    output_dir = Path(args.precomputed_dir) if args.precomputed else DIST_DIR
    if args.precomputed:
        api_dir = output_dir / "api"
        output_dir.mkdir(parents=True, exist_ok=True)
    else:
        copy_frontend()
    service = WorldCupService(db_path=args.db_path)
    max_dates = None if args.all_dates else args.max_dates
    meta = export_static_data(
        service,
        simulations=args.simulations,
        max_dates=max_dates,
        full_team_details=args.full_team_details,
        output_dir=output_dir,
    )
    static = meta["static_export"]
    print(
        "Static export complete: "
        f"{static['fixture_count']} fixtures, {static['team_count']} teams, "
        f"default date {static['default_date']}"
    )


if __name__ == "__main__":
    main()
