from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from .data.sporttery_snapshot import sporttery_snapshot_ids, sporttery_window_anchor_date, sporttery_window_match_start_date
from .service import WorldCupService


def _default_db_path() -> Path:
    return Path(os.getenv("WORLDCUP_DB_PATH") or os.getenv("DATABASE_PATH") or "data/worldcup.sqlite3")


def _cors_origins() -> list[str]:
    defaults = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:8000",
        "https://worldcup.hectorgao.com",
        "null",
    ]
    extra = [
        origin.strip()
        for origin in os.getenv("WORLDCUP_CORS_ORIGINS", "").split(",")
        if origin.strip()
    ]
    return list(dict.fromkeys([*defaults, *extra]))


def create_app(db_path: str | Path | None = None) -> FastAPI:
    app = FastAPI(title="World Cup Prediction System", version="0.1.0")
    service = WorldCupService(db_path=db_path or _default_db_path())
    app.state.service = service
    project_root = Path(__file__).resolve().parents[2]
    src_dir = project_root / "src"

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def no_cache_local_assets(request, call_next):
        response = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith("/src/"):
            response.headers["Cache-Control"] = "no-store, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    @app.get("/api/matches")
    def matches(date: str):
        effective_date = service.default_match_date(date)
        matches = service.list_matches_with_prediction_summary(effective_date)
        is_lottery_window = (
            effective_date in {sporttery_window_anchor_date(), sporttery_window_match_start_date()}
            and len(matches) == len(sporttery_snapshot_ids())
        )
        is_historical_completed = bool(matches) and all(
            match.get("status") == "final" and match.get("historical_without_odds")
            for match in matches
        )
        return {
            "date": effective_date,
            "requested_date": date,
            "display_mode": "sporttery_lottery_window"
            if is_lottery_window
            else "historical_completed_with_prediction"
            if is_historical_completed
            else "match_day",
            "window_dates": sorted({match["date"] for match in matches}),
            "matches": matches,
        }

    @app.get("/api/matches/available-dates")
    def available_dates():
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        return {"dates": service.available_dates(), "default_date": service.default_match_date(today)}

    @app.get("/api/predictions/{fixture_id}")
    def get_prediction(fixture_id: str):
        try:
            return service.get_prediction(fixture_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/matches/{fixture_id}/analysis")
    def match_analysis(fixture_id: str):
        try:
            return service.match_analysis(fixture_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/sync")
    def sync(date: str):
        result = service.sync_date(date)
        if result["fixture_count"] == 0:
            public_result = service.scrape_public_sources()
            result["public_scrape"] = public_result
        return result

    @app.post("/api/refresh/current")
    def refresh_current(date: str):
        return service.refresh_current_data(date)

    @app.post("/api/results/update")
    def update_after_results(
        fetch_online_results: bool = True,
        use_xgboost: bool = True,
        recalculate: bool = True,
        date: str | None = None,
        sync_fifa: bool = False,
        sync_fifa_rosters: bool = False,
        sync_footballdata_io: bool = False,
        sync_sportmonks: bool = False,
        use_sportmonks: bool = False,
        sync_sporttery_odds: bool = False,
        sync_sporttery_history: bool = False,
        backfill_historical_matches: bool = False,
        train_over25: bool = False,
    ):
        return service.update_after_results(
            fetch_online_results=fetch_online_results,
            use_xgboost=use_xgboost,
            recalculate=recalculate,
            date=date,
            sync_fifa=sync_fifa,
            sync_fifa_rosters=sync_fifa_rosters,
            sync_footballdata_io=sync_footballdata_io,
            sync_sportmonks=sync_sportmonks,
            use_sportmonks=use_sportmonks,
            sync_sporttery_odds=sync_sporttery_odds,
            sync_sporttery_history=sync_sporttery_history,
            backfill_historical_matches=backfill_historical_matches,
            train_over25=train_over25,
        )

    @app.post("/api/odds/sporttery/refresh")
    def refresh_sporttery_odds(
        include_history: bool = False,
        history_start: str = "2026-06-23",
        history_end: str = "2026-06-28",
    ):
        return app.state.service.refresh_sporttery_odds(
            include_history=include_history,
            history_start=history_start,
            history_end=history_end,
        )

    @app.post("/api/rounds/sync")
    def sync_round_overview(date: str | None = None):
        return service.sync_round_overview(date=date)

    @app.post("/api/rounds/regress")
    def regress_round_overview(date: str | None = None, auto_sync: bool = True):
        return service.regress_round_overview(date=date, auto_sync=auto_sync)

    @app.post("/api/scrape/public-web")
    def scrape_public_web():
        return service.scrape_public_sources()

    @app.post("/api/scrape/reference-site")
    def scrape_reference_site(include_details: bool = True, detail_limit: int = 120):
        return service.scrape_lyihub(include_details=include_details, detail_limit=detail_limit)

    @app.post("/api/scrape/lyihub")
    def scrape_lyihub(include_details: bool = True, detail_limit: int = 120):
        return service.scrape_lyihub(include_details=include_details, detail_limit=detail_limit)

    @app.get("/api/lyihub/matches")
    def lyihub_matches(date: str | None = None, stage: str | None = None):
        return service.lyihub_matches(date=date, stage=stage)

    @app.get("/api/lyihub/rounds")
    def lyihub_rounds():
        return service.lyihub_rounds()

    @app.get("/api/lyihub/coverage")
    def lyihub_coverage():
        return service.lyihub_coverage()

    @app.post("/api/predict/{fixture_id}")
    def predict(fixture_id: str, roster_weight: float = 0.25, simulations: int | None = None):
        try:
            return service.predict_fixture(fixture_id, roster_weight=roster_weight, simulations=simulations)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/reports/daily")
    def daily_report(date: str):
        effective_date = service.default_match_date(date)
        return {"date": effective_date, "requested_date": date, "report": service.daily_report(effective_date)}

    @app.get("/api/health/data-sources")
    def health():
        return service.data_source_health()

    @app.post("/api/health/validate-sources")
    def validate_sources():
        return service.validate_data_sources()

    @app.post("/api/models/recalibrate")
    def recalibrate_models(date: str):
        return service.recalibrate_model_weights(date)

    @app.get("/api/models/weights")
    def model_weights(date: str):
        return service.model_weights_for_date(date)

    @app.get("/api/health/roster-data")
    def roster_health():
        return service.roster_data_health()

    @app.post("/api/squads/sync")
    def sync_squad(team: str):
        try:
            return service.sync_squad(team)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/squads/sync-all")
    def sync_all_squads():
        return service.sync_all_squads()

    @app.post("/api/squads/process-queue")
    def process_roster_queue(limit: int = 20):
        return service.process_roster_queue(limit=limit)

    @app.post("/api/squads/enrich-public")
    def enrich_public_roster_queue(limit: int = 20):
        return service.enrich_roster_queue_from_public(limit=limit)

    @app.get("/api/teams/{team}/squad")
    def team_squad(team: str):
        return service.get_team_squad(team, allow_empty=True)

    @app.get("/api/teams/{team}/strength")
    def team_strength(team: str):
        try:
            return service.get_team_strength(team)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/teams/{team}/world-cup-detail")
    def team_world_cup_detail(team: str):
        return service.team_world_cup_detail(team)

    @app.get("/api/teams/rankings")
    def team_rankings(alive_only: bool = False):
        if alive_only:
            return service.simulation_rankings()
        return {"teams": service.team_rankings()}

    @app.get("/api/knockout")
    def knockout():
        return service.knockout()

    @app.get("/api/meta")
    def meta():
        return service.meta()

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    if src_dir.exists():
        app.mount("/src", StaticFiles(directory=src_dir), name="src")

    @app.get("/")
    def index():
        return FileResponse(project_root / "index.html")

    @app.get("/favicon.ico")
    def favicon():
        return Response(status_code=204)

    return app


app = create_app()
