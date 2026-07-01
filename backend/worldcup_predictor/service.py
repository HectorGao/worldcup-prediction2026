from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from .data.providers import ProviderRegistry
from .data.lyihub import LyihubWorldCupScraper, canonical_team
from .data.public_sources import HISTORICAL_RESULTS_URL, WIKIPEDIA_PARSE_URL, PublicWorldCupScraper
from .data.rosters import ApiFootballRosterProvider, TheSportsDBRosterProvider
from .data.sporttery_snapshot import (
    SPORTTERY_LOTTERY_SNAPSHOT,
    sporttery_snapshot_ids,
    sporttery_window_anchor_date,
    sporttery_window_match_start_date,
)
from .data.training import HistoricalMatch, build_team_profiles
from .database import Database
from .prediction.calibration import calibrate_score_matrix_to_market
from .prediction.dixon_coles import (
    btts_probability,
    expected_goals,
    outcome_probabilities,
    score_matrix,
    top_scorelines,
    totals_probability,
)
from .prediction.ensemble import EnsembleConfig, blend_probabilities
from .prediction.handicap import handicap_probabilities, handicap_value_analysis, parse_handicap_line
from .prediction.learning import apply_learning_to_lambdas, rolling_worldcup_adjustment
from .prediction.market import analyze_value, market_bundle, market_from_decimal_odds, unavailable_market
from .prediction.metrics import actual_outcome, evaluate_result
from .prediction.monte_carlo import MonteCarloConfig, simulate_match
from .prediction.odds import devig
from .prediction.poisson_model import PoissonModelConfig, estimate_poisson_prediction, poisson_score_matrix
from .prediction.xgboost_model import (
    build_features,
    estimate_xgboost_prediction,
    monte_carlo_feature_proxy,
    train_xgboost_layer,
)
from .prediction.weight_calibration import calibrate_model_weights, default_weight_run
from .result_sync import (
    collect_world_cup_finished_matches,
    evaluate_world_cup_regression,
    fetch_latest_finished_matches,
    retrain_team_ratings_from_world_cup,
    sync_finished_matches_to_local_store,
    update_knockout_bracket_with_result,
    validate_bracket_after_result_sync,
    write_prediction_outputs,
)
from .roster_strength import USABLE_STATUSES, aggregate_team_strength, player_strength
from .team_metadata import display_team, enrich_fixture, enrich_profile


