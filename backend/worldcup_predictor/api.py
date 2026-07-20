from __future__ import annotations

import json
import os
import importlib.util
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from .data.sporttery_snapshot import sporttery_snapshot_ids, sporttery_window_anchor_date, sporttery_window_match_start_date
from .service import WorldCupService


def _default_db_path() -> Path:
    return Path(os.getenv("WORLDCUP_DB_PATH") or os.getenv("DATABASE_PATH") or "data/worldcup.sqlite3")


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _env_false(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"0", "false", "no", "off"}


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


def _encode_path_part(value: str) -> str:
    return quote(value, safe="")


def create_app(db_path: str | Path | None = None) -> FastAPI:
    app = FastAPI(title="World Cup Prediction System", version="0.1.0")
    service = WorldCupService(db_path=db_path or _default_db_path())
    app.state.service = service
    project_root = Path(__file__).resolve().parents[2]
    src_dir = project_root / "src"
    precomputed_root = Path(os.getenv("WORLDCUP_PRECOMPUTED_DIR") or project_root / "precomputed").resolve()
    precomputed_api_exists = (precomputed_root / "api" / "meta.json").exists()
    render_environment = any(
        [
            _env_flag("RENDER"),
            bool(os.getenv("RENDER_SERVICE_ID")),
            bool(os.getenv("RENDER_EXTERNAL_URL")),
            project_root.as_posix().startswith("/opt/render/"),
        ]
    )
    use_precomputed = _env_flag("WORLDCUP_USE_PRECOMPUTED") or (
        "WORLDCUP_USE_PRECOMPUTED" not in os.environ
        and render_environment
        and precomputed_api_exists
    )
    if _env_false("WORLDCUP_USE_PRECOMPUTED"):
        use_precomputed = False
    read_only = _env_flag("WORLDCUP_READ_ONLY") or use_precomputed
    auto_export_precomputed = (
        not read_only
        and os.getenv("WORLDCUP_AUTO_EXPORT_PRECOMPUTED", "1").strip().lower() not in {"0", "false", "no", "off"}
        and (db_path is None or _env_flag("WORLDCUP_AUTO_EXPORT_PRECOMPUTED"))
    )

    def precomputed_path(relative_path: str) -> Path:
        candidate = (precomputed_root / relative_path).resolve()
        try:
            candidate.relative_to(precomputed_root)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid precomputed path.") from exc
        return candidate

    def load_precomputed(relative_path: str) -> Any:
        path = precomputed_path(relative_path)
        if not path.exists():
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "precomputed_json_missing",
                    "message": "线上只读模式缺少预计算 JSON；请在本地重新导出 precomputed/api 并提交。",
                    "path": str(path.relative_to(project_root)) if path.is_relative_to(project_root) else str(path),
                },
            )
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "precomputed_json_invalid",
                    "message": "预计算 JSON 无法解析；请在本地重新导出并提交。",
                    "path": str(path.relative_to(project_root)) if path.is_relative_to(project_root) else str(path),
                },
            ) from exc

    def deployment_meta() -> dict[str, Any]:
        return {
            "read_only": read_only,
            "use_precomputed": use_precomputed,
            "precomputed_root": str(precomputed_root.relative_to(project_root))
            if precomputed_root.is_relative_to(project_root)
            else str(precomputed_root),
            "auto_export_precomputed": auto_export_precomputed,
            "render_environment": render_environment,
        }

    def read_only_error(action: str) -> None:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "read_only_deployment",
                "message": f"线上部署为只读预计算模式，已禁用{action}。请在本地完成更新、导出 precomputed/api 后提交。",
            },
        )

    def maybe_export_precomputed(payload: Any, action: str) -> Any:
        if not auto_export_precomputed or not isinstance(payload, dict):
            return payload
        export_script = project_root / "scripts" / "export_static_site.py"
        try:
            spec = importlib.util.spec_from_file_location("worldcup_static_export", export_script)
            if spec is None or spec.loader is None:
                raise RuntimeError(f"Cannot load export script: {export_script}")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            simulations = int(os.getenv("WORLDCUP_PRECOMPUTED_SIMULATIONS", "10000"))
            meta = module.export_static_data(
                service,
                simulations=simulations,
                max_dates=None,
                full_team_details=_env_flag("WORLDCUP_PRECOMPUTED_FULL_TEAM_DETAILS"),
                output_dir=precomputed_root,
            )
            static = meta.get("static_export") or {}
            payload["precomputed_export"] = {
                "ok": True,
                "action": action,
                "output_dir": str(precomputed_root.relative_to(project_root))
                if precomputed_root.is_relative_to(project_root)
                else str(precomputed_root),
                "generated_at": static.get("generated_at"),
                "default_date": static.get("default_date"),
                "exported_dates": static.get("exported_dates"),
                "fixture_count": static.get("fixture_count"),
                "team_count": static.get("team_count"),
            }
        except Exception as exc:  # pragma: no cover - exercised through API behavior.
            payload["precomputed_export"] = {
                "ok": False,
                "action": action,
                "output_dir": str(precomputed_root),
                "error": str(exc),
            }
        return payload

    def load_precomputed_match_day(date: str) -> Any:
        path = precomputed_path(f"api/matches/{_encode_path_part(date)}.json")
        if path.exists():
            return load_precomputed(f"api/matches/{_encode_path_part(date)}.json")
        index = load_precomputed("api/matches/available-dates.json")
        dates = [str(item) for item in index.get("dates") or []]
        fallback_date = str(index.get("default_date") or "")
        if not fallback_date and dates:
            past_or_today = [item for item in dates if item <= date]
            fallback_date = past_or_today[-1] if past_or_today else dates[-1]
        if not fallback_date:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "precomputed_match_date_missing",
                    "message": "线上只读模式没有可用比赛日缓存；请在本地重新导出 precomputed/api 并提交。",
                    "requested_date": date,
                },
            )
        payload = load_precomputed(f"api/matches/{_encode_path_part(fallback_date)}.json")
        if isinstance(payload, dict):
            payload = dict(payload)
            payload["requested_date"] = date
            payload["date_fallback"] = {
                "requested_date": date,
                "served_date": fallback_date,
                "reason": "requested precomputed match date is not exported",
            }
        return payload

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
        if use_precomputed:
            return load_precomputed_match_day(date)
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
        if use_precomputed:
            return load_precomputed("api/matches/available-dates.json")
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        return {"dates": service.available_dates(), "default_date": service.default_match_date(today)}

    @app.get("/api/predictions/{fixture_id}")
    def get_prediction(fixture_id: str):
        if use_precomputed:
            return load_precomputed(f"api/predictions/{_encode_path_part(fixture_id)}.json")
        try:
            return service.get_prediction(fixture_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/matches/{fixture_id}/analysis")
    def match_analysis(fixture_id: str):
        if use_precomputed:
            return load_precomputed(f"api/matches/{_encode_path_part(fixture_id)}/analysis.json")
        try:
            return service.match_analysis(fixture_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/sync")
    def sync(date: str):
        if read_only:
            read_only_error("赛果同步")
        result = service.sync_date(date)
        if result["fixture_count"] == 0:
            public_result = service.scrape_public_sources()
            result["public_scrape"] = public_result
        return maybe_export_precomputed(result, "sync")

    @app.post("/api/refresh/current")
    def refresh_current(date: str):
        if read_only:
            read_only_error("当前数据刷新")
        return maybe_export_precomputed(service.refresh_current_data(date), "refresh_current")

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
        all_finished_results: bool = False,
    ):
        if read_only:
            read_only_error("赛果同步和模型重训")
        result = service.update_after_results(
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
            fetch_all_finished_results=all_finished_results,
        )
        return maybe_export_precomputed(result, "results_update")

    @app.post("/api/odds/sporttery/refresh")
    def refresh_sporttery_odds(
        include_history: bool = False,
        history_start: str = "2026-06-23",
        history_end: str = "2026-06-28",
    ):
        if read_only:
            read_only_error("赔率刷新")
        return maybe_export_precomputed(app.state.service.refresh_sporttery_odds(
            include_history=include_history,
            history_start=history_start,
            history_end=history_end,
        ), "sporttery_refresh")

    @app.post("/api/rounds/sync")
    def sync_round_overview(date: str | None = None):
        if read_only:
            read_only_error("赛程同步")
        return maybe_export_precomputed(service.sync_round_overview(date=date), "round_sync")

    @app.post("/api/rounds/regress")
    def regress_round_overview(date: str | None = None, auto_sync: bool = True):
        if read_only:
            read_only_error("回归训练")
        return maybe_export_precomputed(service.regress_round_overview(date=date, auto_sync=auto_sync), "round_regress")

    @app.post("/api/scrape/public-web")
    def scrape_public_web():
        if read_only:
            read_only_error("公开网页抓取")
        return maybe_export_precomputed(service.scrape_public_sources(), "public_web_scrape")

    @app.post("/api/scrape/reference-site")
    def scrape_reference_site(include_details: bool = True, detail_limit: int = 120):
        if read_only:
            read_only_error("参考站抓取")
        return maybe_export_precomputed(
            service.scrape_lyihub(include_details=include_details, detail_limit=detail_limit),
            "reference_site_scrape",
        )

    @app.post("/api/scrape/lyihub")
    def scrape_lyihub(include_details: bool = True, detail_limit: int = 120):
        if read_only:
            read_only_error("lyihub 抓取")
        return maybe_export_precomputed(
            service.scrape_lyihub(include_details=include_details, detail_limit=detail_limit),
            "lyihub_scrape",
        )

    @app.get("/api/lyihub/matches")
    def lyihub_matches(date: str | None = None, stage: str | None = None):
        if use_precomputed:
            if stage:
                return load_precomputed(f"api/lyihub/matches/stage-{_encode_path_part(stage)}.json")
            if date:
                return load_precomputed(f"api/lyihub/matches/date-{_encode_path_part(date)}.json")
            return load_precomputed("api/lyihub/matches/all.json")
        return service.lyihub_matches(date=date, stage=stage)

    @app.get("/api/lyihub/rounds")
    def lyihub_rounds():
        if use_precomputed:
            return load_precomputed("api/lyihub/rounds.json")
        return service.lyihub_rounds()

    @app.get("/api/lyihub/coverage")
    def lyihub_coverage():
        return service.lyihub_coverage()

    @app.post("/api/predict/{fixture_id}")
    def predict(fixture_id: str, roster_weight: float = 0.25, simulations: int | None = None):
        if use_precomputed:
            return load_precomputed(f"api/predictions/{_encode_path_part(fixture_id)}.json")
        if read_only:
            read_only_error("实时预测")
        try:
            return service.predict_fixture(fixture_id, roster_weight=roster_weight, simulations=simulations)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/reports/daily")
    def daily_report(date: str):
        if use_precomputed:
            return load_precomputed(f"api/reports/daily/{_encode_path_part(date)}.json")
        effective_date = service.default_match_date(date)
        return {"date": effective_date, "requested_date": date, "report": service.daily_report(effective_date)}

    @app.get("/api/health/data-sources")
    def health():
        if use_precomputed:
            return load_precomputed("api/health/data-sources.json")
        return service.data_source_health()

    @app.post("/api/health/validate-sources")
    def validate_sources():
        if read_only:
            read_only_error("数据源校验")
        return service.validate_data_sources()

    @app.post("/api/models/recalibrate")
    def recalibrate_models(date: str):
        if read_only:
            read_only_error("模型校准")
        return service.recalibrate_model_weights(date)

    @app.get("/api/models/weights")
    def model_weights(date: str):
        return service.model_weights_for_date(date)

    @app.get("/api/health/roster-data")
    def roster_health():
        if use_precomputed:
            return load_precomputed("api/health/roster-data.json")
        return service.roster_data_health()

    @app.post("/api/squads/sync")
    def sync_squad(team: str):
        if read_only:
            read_only_error("阵容同步")
        try:
            return service.sync_squad(team)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/squads/sync-all")
    def sync_all_squads():
        if read_only:
            read_only_error("全量阵容同步")
        return service.sync_all_squads()

    @app.post("/api/squads/process-queue")
    def process_roster_queue(limit: int = 20):
        if read_only:
            read_only_error("阵容补全队列")
        return service.process_roster_queue(limit=limit)

    @app.post("/api/squads/enrich-public")
    def enrich_public_roster_queue(limit: int = 20):
        if read_only:
            read_only_error("公开源阵容补全")
        return service.enrich_roster_queue_from_public(limit=limit)

    @app.get("/api/teams/{team}/squad")
    def team_squad(team: str):
        if use_precomputed:
            return load_precomputed(f"api/teams/{_encode_path_part(team)}/squad.json")
        return service.get_team_squad(team, allow_empty=True)

    @app.get("/api/teams/{team}/strength")
    def team_strength(team: str):
        try:
            return service.get_team_strength(team)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/teams/{team}/world-cup-detail")
    def team_world_cup_detail(team: str):
        if use_precomputed:
            return load_precomputed(f"api/teams/{_encode_path_part(team)}/world-cup-detail.json")
        return service.team_world_cup_detail(team)

    @app.get("/api/teams/rankings")
    def team_rankings(alive_only: bool = False):
        if use_precomputed and not alive_only:
            return load_precomputed("api/teams/rankings.json")
        if alive_only:
            return service.simulation_rankings()
        return {"teams": service.team_rankings()}

    @app.get("/api/knockout")
    def knockout():
        if use_precomputed:
            return load_precomputed("api/knockout.json")
        return service.knockout()

    @app.get("/api/meta")
    def meta():
        if use_precomputed:
            payload = load_precomputed("api/meta.json")
        else:
            payload = service.meta()
        payload = dict(payload)
        payload["deployment"] = deployment_meta()
        return payload

    @app.get("/healthz")
    def healthz():
        return {"status": "ok", "deployment": deployment_meta()}

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