class WorldCupService:
    def __init__(self, db_path: str | Path = "data/worldcup.sqlite3"):
        self.db = Database(db_path)
        self.providers = ProviderRegistry()
        self.public_scraper = PublicWorldCupScraper()
        self.roster_provider = ApiFootballRosterProvider()
        self.public_roster_provider = TheSportsDBRosterProvider()
        self.lyihub_scraper = LyihubWorldCupScraper()
        self.poisson_config = PoissonModelConfig()
        self.monte_carlo_config = MonteCarloConfig()
        self._xgboost_model_cache: dict[str, Any] = {}

    def sync_date(self, date: str) -> dict[str, Any]:
        source, fixtures = self.providers.fetch_fixtures(date)
        for fixture in fixtures:
            self.db.upsert_fixture(fixture)
        return {
            "date": date,
            "fixture_count": len(fixtures),
            "source": source,
            "fixtures": [self._fixture_response(row) for row in self.db.list_fixtures(date)],
            "sources": self.data_source_health(),
        }

    def count_fixtures(self, date: str) -> int:
        count = self.db.count_fixtures(date)
        if count:
            return count
        fallback_date = self.default_match_date(date)
        return self.db.count_fixtures(fallback_date) if fallback_date != date else 0

    def list_matches(self, date: str) -> list[dict[str, Any]]:
        self.ensure_sporttery_lottery_snapshot()
        if self.db.count_fixtures(date) == 0:
            self.sync_date(date)
        effective_date = date
        if self.db.count_fixtures(effective_date) == 0:
            effective_date = self.default_match_date(date)
        lottery_rows = self._current_sporttery_market_rows(effective_date)
        if self._is_sporttery_window_date(effective_date, lottery_rows):
            return [self._fixture_response(row) for row in lottery_rows]
        lyihub_rows = self.db.list_lyihub_matches(date=effective_date)
        if lyihub_rows:
            return [self._lyihub_match_response(row) for row in lyihub_rows]
        rows = self.db.list_fixtures(effective_date) + self.db.list_web_fixtures(effective_date)
        return [self._fixture_response(row) for row in self._dedupe_match_rows(rows)]

    def list_matches_with_prediction_summary(self, date: str) -> list[dict[str, Any]]:
        matches = self.list_matches(date)
        return [
            match if "prediction_accuracy" in match else self._attach_prediction_summary(match)
            for match in matches
        ]

    def available_dates(self) -> list[str]:
        self.ensure_sporttery_lottery_snapshot()
        dates = set(self.db.available_dates())
        if self._current_sporttery_market_rows(sporttery_window_anchor_date()):
            dates.add(sporttery_window_anchor_date())
        return sorted(dates)

    def _dedupe_match_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deduped: dict[tuple[str, str, str], dict[str, Any]] = {}
        for row in rows:
            key = (row["date"], row["home_team"], row["away_team"])
            current = deduped.get(key)
            if current is None or self._row_quality(row) > self._row_quality(current):
                deduped[key] = row
        return sorted(deduped.values(), key=lambda row: (row["kickoff"], row["id"]))

    def _row_quality(self, row: dict[str, Any]) -> int:
        score = 0
        if row.get("home_score") is not None:
            score += 4
        if row.get("market_home") is not None:
            score += 3
        if row.get("market_over_2_5") is not None:
            score += 2
        if row.get("venue"):
            score += 1
        return score

    def default_match_date(self, requested_date: str) -> str:
        self.ensure_sporttery_lottery_snapshot()
        anchor = sporttery_window_anchor_date()
        match_start = sporttery_window_match_start_date()
        if requested_date <= anchor and self._current_sporttery_market_rows(anchor):
            return anchor
        if requested_date == match_start and self._current_sporttery_market_rows(match_start):
            return match_start
        dates = self.db.available_dates()
        if not dates:
            return requested_date
        if requested_date in dates and self._date_has_upcoming_matches(requested_date):
            return requested_date
        upcoming = [date for date in dates if date >= requested_date and self._date_has_upcoming_matches(date)]
        if upcoming:
            return upcoming[0]
        if requested_date in dates:
            return requested_date
        past_or_today = [date for date in dates if date <= requested_date]
        return past_or_today[-1] if past_or_today else dates[0]

    def ensure_sporttery_lottery_snapshot(self) -> None:
        for market in SPORTTERY_LOTTERY_SNAPSHOT:
            match = self.db.get_lyihub_match_by_fixture(market["fixture_id"])
            if not match:
                continue
            home_elo, away_elo = self._fixture_elos(match)
            fixture = {
                "id": market["fixture_id"],
                "date": match["date"],
                "kickoff": match["kickoff"],
                "home_team": match["home_team"],
                "away_team": match["away_team"],
                "group": match.get("stage") or match.get("group"),
                "venue": match.get("venue"),
                "status": match.get("status", "scheduled"),
                "home_score": match.get("home_score"),
                "away_score": match.get("away_score"),
                "home_elo": home_elo,
                "away_elo": away_elo,
                "market_home": market["spf"].get("home"),
                "market_draw": market["spf"].get("draw"),
                "market_away": market["spf"].get("away"),
                "market_source": f"China Sporttery snapshot {market['match_no']} sales {market['business_date']}",
                "market_handicap": {
                    "home": market["rqspf"].get("home"),
                    "draw": market["rqspf"].get("draw"),
                    "away": market["rqspf"].get("away"),
                },
                "market_handicap_line": market["rqspf"].get("line"),
            }
            self.db.upsert_fixture(fixture)

    def _current_sporttery_market_rows(self, start_date: str) -> list[dict[str, Any]]:
        current_ids = sporttery_snapshot_ids()
        order = {str(market["fixture_id"]): index for index, market in enumerate(SPORTTERY_LOTTERY_SNAPSHOT)}
        rows = self.db.list_market_fixtures(start_date, limit=max(20, len(current_ids) + 4))
        rows = [row for row in rows if str(row.get("id")) in current_ids]
        return sorted(rows, key=lambda row: order.get(str(row.get("id")), 999))

    def _is_sporttery_window_date(self, date: str, rows: list[dict[str, Any]]) -> bool:
        if not rows:
            return False
        ids = {str(row.get("id")) for row in rows}
        return date in {sporttery_window_anchor_date(), sporttery_window_match_start_date()} and sporttery_snapshot_ids().issubset(ids)

    def refresh_current_data(self, date: str, detail_limit: int = 120) -> dict[str, Any]:
        lyihub_result = self.scrape_lyihub(include_details=True, detail_limit=detail_limit)
        live_status = self.refresh_sporttery_odds()
        effective_date = self.default_match_date(date)
        matches = self.list_matches(effective_date)
        return {
            "requested_date": date,
            "date": effective_date,
            "lyihub": lyihub_result,
            "sporttery": live_status,
            "match_count": len(matches),
            "window_dates": sorted({match["date"] for match in matches}),
            "matches": matches,
        }

    def update_after_results(
        self,
        *,
        fetch_online_results: bool = True,
        use_xgboost: bool = True,
        recalculate: bool = True,
        output_dir: str | Path = "outputs",
        date: str | None = None,
    ) -> dict[str, Any]:
        print("[INFO] Fetching latest finished World Cup matches from online sources...")
        target_date = date or datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        today_finished_matches = (
            fetch_latest_finished_matches(target_date=target_date, timezone="Asia/Shanghai")
            if fetch_online_results
            else [match for match in self.db.list_finished_matches() if match.get("date") == target_date]
        )
        if fetch_online_results and not today_finished_matches:
            today_finished_matches = self._fetch_reference_finished_matches(target_date)
        print("[INFO] Using result source: ESPN" if today_finished_matches else "[WARNING] No finished online matches returned.")
        sync_result = sync_finished_matches_to_local_store(today_finished_matches, self.db)
        print("[INFO] Finished matches synced.")
        all_world_cup_matches = collect_world_cup_finished_matches(self.db)
        print("[INFO] Retraining team ratings from all finished World Cup matches.")
        retraining = self._retrain_ratings_from_world_cup(all_world_cup_matches)
        self._xgboost_model_cache.clear()
        print("[INFO] Updating knockout bracket from real winners.")
        bracket = self._bracket_from_local_matches()
        teams = {profile["team"]: profile for profile in self.db.list_team_profiles()}
        for match in all_world_cup_matches:
            update_knockout_bracket_with_result(bracket, match, teams)
        for team, profile in teams.items():
            self.db.save_team_profile(team, profile)
        bracket_validation = validate_bracket_after_result_sync(bracket, teams)
        if bracket_validation["eliminated_future_teams"]:
            print("[WARNING] Eliminated teams remain in future bracket slots and will be excluded from predictions.")
        print("[INFO] Eliminated teams removed from future predictions.")
        if use_xgboost:
            print("[INFO] Checking xgboost installation...")
            xgb_status = self._xgboost_status()
            print(f"[INFO] XGBoost {xgb_status['engine']}.")
        else:
            xgb_status = {"available": False, "engine": "disabled"}
        xgb_status["training_sample_summary"] = self._xgboost_sample_summary(target_date)
        print("[INFO] Recalculating all remaining knockout matches.")
        predictions = self._updated_prediction_rows(recalculate=recalculate, teams=teams)
        regression_evaluation = self._evaluate_world_cup_regression(all_world_cup_matches)
        advanced_teams = sorted(
            {
                str(match.get("winner"))
                for match in today_finished_matches
                if match.get("winner") and self._is_knockout_stage(match.get("stage"))
            }
        )
        eliminated_teams = sorted(
            {
                str(match.get("loser"))
                for match in today_finished_matches
                if match.get("loser") and self._is_knockout_stage(match.get("stage"))
            }
        )
        result_sync_log = {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "target_date": target_date,
            "fetch_online_results": fetch_online_results,
            "sources": ["ESPN"],
            "today_finished_match_count": len(today_finished_matches),
            "today_finished_matches": today_finished_matches,
            "finished_match_count": len(all_world_cup_matches),
            "world_cup_finished_match_count": len(all_world_cup_matches),
            "advanced_teams": advanced_teams,
            "eliminated_teams": eliminated_teams,
            "sync": sync_result,
            "ratings": retraining,
            "bracket_validation": bracket_validation,
            "xgboost": xgb_status,
            "regression_evaluation": {
                key: value
                for key, value in regression_evaluation.items()
                if key != "per_match_errors"
            },
        }
        outputs = write_prediction_outputs(
            output_dir=Path(output_dir),
            predictions=predictions,
            bracket=bracket,
            team_ratings=sorted(teams.values(), key=lambda item: item.get("elo", 0), reverse=True),
            result_sync_log=result_sync_log,
            regression_evaluation=regression_evaluation,
            model_retraining_report=retraining,
        )
        print("[INFO] Saved updated predictions.")
        print("[INFO] Validation completed.")
        return {
            "finished_matches": all_world_cup_matches,
            "today_finished_matches": today_finished_matches,
            "advanced_teams": advanced_teams,
            "eliminated_teams": eliminated_teams,
            "world_cup_finished_match_count": len(all_world_cup_matches),
            "sync": sync_result,
            "ratings": retraining,
            "retraining": retraining,
            "regression_evaluation": regression_evaluation,
            "bracket": bracket,
            "bracket_validation": bracket_validation,
            "xgboost": xgb_status,
            "prediction_count": len(predictions),
            "outputs": outputs,
        }

    def _fetch_reference_finished_matches(self, target_date: str) -> list[dict[str, Any]]:
        try:
            index = self.lyihub_scraper.fetch_index()
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            print(f"[WARNING] lyihub fallback unavailable: {exc}")
            return []
        normalized = [
            self.lyihub_scraper.normalize_index_match(match)
            for match in index.get("matches") or []
        ]
        normalized = self._normalize_lyihub_group_rounds(normalized)
        finished = [
            match
            for match in normalized
            if match.get("date") == target_date
            and self._is_final_row(match)
            and match.get("home_score") is not None
            and match.get("away_score") is not None
        ]
        for match in normalized:
            if match.get("date") == target_date or self.db.get_lyihub_match_by_fixture(match["id"]):
                self.db.upsert_lyihub_match(match)
                self.db.upsert_web_fixture(match, source_name="lyihub_worldcup_static_json")
        return [self._lyihub_finished_match_payload(match) for match in finished]

    def _lyihub_finished_match_payload(self, match: dict[str, Any]) -> dict[str, Any]:
        home_score = match.get("home_score")
        away_score = match.get("away_score")
        winner = loser = None
        if home_score is not None and away_score is not None and home_score != away_score:
            winner = match["home_team"] if home_score > away_score else match["away_team"]
            loser = match["away_team"] if winner == match["home_team"] else match["home_team"]
        return {
            "match_id": f"lyihub-{match['match_id']}",
            "date": match["date"],
            "stage": match.get("stage") or match.get("group"),
            "home_team": match["home_team"],
            "away_team": match["away_team"],
            "home_goals_90": home_score,
            "away_goals_90": away_score,
            "home_goals_extra_time": None,
            "away_goals_extra_time": None,
            "home_penalties": None,
            "away_penalties": None,
            "winner": winner,
            "loser": loser,
            "is_finished": True,
            "decided_by_extra_time": False,
            "decided_by_penalties": False,
            "source": "lyihub_worldcup_static_json",
            "source_url": match.get("source_url"),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

    def sync_round_overview(self, date: str | None = None) -> dict[str, Any]:
        requested_date = date or datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        started_at = datetime.now(timezone.utc).isoformat()
        result_sync = self.update_after_results(
            fetch_online_results=True,
            use_xgboost=True,
            recalculate=True,
            date=requested_date,
        )
        sporttery = self.refresh_sporttery_odds()
        effective_date = self.default_match_date(requested_date)
        predictions = self._sporttery_prediction_briefs(effective_date, before={})
        matches = self.list_matches_with_prediction_summary(effective_date)
        completed_at = datetime.now(timezone.utc).isoformat()
        return {
            "requested_date": requested_date,
            "date": effective_date,
            "started_at": started_at,
            "completed_at": completed_at,
            "last_sync_time": completed_at,
            "result_sync": result_sync,
            "sporttery": {
                **sporttery,
                "checked_at": completed_at,
            },
            "predictions": predictions,
            "matches": matches,
            "summary": {
                "finished_today": len(result_sync.get("today_finished_matches") or []),
                "finished_total": result_sync.get("world_cup_finished_match_count", 0),
                "sporttery_updated": sporttery.get("updated", 0),
                "prediction_count": len(predictions),
            },
        }

    def regress_round_overview(self, date: str | None = None, *, auto_sync: bool = True) -> dict[str, Any]:
        requested_date = date or datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        sync_payload = None
        finished_matches = collect_world_cup_finished_matches(self.db)
        if not finished_matches and auto_sync:
            sync_payload = self.sync_round_overview(requested_date)
            finished_matches = collect_world_cup_finished_matches(self.db)
        if not finished_matches:
            return {
                "requested_date": requested_date,
                "date": self.default_match_date(requested_date),
                "needs_sync": True,
                "message": "还没有可用于回归的本届世界杯完赛样本，请先点击同步。",
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "unfinished_predictions": [],
                "current_sporttery_predictions": [],
            }

        effective_date = self.default_match_date(requested_date)
        before = self._unfinished_prediction_snapshot()
        retraining = self._retrain_ratings_from_world_cup(finished_matches)
        self._xgboost_model_cache.clear()
        weight_run = self.recalibrate_model_weights(effective_date)
        regression_evaluation = self._evaluate_world_cup_regression(finished_matches)
        unfinished_predictions = self._unfinished_prediction_briefs(before=before)
        current_sporttery_predictions = [
            item
            for item in unfinished_predictions
            if item["fixture_id"] in {row["id"] for row in self._sporttery_prediction_rows(effective_date)}
        ]
        completed_at = datetime.now(timezone.utc).isoformat()
        return {
            "requested_date": requested_date,
            "date": effective_date,
            "needs_sync": False,
            "auto_sync": sync_payload is not None,
            "sync": sync_payload,
            "completed_at": completed_at,
            "finished_match_count": len(finished_matches),
            "world_cup_data_weight": retraining.get("world_cup_data_weight"),
            "retraining": retraining,
            "model_weight_run": weight_run,
            "regression_evaluation": regression_evaluation,
            "unfinished_predictions": unfinished_predictions,
            "current_sporttery_predictions": current_sporttery_predictions,
        }

    def _sporttery_prediction_rows(self, date: str) -> list[dict[str, Any]]:
        rows = self._current_sporttery_market_rows(date)
        if rows:
            return rows
        for fallback_date in (sporttery_window_anchor_date(), sporttery_window_match_start_date()):
            rows = self._current_sporttery_market_rows(fallback_date)
            if rows:
                return rows
        return []

    def _sporttery_prediction_briefs(
        self,
        date: str,
        *,
        before: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        output = []
        for row in self._sporttery_prediction_rows(date):
            if self._is_final_row(row):
                output.append(self._finished_brief(row))
                continue
            prediction = self.predict_fixture(str(row["id"]))
            output.append(self._prediction_brief(prediction, before_prediction=before.get(str(row["id"]))))
        return output

    def _unfinished_prediction_snapshot(self) -> dict[str, dict[str, Any]]:
        snapshot = {}
        for row in self._unfinished_rows():
            try:
                snapshot[str(row["id"])] = self.get_prediction(str(row["id"]))
            except (KeyError, ValueError, TypeError, RuntimeError):
                continue
        return snapshot

    def _unfinished_prediction_briefs(self, *, before: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        output = []
        for row in self._unfinished_rows():
            prediction = self.predict_fixture(str(row["id"]))
            output.append(self._prediction_brief(prediction, before_prediction=before.get(str(row["id"]))))
        return output

    def _unfinished_rows(self) -> list[dict[str, Any]]:
        rows = self.db.list_lyihub_matches()
        if not rows:
            rows = [row for date in self.available_dates() for row in self.db.list_web_fixtures(date)]
        seen = set()
        unfinished = []
        for row in rows:
            fixture_id = str(row.get("id") or "")
            if not fixture_id or fixture_id in seen or self._is_final_row(row):
                continue
            seen.add(fixture_id)
            unfinished.append(row)
        return sorted(unfinished, key=lambda item: (str(item.get("kickoff") or ""), str(item.get("id") or "")))

    def _prediction_brief(
        self,
        prediction: dict[str, Any],
        *,
        before_prediction: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        fixture = prediction["fixture"]
        probabilities = prediction.get("probabilities") or {}
        handicap = prediction.get("handicap_analysis") or {}
        ensemble = prediction.get("ensemble") or {}
        monte_carlo = prediction.get("monte_carlo") or {}
        xgboost = prediction.get("xgboost") or {}
        before_probabilities = (before_prediction or {}).get("probabilities") or {}
        probability_delta = {
            key: round(float(probabilities.get(key) or 0) - float(before_probabilities.get(key) or 0), 6)
            for key in ("home", "draw", "away")
            if key in probabilities
        } if before_probabilities else {}
        return {
            "fixture_id": fixture["id"],
            "date": fixture.get("date"),
            "kickoff": fixture.get("kickoff"),
            "stage": fixture.get("stage") or fixture.get("group"),
            "home_team": fixture["home_team"],
            "away_team": fixture["away_team"],
            "home_team_zh": fixture.get("home_team_zh"),
            "away_team_zh": fixture.get("away_team_zh"),
            "home_flag": fixture.get("home_flag"),
            "away_flag": fixture.get("away_flag"),
            "status": fixture.get("status"),
            "probabilities_90": probabilities,
            "handicap_probabilities": handicap.get("model_probabilities") or {},
            "handicap_market_probabilities": handicap.get("market_probabilities") or {},
            "handicap_line": handicap.get("line"),
            "score_heatmap": prediction.get("score_heatmap"),
            "top_scorelines": prediction.get("top_scorelines") or [],
            "advancement_probabilities": self._advancement_probabilities(fixture, probabilities),
            "xgboost": {
                "home": xgboost.get("home_win"),
                "draw": xgboost.get("draw"),
                "away": xgboost.get("away_win"),
            },
            "monte_carlo": {
                "home": monte_carlo.get("home_win"),
                "draw": monte_carlo.get("draw"),
                "away": monte_carlo.get("away_win"),
                "simulations": monte_carlo.get("simulations"),
            },
            "ensemble": {
                "home": ensemble.get("home"),
                "draw": ensemble.get("draw"),
                "away": ensemble.get("away"),
                "recommended_result": ensemble.get("recommended_result"),
                "confidence": ensemble.get("confidence"),
            },
            "recommendation": ensemble.get("recommended_result"),
            "betting_recommendations": prediction.get("betting_recommendations") or [],
            "lottery_market": prediction.get("lottery_market"),
            "odds_markets": prediction.get("odds_markets"),
            "world_cup_data_weight": (prediction.get("model_weight_run") or {}).get("world_cup_data_weight"),
            "probability_delta": probability_delta,
            "calculated_at": datetime.now(timezone.utc).isoformat(),
        }

    def _finished_brief(self, row: dict[str, Any]) -> dict[str, Any]:
        result = next(
            (
                match
                for match in self.db.list_finished_matches()
                if match.get("date") == row.get("date")
                and match.get("home_team") == row.get("home_team")
                and match.get("away_team") == row.get("away_team")
            ),
            {},
        )
        return {
            "fixture_id": row.get("id"),
            "date": row.get("date"),
            "kickoff": row.get("kickoff"),
            "stage": row.get("stage") or row.get("group"),
            "home_team": row.get("home_team"),
            "away_team": row.get("away_team"),
            "status": "final",
            "result": self._finished_output_row(row, result if result.get("match_id") else None),
        }

    def _advancement_probabilities(self, fixture: dict[str, Any], probabilities: dict[str, Any]) -> dict[str, float] | None:
        if not self._is_knockout_stage(fixture.get("stage") or fixture.get("group")):
            return None
        home = float(probabilities.get("home") or 0.0)
        draw = float(probabilities.get("draw") or 0.0)
        away = float(probabilities.get("away") or 0.0)
        return {
            "home": round(home + draw * 0.5, 6),
            "away": round(away + draw * 0.5, 6),
        }

    def _retrain_ratings_from_world_cup(self, finished_matches: list[dict[str, Any]]) -> dict[str, Any]:
        teams = {profile["team"]: profile for profile in self.db.list_team_profiles()}
        result = retrain_team_ratings_from_world_cup(finished_matches, teams)
        for team, profile in result["teams"].items():
            self.db.save_team_profile(team, profile)
        report = dict(result)
        report["team_count"] = len(result["teams"])
        report["teams"] = sorted(result["teams"].values(), key=lambda item: item.get("elo", 0), reverse=True)
        return report

    def _is_knockout_stage(self, stage: Any) -> bool:
        value = str(stage or "").lower()
        return any(
            token in value
            for token in (
                "round of",
                "knockout",
                "quarter",
                "semi",
                "final",
                "third place",
                "1/16",
                "1/8",
                "1/4",
                "半决赛",
                "决赛",
                "淘汰",
            )
        ) and "group" not in value and "小组" not in value

    def _xgboost_status(self) -> dict[str, Any]:
        try:
            from xgboost import XGBClassifier, XGBRegressor  # type: ignore  # noqa: F401

            return {"available": True, "engine": "imported successfully", "classes": ["XGBClassifier", "XGBRegressor"]}
        except Exception as exc:
            return {
                "available": False,
                "engine": "fallback deterministic/softmax layer active",
                "last_error": str(exc),
            }

    def _bracket_from_local_matches(self) -> dict[str, Any]:
        matches = [
            {
                "match_id": row.get("match_id") or row.get("id"),
                "fixture_id": row.get("id"),
                "date": row.get("date"),
                "stage": row.get("stage") or row.get("group"),
                "home_team": row.get("home_team"),
                "away_team": row.get("away_team"),
                "status": row.get("status"),
                "is_finished": self._is_final_row(row),
                "home_score": row.get("home_score"),
                "away_score": row.get("away_score"),
            }
            for row in self.db.list_lyihub_matches()
        ]
        if not matches:
            for date in self.available_dates():
                matches.extend(
                    {
                        "match_id": row.get("id"),
                        "fixture_id": row.get("id"),
                        "date": row.get("date"),
                        "stage": row.get("group_name") or row.get("group"),
                        "home_team": row.get("home_team"),
                        "away_team": row.get("away_team"),
                        "status": row.get("status"),
                        "is_finished": self._is_final_row(row),
                        "home_score": row.get("home_score"),
                        "away_score": row.get("away_score"),
                    }
                    for row in self.db.list_web_fixtures(date)
                )
        return {"matches": matches, "generated_at": datetime.now(timezone.utc).isoformat()}

    def _updated_prediction_rows(self, *, recalculate: bool, teams: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        result_by_key = {
            (row["date"], row["home_team"], row["away_team"]): row
            for row in self.db.list_finished_matches()
        }
        rows = self.db.list_lyihub_matches()
        if not rows:
            rows = [row for date in self.available_dates() for row in self.db.list_web_fixtures(date)]
        output = []
        for row in rows:
            result = result_by_key.get((row["date"], row["home_team"], row["away_team"]))
            if result or self._is_final_row(row):
                output.append(self._finished_output_row(row, result))
                continue
            if teams.get(row["home_team"], {}).get("eliminated") or teams.get(row["away_team"], {}).get("eliminated"):
                continue
            prediction = self.predict_fixture(row["id"]) if recalculate else self.get_prediction(row["id"])
            output.append(self._prediction_output_row(prediction))
        return output

    def _evaluate_world_cup_regression(self, matches: list[dict[str, Any]]) -> dict[str, Any]:
        predictions: dict[str, dict[str, Any]] = {}
        for match in matches:
            match_id = str(match.get("match_id") or "")
            prediction = self._prediction_for_finished_match(match)
            if prediction:
                predictions[match_id] = prediction
        return evaluate_world_cup_regression(matches, predictions)

    def _prediction_for_finished_match(self, match: dict[str, Any]) -> dict[str, Any] | None:
        return self._fallback_regression_prediction(match)

    def _fallback_regression_prediction(self, match: dict[str, Any]) -> dict[str, Any]:
        profiles = {profile["team"]: profile for profile in self.db.list_team_profiles()}
        home = profiles.get(str(match.get("home_team"))) or {}
        away = profiles.get(str(match.get("away_team"))) or {}
        home_strength = float(home.get("strength_rating") or home.get("elo") or 1700)
        away_strength = float(away.get("strength_rating") or away.get("elo") or 1700)
        home_attack = float(home.get("attack_rating") or 1.32)
        away_attack = float(away.get("attack_rating") or 1.32)
        home_defense = float(home.get("defense_rating") or 1.32)
        away_defense = float(away.get("defense_rating") or 1.32)
        home_xg = self._clamp(0.62 * home_attack + 0.38 * away_defense, 0.35, 3.4)
        away_xg = self._clamp(0.62 * away_attack + 0.38 * home_defense, 0.35, 3.4)
        home_logit = (home_strength - away_strength) / 420 + (home_xg - away_xg) * 0.45
        away_logit = -home_logit
        draw_logit = -abs(home_xg - away_xg) * 0.35
        max_logit = max(home_logit, draw_logit, away_logit)
        exps = {
            "home": math.exp(home_logit - max_logit),
            "draw": math.exp(draw_logit - max_logit),
            "away": math.exp(away_logit - max_logit),
        }
        total = sum(exps.values())
        return {
            "fixture": {
                "home_team": match.get("home_team"),
                "away_team": match.get("away_team"),
            },
            "probabilities": {key: value / total for key, value in exps.items()},
            "expected_goals": {"home": home_xg, "away": away_xg},
        }

    def _xgboost_sample_summary(self, date: str) -> dict[str, Any]:
        samples = self._xgboost_training_samples(date)
        if not samples:
            return {"sample_count": 0, "world_cup_samples": 0, "historical_samples": 0}
        world_cup = [sample for sample in samples if sample.get("source_name") != "historical_matches"]
        historical = [sample for sample in samples if sample.get("source_name") == "historical_matches"]
        weights = [float(sample.get("sample_weight", 1.0)) for sample in samples]
        return {
            "sample_count": len(samples),
            "world_cup_samples": len(world_cup),
            "historical_samples": len(historical),
            "min_weight": round(min(weights), 6),
            "max_weight": round(max(weights), 6),
            "mean_weight": round(sum(weights) / len(weights), 6),
            "world_cup_weight_policy": "group=4.0 knockout=5.0 historical=0.75",
        }

    def _finished_output_row(self, row: dict[str, Any], result: dict[str, Any] | None) -> dict[str, Any]:
        result = result or {}
        return {
            "match_id": result.get("match_id") or row.get("match_id") or row.get("id"),
            "stage": result.get("stage") or row.get("stage") or row.get("group"),
            "home_team": row.get("home_team"),
            "away_team": row.get("away_team"),
            "is_finished": True,
            "home_goals_90": result.get("home_goals_90", row.get("home_score")),
            "away_goals_90": result.get("away_goals_90", row.get("away_score")),
            "home_goals_extra_time": result.get("home_goals_extra_time"),
            "away_goals_extra_time": result.get("away_goals_extra_time"),
            "home_penalties": result.get("home_penalties"),
            "away_penalties": result.get("away_penalties"),
            "winner": result.get("winner"),
            "loser": result.get("loser"),
            "decided_by_extra_time": result.get("decided_by_extra_time", False),
            "decided_by_penalties": result.get("decided_by_penalties", False),
            "source": result.get("source") or row.get("source_name"),
            "source_url": result.get("source_url") or row.get("source_url"),
            "fetched_at": result.get("fetched_at"),
        }

    def _prediction_output_row(self, prediction: dict[str, Any]) -> dict[str, Any]:
        fixture = prediction["fixture"]
        top_score = (prediction.get("top_scorelines") or [{}])[0].get("score")
        poisson = prediction.get("poisson") or {}
        mc = prediction.get("monte_carlo") or {}
        xgb = prediction.get("xgboost") or {}
        probs = prediction.get("probabilities") or {}
        return {
            "match_id": fixture["id"],
            "stage": fixture.get("stage") or fixture.get("group"),
            "home_team": fixture["home_team"],
            "away_team": fixture["away_team"],
            "is_finished": False,
            "home_win_90_prob": probs.get("home"),
            "draw_90_prob": probs.get("draw"),
            "away_win_90_prob": probs.get("away"),
            "home_advance_prob": probs.get("home"),
            "away_advance_prob": probs.get("away"),
            "most_likely_score": top_score,
            "score_heatmap": prediction.get("score_heatmap"),
            "poisson_home_win_prob": poisson.get("home_win"),
            "poisson_draw_prob": poisson.get("draw"),
            "poisson_away_win_prob": poisson.get("away_win"),
            "monte_carlo_home_win_prob": mc.get("home_win"),
            "monte_carlo_draw_prob": mc.get("draw"),
            "monte_carlo_away_win_prob": mc.get("away_win"),
            "xgboost_home_win_prob": xgb.get("home_win"),
            "xgboost_draw_prob": xgb.get("draw"),
            "xgboost_away_win_prob": xgb.get("away_win"),
            "xgboost_predicted_result_90": (prediction.get("ensemble") or {}).get("recommended_result"),
            "xgboost_confidence": (prediction.get("ensemble") or {}).get("confidence"),
            "final_home_win_prob": probs.get("home"),
            "final_draw_prob": probs.get("draw"),
            "final_away_win_prob": probs.get("away"),
            "final_recommendation": (prediction.get("ensemble") or {}).get("recommended_result"),
            "world_cup_data_weight": (prediction.get("model_weight_run") or {}).get("world_cup_data_weight"),
            "last_result_sync_time": datetime.now(timezone.utc).isoformat(),
            "result_data_source": "ESPN",
        }

    def refresh_sporttery_odds(self) -> dict[str, Any]:
        self.ensure_sporttery_lottery_snapshot()
        try:
            events = self.providers.sporttery_odds_provider.fetch_odds()
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            return {
                "source": "https://m.sporttery.cn/mjc/jsq/zqspf/",
                "mode": "snapshot_fallback",
                "updated": len(SPORTTERY_LOTTERY_SNAPSHOT),
                "match_numbers": [market["match_no"] for market in SPORTTERY_LOTTERY_SNAPSHOT],
                "last_error": str(exc),
            }
        updated = self._upsert_sporttery_events(events)
        return {
            "source": "https://m.sporttery.cn/mjc/jsq/zqspf/",
            "mode": "live",
            "updated": updated,
            "sample_count": len(events),
        }

    def _upsert_sporttery_events(self, events: list[dict[str, Any]]) -> int:
        updated = 0
        for event in events:
            home_team = canonical_team(str(event.get("home_team") or ""))
            away_team = canonical_team(str(event.get("away_team") or ""))
            fixture = self._find_lyihub_fixture_for_market(home_team, away_team, str(event.get("date") or ""))
            if not fixture:
                continue
            h2h = event.get("h2h") or {}
            handicap = event.get("handicap") or {}
            if not {"home", "draw", "away"} <= set(h2h):
                continue
            home_elo, away_elo = self._fixture_elos(fixture)
            self.db.upsert_fixture(
                {
                    "id": fixture["id"],
                    "date": fixture["date"],
                    "kickoff": fixture["kickoff"],
                    "home_team": fixture["home_team"],
                    "away_team": fixture["away_team"],
                    "group": fixture.get("stage") or fixture.get("group"),
                    "venue": fixture.get("venue"),
                    "status": fixture.get("status", "scheduled"),
                    "home_score": fixture.get("home_score"),
                    "away_score": fixture.get("away_score"),
                    "home_elo": home_elo,
                    "away_elo": away_elo,
                    "market_home": h2h.get("home"),
                    "market_draw": h2h.get("draw"),
                    "market_away": h2h.get("away"),
                    "market_source": f"China Sporttery live {event.get('match_num') or ''}".strip(),
                    "market_handicap": handicap if {"home", "draw", "away"} <= set(handicap) else None,
                    "market_handicap_line": event.get("handicap_line"),
                }
            )
            updated += 1
        return updated

    def _find_lyihub_fixture_for_market(self, home_team: str, away_team: str, date: str) -> dict[str, Any] | None:
        for row in self.db.list_lyihub_matches():
            if date and row.get("date") != date:
                continue
            if row.get("home_team") == home_team and row.get("away_team") == away_team:
                return row
        return None


    def _date_has_upcoming_matches(self, date: str) -> bool:
        lyihub_rows = self.db.list_lyihub_matches(date=date)
        if lyihub_rows:
            return any(not self._is_final_row(row) for row in lyihub_rows)
        rows = self.db.list_fixtures(date) + self.db.list_web_fixtures(date)
        return any(not self._is_final_row(row) for row in rows)

    def _is_final_row(self, row: dict[str, Any]) -> bool:
        return str(row.get("status") or "").lower() == "final" or (
            row.get("home_score") is not None and row.get("away_score") is not None
        )

    def predict_fixture(
        self,
        fixture_id: str,
        roster_weight: float = 0.25,
        simulations: int | None = None,
    ) -> dict[str, Any]:
        fixture = self.db.get_fixture(fixture_id)
        if not fixture:
            fixture = self.db.get_web_fixture(fixture_id)
        if not fixture:
            fixture = self.db.get_lyihub_match_by_fixture(fixture_id)
        if not fixture:
            raise KeyError(f"Unknown fixture: {fixture_id}")

        roster_weight = self._clamp(float(roster_weight), 0.0, 0.5)
        home_profile, away_profile = self._fixture_profiles(fixture)
        roster_strength = {
            "home": self.get_team_strength(fixture["home_team"], allow_empty=True),
            "away": self.get_team_strength(fixture["away_team"], allow_empty=True),
        }
        home_xg, away_xg, model_inputs = self._expected_goals_from_profiles(
            home_profile,
            away_profile,
            home_team=fixture["home_team"],
            away_team=fixture["away_team"],
            roster_strength=roster_strength,
            roster_weight=roster_weight,
        )
        baseline_matrix = self._score_matrix_from_inputs(home_xg, away_xg, model_inputs)
        baseline_outcomes = outcome_probabilities(baseline_matrix)
        market_payload = self._market_payload(fixture)
        odds_markets = self._odds_markets(fixture, market_payload)
        market = self._market_probabilities(fixture)
        totals_market = self._totals_market(fixture)
        calibrated_matrix = (
            calibrate_score_matrix_to_market(
                baseline_matrix,
                market,
                totals_market=totals_market,
                weight=0.35,
            )
            if market
            else baseline_matrix
        )
        calibrated_outcomes = outcome_probabilities(calibrated_matrix)
        recent_matches = self._recent_match_inputs(as_of=fixture.get("date"))
        poisson = estimate_poisson_prediction(
            fixture,
            home_profile,
            away_profile,
            recent_matches,
            config=self.poisson_config,
        )
        learning_adjustment = rolling_worldcup_adjustment(fixture=fixture, completed_matches=recent_matches)
        poisson = self._apply_learning_adjustment_to_poisson(poisson, learning_adjustment)
        poisson = self._apply_roster_adjustment_to_poisson(poisson, model_inputs)
        requested_simulations = int(simulations or self.monte_carlo_config.simulations)
        clamped_simulations = max(1_000, min(100_000, requested_simulations))
        mc_config = MonteCarloConfig(
            simulations=clamped_simulations,
            seed=self.monte_carlo_config.seed,
        )
        monte_carlo = simulate_match(
            poisson["lambda_home"],
            poisson["lambda_away"],
            config=mc_config,
        )
        elo = {
            "home": baseline_outcomes.home,
            "draw": baseline_outcomes.draw,
            "away": baseline_outcomes.away,
            "calibrated": {
                "home": calibrated_outcomes.home,
                "draw": calibrated_outcomes.draw,
                "away": calibrated_outcomes.away,
            },
            "expected_goals": expected_goals(calibrated_matrix),
            "top_scorelines": top_scorelines(calibrated_matrix, limit=6),
        }
        xgboost = estimate_xgboost_prediction(
            fixture=fixture,
            home_profile=home_profile,
            away_profile=away_profile,
            poisson=poisson,
            monte_carlo=monte_carlo,
            market=market_payload,
            roster_strength=roster_strength,
            learning_adjustment=learning_adjustment,
            trained_model=self._trained_xgboost_for_date(str(fixture.get("date") or "")),
        )
        model_weight_run = self.model_weights_for_date(str(fixture.get("date") or ""))
        model_weight_run = {
            **model_weight_run,
            "world_cup_data_weight": max(
                float(home_profile.get("world_cup_data_weight") or 0.0),
                float(away_profile.get("world_cup_data_weight") or 0.0),
            ),
            "xgboost_sample_summary": self._xgboost_sample_summary(str(fixture.get("date") or "")),
        }
        ensemble = blend_probabilities(
            elo={key: elo[key] for key in ("home", "draw", "away")},
            poisson=poisson,
            monte_carlo=monte_carlo,
            market=market_payload,
            xgboost=xgboost,
            config=EnsembleConfig(weights=model_weight_run["weights"]),
        )
        probabilities = {
            "home": ensemble["home"],
            "draw": ensemble["draw"],
            "away": ensemble["away"],
        }
        final_score_matrix = self._serialize_score_matrix(calibrated_matrix)
        final_top_scorelines = top_scorelines(calibrated_matrix, limit=6)
        final_expected_goals = expected_goals(calibrated_matrix)
        risk_warnings = self._dynamic_risk_warnings(
            market_payload=market_payload,
            ensemble=ensemble,
            poisson=poisson,
            xgboost=xgboost,
            model_inputs=model_inputs,
            roster_strength=roster_strength,
            learning_adjustment=learning_adjustment,
        )
        value_analysis = analyze_value(probabilities, market_payload, risk_warnings=risk_warnings)
        handicap_analysis = self._handicap_analysis(final_score_matrix, odds_markets)
        lottery_market = self._lottery_market(fixture, odds_markets)
        score_heatmap = self._score_heatmap(final_score_matrix, handicap_analysis)
        combined_recommendations = list(value_analysis.get("recommended_options", []))
        combined_recommendations.extend(
            {
                **item,
                "market_type": "handicap_1x2",
                "line": handicap_analysis.get("line"),
            }
            for item in (handicap_analysis.get("value_analysis") or {}).get("recommended_options", [])
        )
        evaluation = None
        if fixture["status"] == "final" and fixture["home_score"] is not None:
            evaluation = evaluate_result(probabilities, fixture["home_score"], fixture["away_score"])

        payload = {
            "fixture": self._fixture_response(fixture),
            "source_status": self.data_source_health(),
            "probabilities": probabilities,
            "expected_goals": {
                "home": final_expected_goals["home"],
                "away": final_expected_goals["away"],
            },
            "score_matrix": final_score_matrix,
            "top_scorelines": final_top_scorelines,
            "model_inputs": model_inputs,
            "roster_strength": roster_strength,
            "roster_weight": roster_weight,
            "roster_coverage": {
                "home": roster_strength["home"].get("coverage", 0.0),
                "away": roster_strength["away"].get("coverage", 0.0),
            },
            "missing_player_stats": {
                "home": roster_strength["home"].get("missing_player_stats", 0),
                "away": roster_strength["away"].get("missing_player_stats", 0),
            },
            "btts": poisson["btts"],
            "totals": {
                "1.5": totals_probability(self._matrix_from_serialized(poisson["score_matrix"]), 1.5),
                "2.5": {"over": poisson["over_2_5"], "under": poisson["under_2_5"]},
                "3.5": totals_probability(self._matrix_from_serialized(poisson["score_matrix"]), 3.5),
            },
            "model_blend_weights": {
                "dixon_coles_elo": ensemble["weights"].get("elo", 0.0),
                "poisson": ensemble["weights"].get("poisson", 0.0),
                "monte_carlo": ensemble["weights"].get("monte_carlo", 0.0),
                "market": ensemble["weights"].get("market", 0.0),
                "xgboost": ensemble["weights"].get("xgboost", 0.0),
                "market_calibration": 0.35 if market else 0.0,
                "llm_vote": 0.0,
                "llm_vote_cap": 0.15,
            },
            "model_weight_run": model_weight_run,
            "market_alignment": {
                "available": bool(market),
                "totals_available": bool(totals_market),
                "market_probabilities": market,
                "totals_market": totals_market,
                "baseline_probabilities": {
                    "home": baseline_outcomes.home,
                    "draw": baseline_outcomes.draw,
                    "away": baseline_outcomes.away,
                },
            },
            "odds_data_status": self._odds_data_status(fixture, market_payload, odds_markets),
            "elo": elo,
            "poisson": poisson,
            "monte_carlo": monte_carlo,
            "simulation_request": {
                "requested": requested_simulations,
                "used": clamped_simulations,
                "min": 1_000,
                "max": 100_000,
                "clamped": clamped_simulations != requested_simulations,
            },
            "market": market_payload,
            "market_available": bool(market_payload.get("available")),
            "odds_markets": odds_markets,
            "lottery_market": lottery_market,
            "handicap_analysis": handicap_analysis,
            "score_heatmap": score_heatmap,
            "xgboost": xgboost,
            "ensemble": ensemble,
            "value_analysis": value_analysis,
            "betting_recommendations": combined_recommendations,
            "learning": learning_adjustment,
            "llm_vote_audit": {
                "enabled": False,
                "weight_cap": 0.15,
                "votes": [],
                "note": "多模型投票接口已预留；未配置模型 API 时不影响数值预测。",
            },
            "chinese_report": self._report_for_top_scorelines(fixture, probabilities, poisson["scorelines"], ensemble, value_analysis),
            "post_match_evaluation": evaluation,
        }
        self.db.save_prediction(fixture_id, payload)
        return payload

    def get_prediction(self, fixture_id: str) -> dict[str, Any]:
        prediction = self.db.get_prediction(fixture_id)
        if prediction and prediction.get("ensemble") and prediction.get("poisson") and prediction.get("xgboost"):
            return prediction
        return self.predict_fixture(fixture_id)

    def sync_squad(self, team: str) -> dict[str, Any]:
        squad = self.roster_provider.fetch_squad(team)
        canonical = squad.get("team") or team
        self.db.save_team_squad(canonical, squad)
        saved = self.db.get_team_squad(canonical)
        return {
            "team": canonical,
            "coach": (saved or {}).get("coach"),
            "players_count": len((saved or {}).get("players", [])),
            "source": squad.get("source"),
            "queued": self.db.roster_health()["queue_pending"],
        }

    def sync_all_squads(self) -> dict[str, Any]:
        teams = {fixture["home_team"] for fixture in self.list_matches(self.default_match_date("2026-06-23"))}
        teams.update({fixture["away_team"] for fixture in self.list_matches(self.default_match_date("2026-06-23"))})
        synced = []
        errors = []
        for team in sorted(teams):
            try:
                synced.append(self.sync_squad(team))
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                errors.append({"team": team, "error": str(exc)})
        return {"synced_count": len(synced), "errors": errors, "roster_health": self.roster_data_health()}

    def process_roster_queue(self, limit: int = 20) -> dict[str, Any]:
        queue = self.db.get_next_roster_queue(max(1, min(50, int(limit))))
        processed = 0
        failed = 0
        teams_touched: set[str] = set()
        for item in queue:
            player_id = item["player_id"]
            team = item["team"]
            try:
                stats = self.roster_provider.fetch_player_statistics(int(player_id))
                if stats.get("stats_status") == "complete" or stats.get("season"):
                    stats["stats_status"] = "complete"
                    stats["player_strength"] = player_strength(stats)
                    self.db.save_player_club_stats(player_id, stats)
                    self.db.mark_roster_queue_item(team, player_id, "done")
                    processed += 1
                else:
                    self.db.save_player_club_stats(player_id, stats)
                    self.db.mark_roster_queue_item(team, player_id, "failed", stats.get("last_error"))
                    failed += 1
                teams_touched.add(team)
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                self.db.mark_roster_queue_item(team, player_id, "failed", str(exc))
                failed += 1
        for team in teams_touched:
            self._recompute_squad_strength(team)
        return {
            "requested_limit": limit,
            "processed": processed,
            "failed": failed,
            "remaining": self.db.roster_health()["queue_pending"],
        }

    def enrich_roster_queue_from_public(self, limit: int = 20) -> dict[str, Any]:
        queue = self.db.get_next_roster_queue(max(1, min(10, int(limit))))
        enriched = 0
        failed = 0
        teams_touched: set[str] = set()
        for item in queue:
            player = self._queued_player_payload(item["team"], item["player_id"])
            if not player:
                self.db.mark_roster_queue_item(item["team"], item["player_id"], "failed", "queued player not found")
                failed += 1
                continue
            try:
                stats = self.public_roster_provider.enrich_player(player, item["team"])
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                self.db.mark_roster_queue_item(item["team"], item["player_id"], "failed", str(exc))
                failed += 1
                continue
            if stats.get("stats_status") == "enriched":
                stats["player_strength"] = player_strength(stats)
                self.db.save_player_club_stats(item["player_id"], stats)
                self.db.mark_roster_queue_item(item["team"], item["player_id"], "done")
                enriched += 1
                teams_touched.add(item["team"])
            else:
                self.db.save_player_club_stats(item["player_id"], stats)
                self.db.mark_roster_queue_item(item["team"], item["player_id"], "failed", stats.get("last_error"))
                failed += 1
        for team in teams_touched:
            self._recompute_squad_strength(team)
        return {
            "requested_limit": limit,
            "enriched": enriched,
            "failed": failed,
            "remaining": self.db.roster_health()["queue_pending"],
            "source": self.public_roster_provider.name,
        }

    def get_team_squad(self, team: str, allow_empty: bool = False) -> dict[str, Any]:
        squad = self.db.get_team_squad(team)
        if not squad:
            if allow_empty:
                display = display_team(team)
                return {
                    "team": team,
                    "team_zh": display["zh"],
                    "flag": display["flag"],
                    "available": False,
                    "coach": None,
                    "players": [],
                    "coverage": 0.0,
                    "source": None,
                }
            raise KeyError(f"Unknown squad: {team}")
        players = squad.get("players", [])
        completed = len([player for player in players if player.get("stats_status") in USABLE_STATUSES])
        squad["coverage"] = round(completed / len(players), 4) if players else 0.0
        squad["available"] = True
        return squad

    def get_team_strength(self, team: str, allow_empty: bool = False) -> dict[str, Any]:
        strength = self.db.get_squad_strength(team)
        if strength:
            return strength
        squad = self.db.get_team_squad(team)
        if squad:
            return self._recompute_squad_strength(team)
        if allow_empty:
            return {
                "team": team,
                "attack_strength": 70.0,
                "midfield_control_strength": 70.0,
                "defense_gk_strength": 70.0,
                "coverage": 0.0,
                "missing_player_stats": 0,
                "model_version": "roster-strength-empty",
            }
        raise KeyError(f"Unknown strength: {team}")

    def roster_data_health(self) -> dict[str, Any]:
        health = self.db.roster_health()
        health["provider"] = {
            "name": self.roster_provider.name,
            "configured": self.roster_provider.configured(),
            "docs": "https://www.api-football.com/documentation-v3",
            "season_priority": [2026, 2025, 2024],
            "manual_keys": [
                {
                    "source": "football-data.org",
                    "env_var": "FOOTBALL_DATA_API_KEY",
                    "purpose": "optional fixture/result fallback",
                }
            ],
        }
        health["public_provider"] = {
            "name": self.public_roster_provider.name,
            "configured": self.public_roster_provider.configured(),
            "docs": "https://www.thesportsdb.com/documentation",
            "rate_limit": "30 requests/minute on free key 123",
            "fields": ["club", "league", "position", "photo", "source_confidence"],
        }
        return health

    def recalibrate_model_weights(self, date: str) -> dict[str, Any]:
        samples = self.db.completed_prediction_samples_before(date)
        run = calibrate_model_weights(samples, as_of=date)
        self.db.save_model_weight_run(date, run)
        return run

    def model_weights_for_date(self, date: str) -> dict[str, Any]:
        if not date:
            return default_weight_run("", reason="fixture date missing")
        existing = self.db.latest_model_weight_run(date)
        if existing:
            return existing
        return self.recalibrate_model_weights(date)

    def _trained_xgboost_for_date(self, date: str) -> Any | None:
        if not date:
            return None
        if date not in self._xgboost_model_cache:
            samples = self._xgboost_training_samples(date)
            self._xgboost_model_cache[date] = train_xgboost_layer(samples)
        return self._xgboost_model_cache[date]

    def _xgboost_training_samples(self, as_of: str) -> list[dict[str, Any]]:
        completed_matches = sorted(
            [
                match
                for match in self._recent_match_inputs(as_of=as_of)
                if match.get("home_score") is not None and match.get("away_score") is not None
            ],
            key=lambda item: str(item.get("date") or ""),
        )
        if not completed_matches:
            return []
        profiles = {profile["team"]: profile for profile in self.team_rankings()}
        samples = []
        training_window = completed_matches[-180:]
        for index, match in enumerate(training_window):
            match_date = str(match.get("date") or "")[:10]
            fixture = {
                "id": f"training-{match_date}-{match.get('home_team')}-{match.get('away_team')}",
                "date": match_date,
                "home_team": match["home_team"],
                "away_team": match["away_team"],
                "group": match.get("tournament") or match.get("stage") or "training",
                "home_elo": profiles.get(match["home_team"], {}).get("elo", 1700),
                "away_elo": profiles.get(match["away_team"], {}).get("elo", 1700),
            }
            home_profile = dict(profiles.get(match["home_team"], {}))
            away_profile = dict(profiles.get(match["away_team"], {}))
            home_profile.setdefault("team", match["home_team"])
            away_profile.setdefault("team", match["away_team"])
            home_profile.setdefault("elo", fixture["home_elo"])
            away_profile.setdefault("elo", fixture["away_elo"])
            prior = training_window[:index]
            poisson = self._fast_training_poisson_proxy(
                fixture,
                home_profile,
                away_profile,
                prior,
            )
            learning_adjustment = rolling_worldcup_adjustment(
                fixture=fixture,
                completed_matches=prior,
            )
            features = build_features(
                fixture=fixture,
                home_profile=home_profile,
                away_profile=away_profile,
                poisson=poisson,
                monte_carlo=monte_carlo_feature_proxy(poisson),
                market=None,
                roster_strength={
                    "home": {"attack_strength": 70, "defense_gk_strength": 70},
                    "away": {"attack_strength": 70, "defense_gk_strength": 70},
                },
                learning_adjustment=learning_adjustment,
            )
            samples.append(
                {
                    "date": match_date,
                    "fixture_id": fixture["id"],
                    "features": features,
                    "outcome": actual_outcome(int(match["home_score"]), int(match["away_score"])),
                    "sample_weight": self._training_sample_weight(match),
                    "source_name": match.get("source_name"),
                    "stage": match.get("stage") or match.get("tournament"),
                }
            )
        return samples

    def _training_sample_weight(self, match: dict[str, Any]) -> float:
        source = str(match.get("source_name") or "")
        stage = str(match.get("stage") or match.get("tournament") or "")
        if source == "historical_matches":
            return 0.75
        if self._is_knockout_stage(stage):
            return 5.0
        if "World Cup" in stage or "小组赛" in stage or "group" in stage.lower() or source:
            return 4.0
        return 1.0

    def _fast_training_poisson_proxy(
        self,
        fixture: dict[str, Any],
        home_profile: dict[str, Any],
        away_profile: dict[str, Any],
        prior_matches: list[dict[str, Any]],
    ) -> dict[str, Any]:
        home_recent = self._recent_team_goal_rates(fixture["home_team"], prior_matches)
        away_recent = self._recent_team_goal_rates(fixture["away_team"], prior_matches)
        avg_goals = self.poisson_config.avg_team_goals
        home_attack = 0.58 * float(home_profile.get("attack_rating") or avg_goals) + 0.42 * home_recent["for"]
        away_attack = 0.58 * float(away_profile.get("attack_rating") or avg_goals) + 0.42 * away_recent["for"]
        home_defense_allowed = 0.58 * float(home_profile.get("defense_rating") or avg_goals) + 0.42 * home_recent["against"]
        away_defense_allowed = 0.58 * float(away_profile.get("defense_rating") or avg_goals) + 0.42 * away_recent["against"]
        home_elo = float(home_profile.get("elo") or fixture.get("home_elo") or 1700)
        away_elo = float(away_profile.get("elo") or fixture.get("away_elo") or 1700)
        home_elo_factor = self._clamp(math.exp((home_elo - away_elo) / 950), 0.78, 1.28)
        away_elo_factor = self._clamp(math.exp((away_elo - home_elo) / 950), 0.78, 1.28)
        home_lambda = self._clamp(
            avg_goals * (home_attack / avg_goals) * (away_defense_allowed / avg_goals) * home_elo_factor,
            self.poisson_config.min_lambda,
            self.poisson_config.max_lambda,
        )
        away_lambda = self._clamp(
            avg_goals * (away_attack / avg_goals) * (home_defense_allowed / avg_goals) * away_elo_factor,
            self.poisson_config.min_lambda,
            self.poisson_config.max_lambda,
        )
        matrix = poisson_score_matrix(home_lambda, away_lambda, max_goals=5)
        outcomes = outcome_probabilities(matrix)
        return {
            "lambda_home": round(home_lambda, 4),
            "lambda_away": round(away_lambda, 4),
            "home_win": outcomes.home,
            "draw": outcomes.draw,
            "away_win": outcomes.away,
        }

    def _recent_team_goal_rates(self, team: str, matches: list[dict[str, Any]], limit: int = 10) -> dict[str, float]:
        relevant = [
            match
            for match in reversed(matches)
            if team in {match.get("home_team"), match.get("away_team")}
            and match.get("home_score") is not None
            and match.get("away_score") is not None
        ][:limit]
        if not relevant:
            return {"for": self.poisson_config.avg_team_goals, "against": self.poisson_config.avg_team_goals}
        goals_for = goals_against = weight_sum = 0.0
        for offset, match in enumerate(relevant):
            weight = 0.82**offset
            if match.get("home_team") == team:
                goals_for += float(match["home_score"]) * weight
                goals_against += float(match["away_score"]) * weight
            else:
                goals_for += float(match["away_score"]) * weight
                goals_against += float(match["home_score"]) * weight
            weight_sum += weight
        return {
            "for": goals_for / weight_sum,
            "against": goals_against / weight_sum,
        }

    def match_analysis(self, fixture_id: str) -> dict[str, Any]:
        prediction = self.get_prediction(fixture_id)
        fixture = prediction["fixture"]
        profiles = {profile["team"]: profile for profile in self._team_profiles(compact=False)}
        home_profile = profiles.get(fixture["home_team"], {})
        away_profile = profiles.get(fixture["away_team"], {})
        h2h = home_profile.get("h2h", {}).get(fixture["away_team"], {})
        return {
            "fixture": fixture,
            "prediction": prediction,
            "team_profiles": {
                "home": self._compact_profile(home_profile),
                "away": self._compact_profile(away_profile),
            },
            "head_to_head": h2h,
            "analysis_sections": [
                {
                    "title": "实力对比",
                    "text": (
                        f"{fixture['home_team']} Elo {home_profile.get('elo', '未知')}，"
                        f"{fixture['away_team']} Elo {away_profile.get('elo', '未知')}。"
                    ),
                },
                {
                    "title": "近期状态权重",
                    "text": "模型使用 5 年半衰期，越近的国家队比赛权重越高。",
                },
                {
                    "title": "历史交锋",
                    "text": (
                        f"近可用历史样本 {h2h.get('matches', 0)} 场，"
                        f"加权样本 {h2h.get('weighted_matches', 0):.2f}。"
                    ),
                },
            ],
        }

    def daily_report(self, date: str) -> str:
        matches = self.list_matches(date)
        predictions = [self.get_prediction(match["id"]) for match in matches]
        lines = [f"世界杯比赛预测日报 - {date}", "说明：只做数据分析，不做投注建议。", ""]
        for prediction in predictions:
            fixture = prediction["fixture"]
            probs = prediction["probabilities"]
            best = prediction["top_scorelines"][0]
            lines.append(
            f"{fixture.get('home_flag', '')}{fixture.get('home_team_zh', fixture['home_team'])} vs "
            f"{fixture.get('away_flag', '')}{fixture.get('away_team_zh', fixture['away_team'])}："
                f"胜/平/负 {probs['home']:.1%}/{probs['draw']:.1%}/{probs['away']:.1%}，"
                f"最可能比分 {best['score']} ({best['probability']:.1%})。"
            )
        return "\n".join(lines)

    def data_source_health(self) -> dict[str, Any]:
        return {
            "timezone": "Asia/Shanghai",
            "polling_cadence_minutes": 15,
            "providers": self.providers.health(),
            "public_web_sources": [
                {
                    "name": "Wikipedia 2026 FIFA World Cup + FIFA match-centre links",
                    "configured": True,
                    "role": "fixture_public_web",
                },
                {
                    "name": "martj42 international_results results.csv",
                    "configured": True,
                    "role": "historical_results_public_csv",
                },
                {
                    "name": "Kaggle international football results",
                    "configured": False,
                    "role": "optional_historical_import",
                },
            ],
        }

    def validate_data_sources(self) -> dict[str, Any]:
        return {
            "sources": self.providers.validate_sources()
            + [self.roster_provider.validate(), self.public_roster_provider.validate(), self.lyihub_scraper.validate()]
            + self._validate_public_sources()
        }

    def scrape_public_sources(self) -> dict[str, Any]:
        fixtures = self.public_scraper.fetch_world_cup_fixtures()
        self.save_web_fixtures(fixtures, source_name="wikipedia_fifa_links")
        historical = self.public_scraper.fetch_historical_matches()
        self.save_historical_matches(historical)
        return {
            "fixture_count": len(fixtures),
            "historical_match_count": len(historical),
            "available_dates": self.available_dates(),
            "sources": self.data_source_health(),
        }

    def scrape_lyihub(self, include_details: bool = True, detail_limit: int = 120) -> dict[str, Any]:
        index = self.lyihub_scraper.fetch_index()
        raw_matches = index.get("matches") or []
        normalized = [self.lyihub_scraper.normalize_index_match(match) for match in raw_matches]
        normalized = self._normalize_lyihub_group_rounds(normalized)
        for match in normalized:
            self.db.upsert_lyihub_match(match)
            self.db.upsert_web_fixture(match, source_name="lyihub_worldcup_static_json")

        detail_synced = 0
        detail_errors = []
        if include_details:
            detail_candidates = [match for match in normalized if match.get("has_predict")]
            for match in detail_candidates[: max(0, int(detail_limit))]:
                try:
                    detail = self.lyihub_scraper.fetch_match_detail(match["match_id"])
                    normalized_detail = self.lyihub_scraper.normalize_detail(detail)
                    self.db.save_lyihub_match_detail(normalized_detail)
                    detail_synced += 1
                except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                    detail_errors.append({"match_id": match["match_id"], "error": str(exc)})

        coverage = self.db.lyihub_player_coverage()
        return {
            "source": "https://worldcup.lyihub.com/",
            "match_count": len(normalized),
            "detail_synced": detail_synced,
            "detail_errors": detail_errors[:20],
            "available_dates": self.available_dates(),
            "coverage": coverage,
        }

    def _normalize_lyihub_group_rounds(self, matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
        group_matches = [
            match
            for match in matches
            if "小组赛" in str(match.get("stage") or "") and "第" not in str(match.get("stage") or "")
        ]
        group_matches.sort(key=lambda item: (item.get("kickoff") or "", item.get("match_id") or ""))
        for index, match in enumerate(group_matches):
            round_number = min(3, (index // 2) + 1)
            match["stage"] = f"小组赛 第{round_number}轮"
            match["group"] = match["stage"]
            payload = dict(match.get("payload") or {})
            payload["stage"] = match["stage"]
            match["payload"] = payload
        return matches

    def save_web_fixtures(self, fixtures: list[dict[str, Any]], source_name: str) -> None:
        for fixture in fixtures:
            self.db.upsert_web_fixture(fixture, source_name=source_name)

    def save_historical_matches(self, matches: list[HistoricalMatch]) -> None:
        for match in matches:
            self.db.save_historical_match(match)
        profiles = build_team_profiles(
            matches,
            as_of="2026-06-24",
            half_life_years=5.0,
            max_age_years=16,
        )
        for team, profile in profiles.items():
            self.db.save_team_profile(team, profile)

    def team_rankings(self) -> list[dict[str, Any]]:
        return self._team_profiles(compact=True)

    def lyihub_matches(self, date: str | None = None, stage: str | None = None) -> dict[str, Any]:
        rows = self.db.list_lyihub_matches(date=date, stage=stage)
        matches = [self._lyihub_match_response(row) for row in rows]
        return {
            "date": date,
            "stage": stage or "all",
            "matches": matches,
            "count": len(matches),
        }

    def lyihub_rounds(self) -> dict[str, Any]:
        return {"rounds": self.db.lyihub_stages()}

    def lyihub_coverage(self) -> dict[str, Any]:
        return self.db.lyihub_player_coverage()

    def team_world_cup_detail(self, team: str) -> dict[str, Any]:
        canonical = canonical_team(team)
        matches = [self._lyihub_match_response(match) for match in self.db.lyihub_team_matches(canonical)]
        if not matches and canonical != team:
            matches = [self._lyihub_match_response(match) for match in self.db.lyihub_team_matches(team)]
        players = self._players_with_known_clubs(canonical, self.db.lyihub_team_players(canonical))
        if not players and canonical != team:
            players = self._players_with_known_clubs(canonical, self.db.lyihub_team_players(team))
        return {
            "team": canonical,
            "display": display_team(canonical),
            "matches": matches,
            "players": players,
            "coverage": {
                "matches": len(matches),
                "players": len(players),
                "players_with_ability": len([player for player in players if player.get("ability") is not None]),
                "players_with_source_ability": len(
                    [player for player in players if not player.get("ability_estimated")]
                ),
                "players_with_estimated_ability": len(
                    [player for player in players if player.get("ability_estimated")]
                ),
                "players_with_club": len([player for player in players if player.get("club")]),
            },
        }

    def knockout(self) -> dict[str, Any]:
        teams = self.team_rankings()[:8]
        if not teams:
            return {"champion_favorite": None, "nodes": []}
        champion = teams[0]
        return {
            "champion_favorite": {
                "team": champion["team"],
                "team_zh": champion.get("team_zh"),
                "flag": champion.get("flag"),
                "probability": round(min(0.25, max(0.03, (champion["elo"] - 1500) / 2200)), 4),
            },
            "nodes": [
                {
                    "round": "projected",
                    "favorites": [
                        {
                            "team": team["team"],
                            "team_zh": team.get("team_zh"),
                            "flag": team.get("flag"),
                            "probability": round(min(0.25, max(0.01, (team["elo"] - 1450) / 2500)), 4),
                        }
                        for team in teams[:4]
                    ],
                }
            ],
        }

    def meta(self) -> dict[str, Any]:
        return {
            "last_pipeline_run": None,
            "reference_source": {
                "used_for_data": True,
                "note": "worldcup.lyihub.com 用作用户指定的只读赛程、赛果、球员能力值补充源。",
            },
            "web_sources": {
                "fixture_count": self.db.web_fixture_count(),
                "historical_match_count": self.db.historical_match_count(),
                "available_dates": self.available_dates(),
            },
            "lyihub_coverage": self.lyihub_coverage(),
        }

    def _market_probabilities(self, fixture: dict[str, Any]) -> dict[str, float] | None:
        payload = self._market_payload(fixture)
        if not payload.get("available"):
            return None
        return payload.get("market_probability_no_vig") or payload.get("implied_probability_no_vig")

    def _market_payload(self, fixture: dict[str, Any]) -> dict[str, Any]:
        if not fixture.get("market_home") or not fixture.get("market_draw") or not fixture.get("market_away"):
            return unavailable_market("盘口未配置，或 The Odds API / Betfair / China Sporttery 当前没有该比赛市场。")
        return market_from_decimal_odds(
            {
                "home": fixture["market_home"],
                "draw": fixture["market_draw"],
                "away": fixture["market_away"],
            },
            provider=str(fixture.get("market_source") or "the_odds_api"),
            market_key="h2h",
        )

    def _odds_markets(self, fixture: dict[str, Any], h2h_market: dict[str, Any]) -> dict[str, Any]:
        totals = None
        if fixture.get("market_over_2_5") and fixture.get("market_under_2_5"):
            totals = market_from_decimal_odds(
                {
                    "over": fixture["market_over_2_5"],
                    "under": fixture["market_under_2_5"],
                },
                provider=str(fixture.get("market_source") or "the_odds_api"),
                market_key="totals_2_5",
            )
        handicap = unavailable_market("让球胜平负盘口不可用：当前数据源未返回 handicap market。")
        if fixture.get("market_handicap"):
            handicap_payload = fixture.get("market_handicap")
            if isinstance(handicap_payload, str):
                try:
                    import json

                    handicap_payload = json.loads(handicap_payload)
                except (TypeError, ValueError):
                    handicap_payload = None
            if isinstance(handicap_payload, dict) and {"home", "draw", "away"} <= set(handicap_payload):
                handicap = market_from_decimal_odds(
                    {
                        "home": handicap_payload["home"],
                        "draw": handicap_payload["draw"],
                        "away": handicap_payload["away"],
                    },
                    provider=str(fixture.get("market_source") or "the_odds_api"),
                    market_key="handicap_1x2",
                )
                handicap["line"] = fixture.get("market_handicap_line")
        return market_bundle(
            h2h=h2h_market,
            handicap=handicap,
            totals=totals,
        )

    def _lottery_market(self, fixture: dict[str, Any], odds_markets: dict[str, Any]) -> dict[str, Any]:
        h2h = odds_markets.get("h2h") or {}
        handicap = odds_markets.get("handicap") or {}
        available = bool(h2h.get("available") or handicap.get("available"))
        if not available:
            return {
                "available": False,
                "reason": "no_sporttery_worldcup_match_found",
            }
        snapshot = next((item for item in SPORTTERY_LOTTERY_SNAPSHOT if item["fixture_id"] == fixture.get("id")), {})
        return {
            "available": True,
            "source": fixture.get("market_source") or h2h.get("provider") or handicap.get("provider"),
            "match_no": fixture.get("market_match_no") or snapshot.get("match_no"),
            "league": fixture.get("market_league") or snapshot.get("league") or fixture.get("group_name") or fixture.get("group"),
            "sale_status": "selling",
            "business_date": snapshot.get("business_date"),
            "kickoff_time": fixture.get("kickoff"),
            "spf": self._lottery_odds(h2h) if h2h.get("available") else None,
            "rqspf": self._lottery_odds(handicap, line=handicap.get("line")) if handicap.get("available") else None,
            "updated_at": h2h.get("checked_at") or handicap.get("checked_at"),
        }

    def _lottery_odds(self, market: dict[str, Any], line: Any = None) -> dict[str, Any]:
        odds = market.get("odds") or {}
        payload = {
            "home_win": odds.get("home"),
            "draw": odds.get("draw"),
            "away_win": odds.get("away"),
        }
        if line is not None:
            payload["handicap"] = line
        return payload

    def _handicap_analysis(self, serialized_matrix: list[dict[str, Any]], odds_markets: dict[str, Any]) -> dict[str, Any]:
        market = odds_markets.get("handicap") or {}
        line = parse_handicap_line(market.get("line"))
        if not market.get("available") or line is None:
            return {
                "available": False,
                "reason": market.get("reason") or "让球胜平负盘口不可用。",
                "line": market.get("line"),
                "model_probabilities": {},
                "market_probabilities": {},
                "value_analysis": {"available": False, "items": [], "recommended_options": []},
            }
        model = handicap_probabilities(self._matrix_from_serialized(serialized_matrix), line)
        value = handicap_value_analysis(model["probabilities"], market)
        return {
            "available": True,
            "line": line,
            "model_probabilities": model["probabilities"],
            "market_probabilities": market.get("market_probability_no_vig") or market.get("implied_probability_no_vig") or {},
            "odds": market.get("odds") or {},
            "tail_probability": model["tail_probability"],
            "tail_note": model["tail_note"],
            "regions": model["regions"],
            "value_analysis": value,
        }

    def _score_heatmap(
        self,
        serialized_matrix: list[dict[str, Any]],
        handicap_analysis: dict[str, Any],
    ) -> dict[str, Any]:
        regions = {
            f"{item['home_goals']}-{item['away_goals']}": item["outcome"]
            for item in handicap_analysis.get("regions", [])
        }
        return {
            "matrix": serialized_matrix,
            "handicap": handicap_analysis.get("line"),
            "handicap_probabilities": handicap_analysis.get("model_probabilities"),
            "handicap_market_probabilities": handicap_analysis.get("market_probabilities"),
            "handicap_regions": regions,
            "tail_probability": handicap_analysis.get("tail_probability"),
            "tail_note": handicap_analysis.get("tail_note"),
        }

    def _odds_data_status(
        self,
        fixture: dict[str, Any],
        h2h_market: dict[str, Any],
        odds_markets: dict[str, Any],
    ) -> dict[str, Any]:
        providers = [
            provider
            for provider in self.providers.health()
            if provider.get("role") in {"odds_paid", "odds_optional_exchange", "official_cn_odds_public_web_fallback"}
        ]
        available_markets = [
            key
            for key in ("h2h", "handicap", "totals")
            if (odds_markets.get(key) or {}).get("available")
        ]
        return {
            "market_available": bool(h2h_market.get("available")),
            "source": fixture.get("market_source") or h2h_market.get("provider"),
            "available_markets": available_markets,
            "providers": providers,
            "reason": None if h2h_market.get("available") else h2h_market.get("reason"),
            "sporttery_priority_note": "China Sporttery overrides other odds if live access is enabled and a match is found.",
        }

    def _totals_market(self, fixture: dict[str, Any]) -> dict[str, float] | None:
        if not fixture.get("market_over_2_5") or not fixture.get("market_under_2_5"):
            return None
        fair = devig(
            {
                "over": fixture["market_over_2_5"],
                "under": fixture["market_under_2_5"],
            }
        )
        return {"over": fair["over"], "under": fair["under"], "line": 2.5}

    def _fixture_elos(self, fixture: dict[str, Any]) -> tuple[float, float]:
        rankings = {profile["team"]: profile for profile in self.team_rankings()}
        home = rankings.get(fixture["home_team"], {})
        away = rankings.get(fixture["away_team"], {})
        return (
            float(fixture.get("home_elo") or home.get("elo") or 1700),
            float(fixture.get("away_elo") or away.get("elo") or 1700),
        )

    def _fixture_profiles(self, fixture: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        profiles = {profile["team"]: profile for profile in self.team_rankings()}
        home = dict(profiles.get(fixture["home_team"], {}))
        away = dict(profiles.get(fixture["away_team"], {}))
        home.setdefault("team", fixture["home_team"])
        away.setdefault("team", fixture["away_team"])
        home.setdefault("elo", fixture.get("home_elo") or 1700)
        away.setdefault("elo", fixture.get("away_elo") or 1700)
        return home, away

    def _team_profiles(self, compact: bool) -> list[dict[str, Any]]:
        profiles = self.db.list_team_profiles()
        if not compact:
            return profiles
        return [self._compact_profile(profile) for profile in profiles]

    def _compact_profile(self, profile: dict[str, Any]) -> dict[str, Any]:
        fields = {
            "team",
            "elo",
            "attack_rating",
            "defense_rating",
            "recent_weighted_matches",
            "wins",
            "draws",
            "losses",
        }
        return enrich_profile({key: profile.get(key) for key in fields if key in profile})

    def _expected_goals_from_elo(self, home_elo: float, away_elo: float) -> tuple[float, float]:
        delta = (home_elo + 45 - away_elo) / 400
        home_xg = min(2.8, max(0.45, 1.35 + 0.55 * delta))
        away_xg = min(2.8, max(0.45, 1.15 - 0.50 * delta))
        return home_xg, away_xg

    def _expected_goals_from_profiles(
        self,
        home_profile: dict[str, Any],
        away_profile: dict[str, Any],
        home_team: str | None = None,
        away_team: str | None = None,
        roster_strength: dict[str, dict[str, Any]] | None = None,
        roster_weight: float = 0.25,
    ) -> tuple[float, float, dict[str, Any]]:
        avg_team_goals = 1.32
        global_attack_avg = 1.45
        global_defense_avg = 1.05
        home_team = home_team or str(home_profile.get("team") or "Home")
        away_team = away_team or str(away_profile.get("team") or "Away")
        home_elo = float(home_profile.get("elo") or 1700)
        away_elo = float(away_profile.get("elo") or 1700)

        home_attack = self._regressed_factor(
            float(home_profile.get("attack_rating") or global_attack_avg),
            global_attack_avg,
            float(home_profile.get("recent_weighted_matches") or 0),
        )
        away_attack = self._regressed_factor(
            float(away_profile.get("attack_rating") or global_attack_avg),
            global_attack_avg,
            float(away_profile.get("recent_weighted_matches") or 0),
        )
        home_defense_allowed = self._regressed_factor(
            float(home_profile.get("defense_rating") or global_defense_avg),
            global_defense_avg,
            float(home_profile.get("recent_weighted_matches") or 0),
        )
        away_defense_allowed = self._regressed_factor(
            float(away_profile.get("defense_rating") or global_defense_avg),
            global_defense_avg,
            float(away_profile.get("recent_weighted_matches") or 0),
        )

        host_adjustment = self._host_adjustment(home_team, away_team)
        elo_factor_home = self._clamp(math.exp((home_elo + host_adjustment - away_elo) / 650), 0.75, 1.35)
        elo_factor_away = self._clamp(math.exp((away_elo - home_elo - host_adjustment) / 650), 0.75, 1.35)

        home_xg = self._clamp(avg_team_goals * home_attack * away_defense_allowed * elo_factor_home, 0.35, 3.4)
        away_xg = self._clamp(avg_team_goals * away_attack * home_defense_allowed * elo_factor_away, 0.35, 3.4)
        home_multiplier, away_multiplier, roster_adjustment = self._roster_xg_multipliers(
            roster_strength,
            roster_weight,
        )
        home_xg = self._clamp(home_xg * home_multiplier, 0.35, 3.4)
        away_xg = self._clamp(away_xg * away_multiplier, 0.35, 3.4)
        rho = self._dynamic_rho(home_xg + away_xg)
        inputs = {
            "avg_team_goals": avg_team_goals,
            "rho": rho,
            "neutral_site": True,
            "host_adjustment": host_adjustment,
            "roster_weight": roster_weight,
            "roster_adjustment": roster_adjustment,
            "home": {
                "team": home_team,
                "elo": home_elo,
                "attack_factor": round(home_attack, 4),
                "opponent_defense_factor": round(away_defense_allowed, 4),
                "elo_factor": round(elo_factor_home, 4),
                "sample_confidence": round(min(1.0, float(home_profile.get("recent_weighted_matches") or 0) / 30), 4),
                "xg": round(home_xg, 4),
            },
            "away": {
                "team": away_team,
                "elo": away_elo,
                "attack_factor": round(away_attack, 4),
                "opponent_defense_factor": round(home_defense_allowed, 4),
                "elo_factor": round(elo_factor_away, 4),
                "sample_confidence": round(min(1.0, float(away_profile.get("recent_weighted_matches") or 0) / 30), 4),
                "xg": round(away_xg, 4),
            },
        }
        return home_xg, away_xg, inputs

    def _roster_xg_multipliers(
        self,
        roster_strength: dict[str, dict[str, Any]] | None,
        roster_weight: float,
    ) -> tuple[float, float, dict[str, Any]]:
        if not roster_strength or roster_weight <= 0:
            return 1.0, 1.0, {
                "available": False,
                "home_xg_multiplier": 1.0,
                "away_xg_multiplier": 1.0,
            }
        home = roster_strength.get("home") or {}
        away = roster_strength.get("away") or {}
        home_coverage = float(home.get("coverage") or 0)
        away_coverage = float(away.get("coverage") or 0)
        coverage = min(home_coverage, away_coverage)
        if coverage <= 0:
            return 1.0, 1.0, {
                "available": False,
                "home_xg_multiplier": 1.0,
                "away_xg_multiplier": 1.0,
                "coverage": 0.0,
            }
        home_delta = (
            (float(home.get("attack_strength", 70)) - float(away.get("defense_gk_strength", 70))) * 0.010
            + (float(home.get("midfield_control_strength", 70)) - float(away.get("midfield_control_strength", 70)))
            * 0.004
        )
        away_delta = (
            (float(away.get("attack_strength", 70)) - float(home.get("defense_gk_strength", 70))) * 0.010
            + (float(away.get("midfield_control_strength", 70)) - float(home.get("midfield_control_strength", 70)))
            * 0.004
        )
        home_multiplier = self._clamp(1 + roster_weight * coverage * home_delta, 0.82, 1.24)
        away_multiplier = self._clamp(1 + roster_weight * coverage * away_delta, 0.82, 1.24)
        return home_multiplier, away_multiplier, {
            "available": True,
            "coverage": round(coverage, 4),
            "home_xg_multiplier": round(home_multiplier, 4),
            "away_xg_multiplier": round(away_multiplier, 4),
            "home_attack_vs_away_defense": round(home_delta, 4),
            "away_attack_vs_home_defense": round(away_delta, 4),
        }

    def _recompute_squad_strength(self, team: str) -> dict[str, Any]:
        squad = self.db.get_team_squad(team)
        if not squad:
            raise KeyError(f"Unknown squad: {team}")
        strength = aggregate_team_strength(team, squad.get("players", []))
        self.db.save_squad_strength(team, strength)
        return strength

    def _queued_player_payload(self, team: str, player_id: str) -> dict[str, Any] | None:
        squad = self.db.get_team_squad(team)
        if not squad:
            return None
        return next((player for player in squad.get("players", []) if str(player.get("player_id")) == str(player_id)), None)

    def _score_matrix_from_inputs(
        self,
        home_xg: float,
        away_xg: float,
        model_inputs: dict[str, Any],
    ) -> dict[tuple[Any, Any], float]:
        return score_matrix(home_xg=home_xg, away_xg=away_xg, rho=model_inputs.get("rho", -0.025), max_goals=7)

    def _apply_learning_adjustment_to_poisson(
        self,
        poisson: dict[str, Any],
        learning_adjustment: dict[str, Any],
    ) -> dict[str, Any]:
        if not learning_adjustment.get("available"):
            return poisson
        home_lambda, away_lambda = apply_learning_to_lambdas(
            float(poisson["lambda_home"]),
            float(poisson["lambda_away"]),
            learning_adjustment,
            min_lambda=self.poisson_config.min_lambda,
            max_lambda=self.poisson_config.max_lambda,
        )
        matrix = poisson_score_matrix(home_lambda, away_lambda, max_goals=self.poisson_config.max_goals)
        outcomes = outcome_probabilities(matrix)
        totals = totals_probability(matrix, 2.5)
        adjusted = dict(poisson)
        adjusted.update(
            {
                "lambda_home": round(home_lambda, 4),
                "lambda_away": round(away_lambda, 4),
                "home_win": outcomes.home,
                "draw": outcomes.draw,
                "away_win": outcomes.away,
                "over_2_5": totals["over"],
                "under_2_5": totals["under"],
                "btts": btts_probability(matrix),
                "scorelines": top_scorelines(matrix, limit=6),
                "score_matrix": self._serialize_score_matrix(matrix),
                "tail_probability": matrix.get((f"{self.poisson_config.max_goals + 1}+", f"{self.poisson_config.max_goals + 1}+"), 0.0),
            }
        )
        adjusted.setdefault("model_explanation", {})["learning_adjustment"] = learning_adjustment
        return adjusted

    def _apply_roster_adjustment_to_poisson(
        self,
        poisson: dict[str, Any],
        model_inputs: dict[str, Any],
    ) -> dict[str, Any]:
        adjustment = model_inputs.get("roster_adjustment") or {}
        if not adjustment.get("available"):
            return poisson
        home_lambda = self._clamp(
            float(poisson["lambda_home"]) * float(adjustment.get("home_xg_multiplier") or 1.0),
            self.poisson_config.min_lambda,
            self.poisson_config.max_lambda,
        )
        away_lambda = self._clamp(
            float(poisson["lambda_away"]) * float(adjustment.get("away_xg_multiplier") or 1.0),
            self.poisson_config.min_lambda,
            self.poisson_config.max_lambda,
        )
        matrix = poisson_score_matrix(home_lambda, away_lambda, max_goals=self.poisson_config.max_goals)
        outcomes = outcome_probabilities(matrix)
        totals = totals_probability(matrix, 2.5)
        adjusted = dict(poisson)
        adjusted.update(
            {
                "lambda_home": round(home_lambda, 4),
                "lambda_away": round(away_lambda, 4),
                "home_win": outcomes.home,
                "draw": outcomes.draw,
                "away_win": outcomes.away,
                "over_2_5": totals["over"],
                "under_2_5": totals["under"],
                "btts": btts_probability(matrix),
                "scorelines": top_scorelines(matrix, limit=6),
                "score_matrix": self._serialize_score_matrix(matrix),
                "tail_probability": matrix.get((f"{self.poisson_config.max_goals + 1}+", f"{self.poisson_config.max_goals + 1}+"), 0.0),
            }
        )
        adjusted.setdefault("model_explanation", {})["roster_adjustment"] = adjustment
        return adjusted

    def _regressed_factor(self, value: float, average: float, weighted_matches: float) -> float:
        confidence = min(1.0, max(0.0, weighted_matches / 30))
        return ((confidence * value) + ((1 - confidence) * average)) / average

    def _dynamic_rho(self, total_xg: float) -> float:
        if total_xg < 2.2:
            return -0.035
        if total_xg < 2.6:
            return -0.02
        return -0.005

    def _host_adjustment(self, home_team: str, away_team: str) -> float:
        hosts = {"United States", "Mexico", "Canada"}
        if home_team in hosts:
            return 25.0
        if away_team in hosts:
            return -25.0
        return 0.0

    def _clamp(self, value: float, low: float, high: float) -> float:
        return min(high, max(low, value))

    def _recent_match_inputs(self, as_of: str | None = None) -> list[dict[str, Any]]:
        since = None
        if as_of:
            try:
                since = (datetime.fromisoformat(str(as_of)[:10]) - timedelta(days=400)).date().isoformat()
            except ValueError:
                since = None
        matches = [
            {
                "date": row["date"],
                "home_team": row["home_team"],
                "away_team": row["away_team"],
                "home_score": row["home_score"],
                "away_score": row["away_score"],
                "tournament": row.get("tournament") or "historical",
                "source_name": "historical_matches",
            }
            for row in self.db.list_historical_matches(since=since)
        ]
        matches.extend(
            {
                "date": row["date"],
                "home_team": row["home_team"],
                "away_team": row["away_team"],
                "home_score": row.get("home_score"),
                "away_score": row.get("away_score"),
                "tournament": row.get("stage") or "World Cup",
                "stage": row.get("stage"),
                "source_name": row.get("source_name") or "lyihub_worldcup_static_json",
            }
            for row in self.db.list_lyihub_matches()
            if row.get("status") == "final" and row.get("home_score") is not None and row.get("away_score") is not None
        )
        matches.extend(
            {
                "date": row["date"],
                "home_team": row["home_team"],
                "away_team": row["away_team"],
                "home_score": row.get("home_goals_90"),
                "away_score": row.get("away_goals_90"),
                "tournament": row.get("stage") or "World Cup",
                "stage": row.get("stage"),
                "source_name": row.get("source") or "finished_match_results",
            }
            for row in self.db.list_finished_matches()
            if row.get("home_goals_90") is not None and row.get("away_goals_90") is not None
        )
        if not as_of:
            return matches
        return [match for match in matches if str(match.get("date") or "") < as_of]

    def _dynamic_risk_warnings(
        self,
        *,
        market_payload: dict[str, Any],
        ensemble: dict[str, Any],
        poisson: dict[str, Any],
        xgboost: dict[str, Any],
        model_inputs: dict[str, Any],
        roster_strength: dict[str, dict[str, Any]],
        learning_adjustment: dict[str, Any],
    ) -> list[str]:
        warnings = []
        if not market_payload.get("available"):
            warnings.append("盘口缺失")
        source_probs = ensemble.get("source_probabilities") or {}
        winners = {
            name: max(probabilities.items(), key=lambda item: item[1])[0]
            for name, probabilities in source_probs.items()
            if isinstance(probabilities, dict) and probabilities
        }
        if len(set(winners.values())) > 1:
            warnings.append("模型分歧")
        if (poisson.get("model_explanation") or {}).get("stage_bucket") == "group_round_1":
            warnings.append("首轮保守系数影响")
        home_sample = float((poisson.get("model_explanation") or {}).get("home_recent_sample") or 0)
        away_sample = float((poisson.get("model_explanation") or {}).get("away_recent_sample") or 0)
        if min(home_sample, away_sample) < 1.0:
            warnings.append("近期样本不足")
        if min(float((roster_strength.get("home") or {}).get("coverage") or 0), float((roster_strength.get("away") or {}).get("coverage") or 0)) < 0.6:
            warnings.append("roster 不完整")
        if not learning_adjustment.get("available"):
            warnings.append("本届世界杯可学习样本不足")
        if not warnings:
            warnings.append("主要模型信号一致")
        return warnings

    def _validate_public_sources(self) -> list[dict[str, Any]]:
        return [
            self._validate_wikipedia_fixture_source(),
            self._validate_http_source(
                "martj42 international_results CSV",
                HISTORICAL_RESULTS_URL,
                sample_text="date,home_team,away_team",
            ),
            self._validate_http_source(
                "TheSportsDB free teams API",
                "https://www.thesportsdb.com/api/v1/json/3/searchteams.php",
                params={"t": "France"},
                sample_key=("teams",),
            ),
        ]

    def _validate_http_source(
        self,
        name: str,
        url: str,
        params: dict[str, str] | None = None,
        sample_key: tuple[str, ...] | None = None,
        sample_text: str | None = None,
    ) -> dict[str, Any]:
        checked_at = datetime.now(timezone.utc).isoformat()
        try:
            response = httpx.get(url, params=params, timeout=20, follow_redirects=True)
            reachable = response.status_code < 500
            auth_valid = response.status_code < 400
            sample_count = 0
            if auth_valid and sample_key:
                payload: Any = response.json()
                for key in sample_key:
                    payload = payload.get(key) if isinstance(payload, dict) else None
                if isinstance(payload, list):
                    sample_count = len(payload)
                elif payload:
                    sample_count = 1
            elif auth_valid and sample_text:
                sample_count = 1 if sample_text in response.text[:5000] else 0
            return {
                "name": name,
                "configured": True,
                "reachable": reachable,
                "auth_valid": auth_valid,
                "quota_remaining": None,
                "sample_count": sample_count,
                "last_error": None if auth_valid else f"HTTP {response.status_code}",
                "checked_at": checked_at,
            }
        except (httpx.HTTPError, ValueError) as exc:
            return {
                "name": name,
                "configured": True,
                "reachable": False,
                "auth_valid": False,
                "quota_remaining": None,
                "sample_count": 0,
                "last_error": str(exc),
                "checked_at": checked_at,
            }

    def _validate_wikipedia_fixture_source(self) -> dict[str, Any]:
        checked_at = datetime.now(timezone.utc).isoformat()
        try:
            payload = self.public_scraper._get_json(
                WIKIPEDIA_PARSE_URL,
                params={
                    "action": "parse",
                    "page": "2026_FIFA_World_Cup",
                    "prop": "text",
                    "format": "json",
                },
            )
            html_text = payload["parse"]["text"]["*"]
            fixtures = self.public_scraper.parse_wikipedia_world_cup(html_text)
            return {
                "name": "Wikipedia/FIFA public fixture scrape",
                "configured": True,
                "reachable": True,
                "auth_valid": True,
                "quota_remaining": None,
                "sample_count": len(fixtures),
                "last_error": None,
                "checked_at": checked_at,
            }
        except (KeyError, ValueError, httpx.HTTPError, RuntimeError) as exc:
            return {
                "name": "Wikipedia/FIFA public fixture scrape",
                "configured": True,
                "reachable": False,
                "auth_valid": False,
                "quota_remaining": None,
                "sample_count": 0,
                "last_error": str(exc),
                "checked_at": checked_at,
            }

    def _fixture_response(self, row: dict[str, Any]) -> dict[str, Any]:
        return enrich_fixture({
            "id": row["id"],
            "date": row["date"],
            "kickoff": row["kickoff"],
            "home_team": row["home_team"],
            "away_team": row["away_team"],
            "group": row.get("group_name") or row.get("group"),
            "venue": row.get("venue"),
            "status": row["status"],
            "home_score": row.get("home_score"),
            "away_score": row.get("away_score"),
            "source_url": row.get("source_url"),
            "source_name": row.get("source_name"),
            "market_home": row.get("market_home"),
            "market_draw": row.get("market_draw"),
            "market_away": row.get("market_away"),
            "market_over_2_5": row.get("market_over_2_5"),
            "market_under_2_5": row.get("market_under_2_5"),
            "market_source": row.get("market_source"),
            "market_handicap": row.get("market_handicap"),
            "market_handicap_line": row.get("market_handicap_line"),
        })

    def _lyihub_match_response(self, row: dict[str, Any]) -> dict[str, Any]:
        response = enrich_fixture(
            {
                "id": row["id"],
                "date": row["date"],
                "kickoff": row["kickoff"],
                "home_team": row["home_team"],
                "away_team": row["away_team"],
                "group": row.get("stage") or row.get("group"),
                "venue": row.get("venue"),
                "status": row["status"],
                "home_score": row.get("home_score"),
                "away_score": row.get("away_score"),
                "source_url": row.get("source_url"),
                "source_name": row.get("source_name"),
            }
        )
        response.update(
            {
                "match_id": row.get("match_id"),
                "stage": row.get("stage"),
                "has_predict": row.get("has_predict"),
                "source_home_team_zh": row.get("home_team_zh"),
                "source_away_team_zh": row.get("away_team_zh"),
            }
        )
        return self._attach_prediction_summary(response)

    def _attach_prediction_summary(self, match: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(match)
        actual_score = self._actual_score(match)
        enriched["actual_score"] = actual_score
        finished_result = self._finished_result_for_row(match)
        if finished_result:
            enriched["finished_result"] = self._finished_output_row(match, finished_result)
        fixture_odds_markets = self._odds_markets(match, self._market_payload(match))
        if (fixture_odds_markets.get("h2h") or {}).get("available") or (fixture_odds_markets.get("handicap") or {}).get("available"):
            enriched["odds_markets"] = fixture_odds_markets
            enriched["lottery_market"] = self._lottery_market(match, fixture_odds_markets)
            enriched["odds_data_status"] = self._odds_data_status(match, self._market_payload(match), fixture_odds_markets)
        try:
            prediction = self.get_prediction(match["id"])
            top = prediction.get("top_scorelines", [{}])[0]
            predicted_score = top.get("score")
            enriched.setdefault("odds_markets", prediction.get("odds_markets"))
            enriched.setdefault("lottery_market", prediction.get("lottery_market"))
            enriched.setdefault("odds_data_status", prediction.get("odds_data_status"))
            enriched["value_analysis"] = prediction.get("value_analysis")
            enriched["handicap_analysis"] = prediction.get("handicap_analysis")
        except (KeyError, ValueError, TypeError, RuntimeError):
            predicted_score = None
        enriched["predicted_score"] = predicted_score
        enriched["prediction_accuracy"] = self._prediction_accuracy(predicted_score, actual_score)
        return enriched

    def _finished_result_for_row(self, row: dict[str, Any]) -> dict[str, Any] | None:
        for match in self.db.list_finished_matches():
            if (
                match.get("date") == row.get("date")
                and match.get("home_team") == row.get("home_team")
                and match.get("away_team") == row.get("away_team")
            ):
                return match
        return None

    def _actual_score(self, match: dict[str, Any]) -> str | None:
        if match.get("home_score") is None or match.get("away_score") is None:
            return None
        return f"{match['home_score']}-{match['away_score']}"

    def _prediction_accuracy(self, predicted_score: str | None, actual_score: str | None) -> dict[str, Any] | None:
        if not predicted_score or not actual_score or "-" not in predicted_score or "-" not in actual_score:
            return None
        try:
            pred_home, pred_away = [int(part) for part in predicted_score.split("-", 1)]
            actual_home, actual_away = [int(part) for part in actual_score.split("-", 1)]
        except ValueError:
            return None
        pred_outcome = "home" if pred_home > pred_away else "away" if pred_home < pred_away else "draw"
        actual_outcome = "home" if actual_home > actual_away else "away" if actual_home < actual_away else "draw"
        return {
            "exact_score": pred_home == actual_home and pred_away == actual_away,
            "outcome_hit": pred_outcome == actual_outcome,
            "goal_diff_error": abs((pred_home - pred_away) - (actual_home - actual_away)),
            "total_goal_error": abs((pred_home + pred_away) - (actual_home + actual_away)),
        }

    def _players_with_known_clubs(self, team: str, players: list[dict[str, Any]]) -> list[dict[str, Any]]:
        squad_by_number: dict[int, dict[str, Any]] = {}
        try:
            squad = self.get_team_squad(team)
            for player in squad.get("players", []):
                if player.get("number") is not None:
                    squad_by_number[int(player["number"])] = player
        except KeyError:
            squad_by_number = {}
        enriched = []
        for player in players:
            item = dict(player)
            item["ability_estimated"] = False
            known = squad_by_number.get(int(player["shirt_number"])) if player.get("shirt_number") is not None else None
            if known:
                item["club"] = known.get("club")
                item["league"] = known.get("league")
                item["club_source"] = known.get("source") or known.get("source_name")
            else:
                item["club"] = None
                item["league"] = None
                item["club_source"] = None
            enriched.append(item)
        return self._estimate_missing_player_abilities(enriched)

    def _estimate_missing_player_abilities(self, players: list[dict[str, Any]]) -> list[dict[str, Any]]:
        known_values = [
            float(player["ability"])
            for player in players
            if player.get("ability") is not None
        ]
        team_average = sum(known_values) / len(known_values) if known_values else 6.5
        by_position: dict[str, list[float]] = {}
        for player in players:
            if player.get("ability") is None:
                continue
            position = str(player.get("position") or "unknown")
            by_position.setdefault(position, []).append(float(player["ability"]))
        position_average = {
            position: sum(values) / len(values)
            for position, values in by_position.items()
            if values
        }
        for player in players:
            if player.get("ability") is not None:
                continue
            position = str(player.get("position") or "unknown")
            player["ability"] = round(position_average.get(position, team_average), 1)
            player["ability_estimated"] = True
        return players

    def _serialize_score_matrix(self, matrix: dict[tuple[Any, Any], float]) -> list[dict[str, Any]]:
        return [
            {"home_goals": home, "away_goals": away, "probability": probability}
            for (home, away), probability in matrix.items()
        ]

    def _matrix_from_serialized(self, rows: list[dict[str, Any]]) -> dict[tuple[Any, Any], float]:
        return {
            (row["home_goals"], row["away_goals"]): float(row["probability"])
            for row in rows
        }

    def _report_for_fixture(
        self,
        fixture: dict[str, Any],
        probabilities: dict[str, float],
        matrix: dict[tuple[Any, Any], float],
    ) -> str:
        best = top_scorelines(matrix, limit=1)[0]
        home = display_team(fixture["home_team"])
        away = display_team(fixture["away_team"])
        return (
            f"{home['flag']}{home['zh']} vs {away['flag']}{away['zh']} 数据分析："
            f"主胜 {probabilities['home']:.1%}，平局 {probabilities['draw']:.1%}，"
            f"客胜 {probabilities['away']:.1%}。最可能比分为 {best['score']}，"
            f"概率 {best['probability']:.1%}。本结论用于参考和赛后复盘，不构成投注建议。"
        )

    def _report_for_top_scorelines(
        self,
        fixture: dict[str, Any],
        probabilities: dict[str, float],
        scorelines: list[dict[str, Any]],
        ensemble: dict[str, Any],
        value_analysis: dict[str, Any],
    ) -> str:
        best = scorelines[0] if scorelines else {"score": "--", "probability": 0.0}
        home = display_team(fixture["home_team"])
        away = display_team(fixture["away_team"])
        value_text = value_analysis.get("summary") or "盘口未配置，当前仅基于模型评估。"
        return (
            f"{home['flag']}{home['zh']} vs {away['flag']}{away['zh']} 数据分析："
            f"融合模型给出主胜 {probabilities['home']:.1%}，平局 {probabilities['draw']:.1%}，"
            f"客胜 {probabilities['away']:.1%}，信心 {ensemble.get('confidence', '低')}。"
            f"最可能比分为 {best['score']}，概率 {float(best['probability']):.1%}。"
            f"{value_text} 本结论用于参考和赛后复盘，不构成投注建议。"
        )
