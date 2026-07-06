from __future__ import annotations

import math
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from .data.providers import ProviderRegistry
from .data.fifa_official import FifaOfficialWorldCupCrawler, merge_field_sources
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
from .prediction.ensemble import EnsembleConfig, blend_probabilities, normalize_outcomes
from .prediction.handicap import handicap_probabilities, handicap_value_analysis, parse_handicap_line
from .prediction.learning import apply_learning_to_lambdas, rolling_worldcup_adjustment
from .prediction.market import analyze_value, kelly_fraction, market_bundle, market_from_decimal_odds, unavailable_market
from .prediction.metrics import actual_outcome, evaluate_result
from .prediction.monte_carlo import MonteCarloConfig, simulate_match
from .prediction.odds import devig
from .prediction.poisson_model import PoissonModelConfig, estimate_poisson_prediction, poisson_score_matrix
from .prediction.xgboost_model import (
    FEATURE_NAMES,
    build_features,
    estimate_xgboost_prediction,
    monte_carlo_feature_proxy,
    stage_bucket_for_fixture,
    train_xgboost_layer,
)
from .prediction.weight_calibration import calibrate_model_weights, default_weight_run
from .result_sync import (
    collect_world_cup_finished_matches,
    evaluate_round_of_32_regression,
    evaluate_world_cup_regression,
    fetch_latest_finished_matches,
    retrain_team_ratings_from_world_cup,
    sync_finished_matches_to_local_store,
    update_knockout_bracket_with_result,
    validate_bracket_after_result_sync,
    write_prediction_outputs,
)
from .roster_strength import USABLE_STATUSES, aggregate_team_strength, player_strength, position_bucket
from .team_metadata import display_team, enrich_fixture, enrich_profile


class WorldCupService:
    def __init__(self, db_path: str | Path = "data/worldcup.sqlite3"):
        self.db = Database(db_path)
        self.providers = ProviderRegistry()
        self.public_scraper = PublicWorldCupScraper()
        self.fifa_crawler = FifaOfficialWorldCupCrawler()
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

    def sync_footballdata_io(self, date: str | None = None) -> dict[str, Any]:
        provider = getattr(self.providers, "footballdata_io_provider", None)
        if provider is None:
            provider = next((item for item in self.providers.providers if getattr(item, "name", "") == "FootballData.io"), None)
        if provider is None or not provider.configured():
            return {
                "source": "FootballData.io",
                "configured": False,
                "fixtures": [],
                "warnings": ["FOOTBALLDATA_IO_API_KEY not set"],
            }
        target_date = date or datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        warnings: list[str] = []
        try:
            fixtures = provider.fetch_fixtures(target_date)
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            return {
                "source": "FootballData.io",
                "configured": True,
                "fixtures": [],
                "warnings": [str(exc)],
            }
        for fixture in fixtures:
            self.db.save_raw_provider_payload("FootballData.io", fixture, fixture_id=fixture.get("id"))
            self.db.upsert_fixture(fixture)
            for field in ("home_score", "away_score", "status", "footballdata_io_stats"):
                if fixture.get(field) is not None:
                    self.db.save_field_source(
                        entity_type="match",
                        entity_id=fixture["id"],
                        field_name=field,
                        value=fixture.get(field),
                        source="FootballData.io",
                        source_priority=2,
                        source_id=fixture.get("source_id"),
                    )
        warnings.extend(getattr(provider, "last_warnings", []))
        return {
            "source": "FootballData.io",
            "configured": True,
            "fixtures": fixtures,
            "fixture_count": len(fixtures),
            "meta": getattr(provider, "last_meta", {}),
            "warnings": warnings,
        }

    def sync_sportmonks(self, date: str | None = None) -> dict[str, Any]:
        provider = getattr(self.providers, "sportmonks_provider", None)
        if provider is None:
            provider = next((item for item in self.providers.providers if getattr(item, "name", "") == "SportMonks"), None)
        if provider is None or not provider.configured():
            return {
                "source": "sportmonks",
                "source_priority": 2,
                "configured": False,
                "fixtures": [],
                "warnings": ["SPORTMONKS_API_KEY not set"],
            }
        target_date = date or datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        try:
            fixtures = provider.fetch_fixtures(target_date)
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            return {
                "source": "sportmonks",
                "source_priority": 2,
                "configured": True,
                "fixtures": [],
                "warnings": [str(exc)],
            }
        supplemental: dict[str, Any] = {"teams": [], "players": [], "livescores": []}
        supplemental_warnings: list[str] = []
        for key, fetcher in (
            ("teams", getattr(provider, "fetch_teams", None)),
            ("players", getattr(provider, "fetch_players", None)),
            ("livescores", getattr(provider, "fetch_livescores", None)),
        ):
            if not fetcher:
                continue
            try:
                rows = fetcher()
                supplemental[key] = rows[:50] if isinstance(rows, list) else []
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                supplemental_warnings.append(f"{key}: {exc}")
        for fixture in fixtures:
            self.db.save_raw_provider_payload("sportmonks", fixture, fixture_id=fixture.get("id"))
            self.db.upsert_fixture(fixture)
            for field in (
                "home_score",
                "away_score",
                "status",
                "sportmonks_features",
                "sportmonks_lineups",
                "sportmonks_statistics",
                "sportmonks_sidelined",
                "sportmonks_standings",
            ):
                if fixture.get(field) is not None:
                    self.db.save_field_source(
                        entity_type="match",
                        entity_id=fixture["id"],
                        field_name=field,
                        value=fixture.get(field),
                        source="sportmonks",
                        source_priority=2,
                        source_id=fixture.get("source_id"),
                    )
        for key, rows in supplemental.items():
            for row in rows[:10]:
                source_id = str(row.get("id") or row.get("source_id") or "") if isinstance(row, dict) else None
                self.db.save_raw_provider_payload(f"sportmonks_{key}", row if isinstance(row, dict) else {"value": row}, fixture_id=source_id)
        return {
            "source": "sportmonks",
            "source_priority": 2,
            "configured": True,
            "fixtures": fixtures,
            "fixture_count": len(fixtures),
            "teams_count": len(supplemental["teams"]),
            "players_count": len(supplemental["players"]),
            "livescores_count": len(supplemental["livescores"]),
            "normalized_count": len(fixtures),
            "rate_limit": getattr(provider, "last_rate_limit", {}),
            "warnings": list(getattr(provider, "capability_warnings", [])) + supplemental_warnings,
        }

    def sync_fifa_official_data(self, date: str | None = None) -> dict[str, Any]:
        try:
            payload = self.fifa_crawler.fetch_world_cup_data()
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            return {
                "source": "FIFA",
                "matches": [],
                "power_rankings": [],
                "warnings": [str(exc)],
            }
        warnings = list(payload.get("warnings") or [])
        target_date = date
        matched_power = 0
        for match in payload.get("matches") or []:
            if target_date and match.get("date") and match.get("date") != target_date:
                continue
            self.db.save_raw_provider_payload("FIFA", match, fixture_id=match.get("match_id"))
            self.db.save_fifa_match_context(match)
            for field, source_payload in (merge_field_sources([match])["fields"]).items():
                self.db.save_field_source(
                    entity_type="match",
                    entity_id=match.get("match_id") or match.get("source_id") or "",
                    field_name=field,
                    value=source_payload["value"],
                    source=source_payload["source"],
                    source_priority=source_payload["source_priority"],
                    source_id=source_payload.get("source_id"),
                )
            if match.get("is_finished") and match.get("home_goals_90") is not None and match.get("away_goals_90") is not None:
                self.db.upsert_finished_match_result(match)
                self.db.mark_fixture_final_from_result(match)
        for ranking in payload.get("power_rankings") or []:
            self.db.save_player_power_ranking(ranking)
            if self.db.apply_player_power_ranking_to_squad(ranking):
                matched_power += 1
                try:
                    self._recompute_squad_strength(ranking["team"])
                except KeyError:
                    pass
        roster_count = 0
        for roster in payload.get("rosters") or []:
            team = roster.get("team")
            if not team:
                continue
            self.db.save_team_squad(str(team), roster)
            roster_count += 1
            try:
                self._recompute_squad_strength(str(team))
            except KeyError:
                pass
        return {
            "source": "FIFA",
            "source_priority": 1,
            "matches": payload.get("matches") or [],
            "match_count": len(payload.get("matches") or []),
            "power_rankings": payload.get("power_rankings") or [],
            "power_ranking_count": len(payload.get("power_rankings") or []),
            "power_ranking_matched_players": matched_power,
            "rosters": payload.get("rosters") or [],
            "roster_count": roster_count,
            "news": payload.get("news") or [],
            "warnings": warnings,
        }

    def backfill_historical_matches(self, start_date: str = "2026-06-23", end_date: str = "2026-06-28") -> dict[str, Any]:
        backfilled: list[dict[str, Any]] = []
        prediction_ids: list[str] = []
        for row in self.db.list_lyihub_matches():
            date = str(row.get("date") or "")
            if date < start_date or date > end_date or not self._is_final_row(row):
                continue
            match = self._lyihub_finished_match_payload(row)
            self.db.upsert_finished_match_result(match)
            fixture_id = str(row.get("id") or row.get("fixture_id") or match["match_id"])
            try:
                self.predict_fixture(fixture_id)
                prediction_ids.append(fixture_id)
            except (KeyError, ValueError, TypeError, RuntimeError):
                pass
            backfilled.append(match)
        return {
            "start_date": start_date,
            "end_date": end_date,
            "backfilled_match_count": len(backfilled),
            "prediction_count": len(prediction_ids),
            "matches": backfilled,
            "prediction_fixture_ids": prediction_ids,
        }

    def train_over25_parameters(self, start_date: str = "2026-06-23") -> dict[str, Any]:
        matches = [
            match
            for match in self.db.list_finished_matches()
            if str(match.get("date") or "") >= start_date
            and match.get("home_goals_90") is not None
            and match.get("away_goals_90") is not None
        ]
        team_rows: dict[str, list[dict[str, Any]]] = {}
        report_matches: list[dict[str, Any]] = []
        for match in matches:
            home_goals = int(match["home_goals_90"])
            away_goals = int(match["away_goals_90"])
            total = home_goals + away_goals
            over = total > 2.5
            item = {
                "match_id": match.get("match_id"),
                "date": match.get("date"),
                "home_team": match.get("home_team"),
                "away_team": match.get("away_team"),
                "total_goals_90": total,
                "over25": over,
            }
            report_matches.append(item)
            team_rows.setdefault(match["home_team"], []).append({"goals_for": home_goals, "goals_against": away_goals, "over": over})
            team_rows.setdefault(match["away_team"], []).append({"goals_for": away_goals, "goals_against": home_goals, "over": over})
        profiles = {profile["team"]: profile for profile in self.db.list_team_profiles()}
        for team, rows in team_rows.items():
            count = max(1, len(rows))
            over_rate = sum(1 for row in rows if row["over"]) / count
            goals_for = sum(row["goals_for"] for row in rows) / count
            goals_against = sum(row["goals_against"] for row in rows) / count
            attack_tendency = self._clamp((goals_for + over_rate) / 3.2, 0.05, 0.95)
            defense_tendency = self._clamp((goals_against + over_rate) / 3.2, 0.05, 0.95)
            match_tendency = self._clamp((attack_tendency + defense_tendency + over_rate) / 3, 0.05, 0.95)
            profile = dict(profiles.get(team, {"team": team}))
            profile.update(
                {
                    "team": team,
                    "over25_attack_tendency": round(attack_tendency, 6),
                    "over25_defense_tendency": round(defense_tendency, 6),
                    "over25_match_tendency": round(match_tendency, 6),
                    "over25_recent_rate": round(over_rate, 6),
                    "over25_adjusted_rating": round(self._clamp(0.55 * match_tendency + 0.45 * over_rate, 0.05, 0.95), 6),
                    "under25_stability": round(self._clamp(1 - over_rate, 0.05, 0.95), 6),
                    "over25_sample_count": count,
                }
            )
            self.db.save_team_profile(team, profile)
        return {
            "start_date": start_date,
            "sample_count": len(matches),
            "over25_count": sum(1 for item in report_matches if item["over25"]),
            "team_count": len(team_rows),
            "matches": report_matches,
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
        sync_fifa: bool = False,
        sync_fifa_rosters: bool = False,
        sync_footballdata_io: bool = False,
        sync_sporttery_odds: bool = False,
        sync_sporttery_history: bool = False,
        sync_sportmonks: bool = False,
        use_sportmonks: bool = False,
        backfill_historical_matches: bool = False,
        train_over25: bool = False,
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
        sporttery_sync = None
        if sync_sporttery_history:
            sporttery_sync = self.refresh_sporttery_odds(
                include_history=True,
                history_start="2026-06-23",
                history_end="2026-06-28",
            )
        elif sync_sporttery_odds:
            sporttery_sync = self.refresh_sporttery_odds()
        external_data = {
            "fifa": self.sync_fifa_official_data(date=target_date if not sync_fifa_rosters else None) if sync_fifa or sync_fifa_rosters else None,
            "sportmonks": self.sync_sportmonks(date=target_date) if sync_sportmonks or use_sportmonks else None,
            "footballdata_io": self.sync_footballdata_io(date=target_date) if sync_footballdata_io else None,
            "sporttery": sporttery_sync,
        }
        print("[INFO] Using result source: ESPN" if today_finished_matches else "[WARNING] No finished online matches returned.")
        sync_result = sync_finished_matches_to_local_store(today_finished_matches, self.db)
        historical_backfill = (
            self.backfill_historical_matches(start_date="2026-06-23", end_date="2026-06-28")
            if backfill_historical_matches
            else None
        )
        print("[INFO] Finished matches synced.")
        all_world_cup_matches = collect_world_cup_finished_matches(self.db)
        weight_scheme_comparison = self.compare_world_cup_weight_schemes(all_world_cup_matches)
        selected_world_cup_weight = (weight_scheme_comparison.get("selected_scheme") or {}).get("current_world_cup")
        print("[INFO] Retraining team ratings from all finished World Cup matches.")
        retraining = self._retrain_ratings_from_world_cup(all_world_cup_matches, world_cup_weight=selected_world_cup_weight)
        retraining["weight_scheme_comparison"] = weight_scheme_comparison
        over25_retraining = self.train_over25_parameters(start_date="2026-06-23") if train_over25 else None
        self._xgboost_model_cache.clear()
        print("[INFO] Updating knockout bracket from real winners.")
        bracket = self._bracket_from_local_matches()
        teams = {profile["team"]: profile for profile in self.db.list_team_profiles()}
        for match in all_world_cup_matches:
            update_knockout_bracket_with_result(bracket, match, teams)
        for team, profile in teams.items():
            self.db.save_team_profile(team, profile)
        squad_strength_update = self.recompute_all_squad_strengths(force=True)
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
        r32_regression = self.run_round_of_32_regression(output_dir=output_dir, before_matches=all_world_cup_matches)
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
            "historical_backfill": historical_backfill,
            "ratings": retraining,
            "weight_scheme_comparison": weight_scheme_comparison,
            "over25_retraining": over25_retraining,
            "squad_strength_update": squad_strength_update,
            "bracket_validation": bracket_validation,
            "xgboost": xgb_status,
            "external_data": external_data,
            "source_priority": {
                "FIFA": 1,
                "sportmonks": 2,
                "FootballData.io": 3,
                "ESPN": 4,
                "sporttery": 5,
                "other": 9,
            },
            "regression_evaluation": {
                key: value
                for key, value in regression_evaluation.items()
                if key != "per_match_errors"
            },
            "r32_regression": {
                key: value for key, value in r32_regression.items() if key != "per_match_errors"
            },
        }
        team_ratings_with_strength = []
        for profile in sorted(teams.values(), key=lambda item: item.get("elo", 0), reverse=True):
            enriched_profile = dict(profile)
            strength = self.db.get_squad_strength(str(profile.get("team") or ""))
            if strength:
                enriched_profile["squad_strength"] = strength
                for field in (
                    "attack_line_strength",
                    "midfield_line_strength",
                    "defense_line_strength",
                    "goalkeeper_strength",
                    "squad_overall_strength",
                    "starting_xi_strength",
                    "bench_strength",
                    "strength_baseline",
                    "paper_strength_source",
                    "line_strength_source",
                    "model_version",
                ):
                    if field in strength:
                        enriched_profile[field] = strength[field]
            team_ratings_with_strength.append(enriched_profile)
        outputs = write_prediction_outputs(
            output_dir=Path(output_dir),
            predictions=predictions,
            bracket=bracket,
            team_ratings=team_ratings_with_strength,
            result_sync_log=result_sync_log,
            regression_evaluation=regression_evaluation,
            model_retraining_report=retraining,
        )
        outputs["r32_result_90_error_analysis_json"] = str(Path(output_dir) / "r32_result_90_error_analysis.json")
        outputs["r32_regression_metrics_before_after_json"] = str(Path(output_dir) / "r32_regression_metrics_before_after.json")
        scheme_path = Path(output_dir) / "world_cup_weight_scheme_comparison.json"
        scheme_path.write_text(json.dumps(weight_scheme_comparison, ensure_ascii=False, indent=2), encoding="utf-8")
        outputs["world_cup_weight_scheme_comparison_json"] = str(scheme_path)
        extra_outputs = self._write_external_source_outputs(Path(output_dir), external_data, result_sync_log)
        outputs.update(extra_outputs)
        outputs.update(self._write_squad_strength_outputs(Path(output_dir)))
        print("[INFO] Saved updated predictions.")
        print("[INFO] Validation completed.")
        return {
            "finished_matches": all_world_cup_matches,
            "today_finished_matches": today_finished_matches,
            "advanced_teams": advanced_teams,
            "eliminated_teams": eliminated_teams,
            "world_cup_finished_match_count": len(all_world_cup_matches),
            "sync": sync_result,
            "historical_backfill": historical_backfill,
            "ratings": retraining,
            "retraining": retraining,
            "weight_scheme_comparison": weight_scheme_comparison,
            "over25_retraining": over25_retraining,
            "squad_strength_update": squad_strength_update,
            "regression_evaluation": regression_evaluation,
            "r32_regression": r32_regression,
            "bracket": bracket,
            "bracket_validation": bracket_validation,
            "xgboost": xgb_status,
            "external_data": external_data,
            "prediction_count": len(predictions),
            "outputs": outputs,
        }

    def _write_external_source_outputs(
        self,
        output_dir: Path,
        external_data: dict[str, Any],
        result_sync_log: dict[str, Any],
    ) -> dict[str, str]:
        output_dir.mkdir(parents=True, exist_ok=True)
        sync_log = output_dir / "external_data_sync_log.json"
        conflict_report = output_dir / "source_conflict_report.json"
        sportmonks_log = output_dir / "sportmonks_sync_log.json"
        warnings = []
        for source_name, payload in (external_data or {}).items():
            if not payload:
                continue
            for warning in payload.get("warnings") or []:
                warnings.append({"source": source_name, "warning": warning})
            if payload.get("last_error"):
                warnings.append({"source": source_name, "warning": payload["last_error"]})
        sync_log.write_text(json.dumps(external_data, ensure_ascii=False, indent=2), encoding="utf-8")
        if (external_data or {}).get("sportmonks"):
            sportmonks_log.write_text(json.dumps(external_data["sportmonks"], ensure_ascii=False, indent=2), encoding="utf-8")
        conflict_report.write_text(
            json.dumps(
                {
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "source_priority": result_sync_log.get("source_priority"),
                    "warnings": warnings,
                    "note": "FIFA fields take precedence when present; conflicts are recorded here instead of silently overwriting.",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        outputs = {
            "external_data_sync_log_json": str(sync_log),
            "source_conflict_report_json": str(conflict_report),
        }
        if sportmonks_log.exists():
            outputs["sportmonks_sync_log_json"] = str(sportmonks_log)
        return outputs

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
            and not self._is_untrusted_lyihub_placeholder_result(match)
        ]
        for match in normalized:
            if self._is_untrusted_lyihub_placeholder_result(match):
                continue
            if match.get("date") == target_date or self.db.get_lyihub_match_by_fixture(match["id"]):
                self.db.upsert_lyihub_match(match)
                self.db.upsert_web_fixture(match, source_name="lyihub_worldcup_static_json")
        return [self._lyihub_finished_match_payload(match) for match in finished]

    def _is_untrusted_lyihub_placeholder_result(self, match: dict[str, Any]) -> bool:
        if not self._is_knockout_stage(match.get("stage") or match.get("group")):
            return False
        home_score = match.get("home_score")
        away_score = match.get("away_score")
        if home_score != 0 or away_score != 0:
            return False
        full_score = self._lyihub_full_score(match)
        if full_score.get("home") not in (None, 0) or full_score.get("away") not in (None, 0):
            return False
        return True

    def _lyihub_finished_match_payload(self, match: dict[str, Any]) -> dict[str, Any]:
        home_score = match.get("home_score")
        away_score = match.get("away_score")
        full_score = self._lyihub_full_score(match)
        home_penalties = away_penalties = None
        decided_by_penalties = False
        winner = loser = None
        if (
            home_score is not None
            and away_score is not None
            and home_score == away_score
            and full_score["home"] is not None
            and full_score["away"] is not None
            and full_score["home"] != full_score["away"]
            and max(int(full_score["home"]), int(full_score["away"])) >= 3
        ):
            decided_by_penalties = True
            home_penalties = full_score["home"]
            away_penalties = full_score["away"]
            winner = match["home_team"] if home_penalties > away_penalties else match["away_team"]
            loser = match["away_team"] if winner == match["home_team"] else match["home_team"]
        elif home_score is not None and away_score is not None and home_score != away_score:
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
            "home_penalties": home_penalties,
            "away_penalties": away_penalties,
            "winner": winner,
            "loser": loser,
            "is_finished": True,
            "decided_by_extra_time": False,
            "decided_by_penalties": decided_by_penalties,
            "source": "lyihub_worldcup_static_json",
            "source_url": match.get("source_url"),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

    def _lyihub_full_score(self, match: dict[str, Any]) -> dict[str, int | None]:
        payload = match.get("payload") if isinstance(match.get("payload"), dict) else {}
        detail_match = (match.get("detail") or {}).get("match", {}) if isinstance(match.get("detail"), dict) else {}
        for candidate in (payload, detail_match):
            for key in ("score_full", "score"):
                score = candidate.get(key)
                if isinstance(score, dict) and score.get("team_a") is not None and score.get("team_b") is not None:
                    return {"home": score.get("team_a"), "away": score.get("team_b")}
        return {"home": match.get("home_score"), "away": match.get("away_score")}

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
        r32_regression = self.run_round_of_32_regression(before_matches=finished_matches)
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
            "r32_regression": r32_regression,
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
            "over25_summary": prediction.get("over25_summary"),
            "over25_prob": prediction.get("final_over25_prob"),
            "under25_prob": prediction.get("under25_prob"),
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

    def _retrain_ratings_from_world_cup(self, finished_matches: list[dict[str, Any]], world_cup_weight: float | None = None) -> dict[str, Any]:
        teams = {profile["team"]: profile for profile in self.db.list_team_profiles()}
        result = retrain_team_ratings_from_world_cup(finished_matches, teams, world_cup_weight_override=world_cup_weight)
        for team, profile in result["teams"].items():
            self.db.save_team_profile(team, profile)
        report = dict(result)
        report["team_count"] = len(result["teams"])
        report["teams"] = sorted(result["teams"].values(), key=lambda item: item.get("elo", 0), reverse=True)
        return report

    def compare_world_cup_weight_schemes(self, finished_matches: list[dict[str, Any]]) -> dict[str, Any]:
        schemes = {
            "scheme_1": {"current_world_cup": 0.70, "historical": 0.20, "market": 0.10},
            "scheme_2": {"current_world_cup": 0.75, "historical": 0.15, "market": 0.10},
            "scheme_3": {"current_world_cup": 0.80, "historical": 0.10, "market": 0.10},
            "scheme_4": {"current_world_cup": 0.85, "historical": 0.10, "market": 0.05},
        }
        prior_profiles = {profile["team"]: profile for profile in self.db.list_team_profiles()}
        evaluated: dict[str, Any] = {}
        for name, scheme in schemes.items():
            retrained = retrain_team_ratings_from_world_cup(
                finished_matches,
                prior_profiles,
                world_cup_weight_override=scheme["current_world_cup"],
            )
            predictions = {
                str(match.get("match_id") or ""): self._fallback_regression_prediction_with_profiles(
                    match,
                    retrained["teams"],
                    apply_stage_adjustment=True,
                )
                for match in finished_matches
                if match.get("match_id")
            }
            metrics = evaluate_world_cup_regression(finished_matches, predictions)
            evaluated[name] = {
                **scheme,
                "metrics": {key: value for key, value in metrics.items() if key != "per_match_errors"},
                "selection_score": self._weight_scheme_score(metrics),
            }
        has_knockout = any(self._is_knockout_stage(match.get("stage") or match.get("group")) for match in finished_matches)
        target_weight = 0.80 if has_knockout else 0.75
        selected_name = (
            min(
                evaluated,
                key=lambda key: (
                    round(float(evaluated[key]["selection_score"]), 5),
                    abs(float(evaluated[key]["current_world_cup"]) - target_weight),
                ),
            )
            if evaluated
            else None
        )
        selected = {"name": selected_name, **evaluated[selected_name]} if selected_name else {}
        return {
            "schemes": evaluated,
            "selected_scheme": selected,
            "selection_policy": "minimize log_loss + brier + calibration_error + goal error, with small rewards for accuracy and over2.5 accuracy",
            "tie_break_target_world_cup_weight": target_weight,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    def _weight_scheme_score(self, metrics: dict[str, Any]) -> float:
        accuracy = float(metrics.get("accuracy_90") or 0.0)
        over25 = float(metrics.get("over25_accuracy") or 0.0)
        return round(
            float(metrics.get("log_loss") or 0.0)
            + float(metrics.get("brier_score") or 0.0)
            + float(metrics.get("calibration_error") or 0.0)
            + 0.15 * float(metrics.get("goals_mae") or 0.0)
            + 0.08 * float(metrics.get("goals_rmse") or 0.0)
            - 0.35 * accuracy
            - 0.15 * over25,
            6,
        )

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

    def run_round_of_32_regression(
        self,
        *,
        output_dir: str | Path = "outputs",
        before_matches: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        matches = before_matches or collect_world_cup_finished_matches(self.db)
        legacy_matches = self._legacy_full_score_r32_matches(matches)
        before_predictions = self._stage_regression_predictions(legacy_matches, apply_stage_adjustment=False)
        before_metrics = evaluate_round_of_32_regression(legacy_matches, before_predictions)
        self._xgboost_model_cache.clear()
        after_predictions = self._stage_regression_predictions(matches, apply_stage_adjustment=True)
        after_metrics = evaluate_round_of_32_regression(matches, after_predictions)
        payload = {
            "stage": "round_of_32",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "blend_weights": {
                **self._stage_blend_weights({"group": "1/16决赛"}, {}),
                "draw_adjustment": 0.05,
            },
            "before": {key: value for key, value in before_metrics.items() if key != "per_match_errors"},
            "after": {key: value for key, value in after_metrics.items() if key != "per_match_errors"},
            "per_match_errors": after_metrics.get("per_match_errors", []),
            "notes": [
                "before 使用旧版 full-score/row-score 口径，用于暴露点球或全场比分污染；after 使用修正后的90分钟比分口径。",
                "90分钟胜平负只使用 home_goals_90 / away_goals_90；加时和点球只用于晋级字段。",
                "draw_recall 仅统计真实90分钟平局样本；点球胜者不计为90分钟胜者。",
            ],
        }
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        (output_path / "r32_result_90_error_analysis.json").write_text(
            json.dumps(
                {
                    "generated_at": payload["generated_at"],
                    "stage": payload["stage"],
                    "match_count": after_metrics.get("match_count", 0),
                    "per_match_errors": after_metrics.get("per_match_errors", []),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        (output_path / "r32_regression_metrics_before_after.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return payload

    def _legacy_full_score_r32_matches(self, matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
        lyihub_by_id = {str(row.get("match_id") or row.get("id")): row for row in self.db.list_lyihub_matches()}
        legacy_matches: list[dict[str, Any]] = []
        for match in matches:
            legacy = dict(match)
            if stage_bucket_for_fixture({"group": match.get("stage") or match.get("group")}) != "round_of_32":
                legacy_matches.append(legacy)
                continue
            row = lyihub_by_id.get(str(match.get("match_id") or "").replace("lyihub-", ""))
            if not row:
                legacy_matches.append(legacy)
                continue
            full_score = self._lyihub_full_score(row)
            if full_score["home"] is None or full_score["away"] is None:
                legacy_matches.append(legacy)
                continue
            if full_score["home"] == match.get("home_goals_90") and full_score["away"] == match.get("away_goals_90"):
                legacy_matches.append(legacy)
                continue
            legacy["home_goals_90"] = full_score["home"]
            legacy["away_goals_90"] = full_score["away"]
            legacy["decided_by_penalties"] = False
            if full_score["home"] != full_score["away"]:
                legacy["winner"] = legacy["home_team"] if full_score["home"] > full_score["away"] else legacy["away_team"]
                legacy["loser"] = legacy["away_team"] if legacy["winner"] == legacy["home_team"] else legacy["home_team"]
            legacy["score_source"] = "legacy_full_score"
            legacy_matches.append(legacy)
        return legacy_matches

    def _stage_regression_predictions(self, matches: list[dict[str, Any]], *, apply_stage_adjustment: bool) -> dict[str, dict[str, Any]]:
        predictions: dict[str, dict[str, Any]] = {}
        for match in matches:
            match_id = str(match.get("match_id") or "")
            if not match_id:
                continue
            predictions[match_id] = self._prediction_for_finished_match(match, apply_stage_adjustment=apply_stage_adjustment)
        return predictions

    def _prediction_for_finished_match(self, match: dict[str, Any], *, apply_stage_adjustment: bool = True) -> dict[str, Any] | None:
        return self._fallback_regression_prediction(match, apply_stage_adjustment=apply_stage_adjustment)

    def _fallback_regression_prediction(self, match: dict[str, Any], *, apply_stage_adjustment: bool = True) -> dict[str, Any]:
        profiles = {profile["team"]: profile for profile in self.db.list_team_profiles()}
        return self._fallback_regression_prediction_with_profiles(
            match,
            profiles,
            apply_stage_adjustment=apply_stage_adjustment,
        )

    def _fallback_regression_prediction_with_profiles(
        self,
        match: dict[str, Any],
        profiles: dict[str, dict[str, Any]],
        *,
        apply_stage_adjustment: bool = True,
    ) -> dict[str, Any]:
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
        probabilities = {key: value / total for key, value in exps.items()}
        draw_adjustment = {"applied": False, "weight": 0.0, "probabilities": probabilities}
        if apply_stage_adjustment:
            draw_adjustment = self._stage_draw_adjustment(
                fixture={
                    "group": match.get("stage") or match.get("group"),
                    "home_team": match.get("home_team"),
                    "away_team": match.get("away_team"),
                },
                probabilities=probabilities,
                home_profile=home,
                away_profile=away,
                market_payload={"available": False},
            )
        if apply_stage_adjustment and draw_adjustment.get("applied"):
            probabilities = draw_adjustment["probabilities"]
        return {
            "fixture": {
                "home_team": match.get("home_team"),
                "away_team": match.get("away_team"),
            },
            "probabilities": probabilities,
            "expected_goals": {"home": home_xg, "away": away_xg},
            "stage_calibration": {
                "stage": stage_bucket_for_fixture({"group": match.get("stage") or match.get("group")}),
                "draw_adjustment": draw_adjustment,
            },
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
            "feature_names": list(FEATURE_NAMES),
            "min_weight": round(min(weights), 6),
            "max_weight": round(max(weights), 6),
            "mean_weight": round(sum(weights) / len(weights), 6),
            "world_cup_weight_policy": "wc2026_r32=4.0 wc2026_other=3.0 historical_knockout=1.2 historical_other=0.5",
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
        value = prediction.get("value_analysis") or {}
        handicap = prediction.get("handicap_analysis") or {}
        lottery = prediction.get("lottery_market") or {}
        odds_markets = prediction.get("odds_markets") or {}
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
            "odds_markets": odds_markets,
            "lottery_market": lottery,
            "market_implied_probability": (prediction.get("market") or {}).get("market_probability_no_vig"),
            "value_analysis": value,
            "edge": value.get("items"),
            "kelly": value.get("recommended_options"),
            "handicap_analysis": handicap,
            "handicap_1x2_probabilities": handicap.get("probabilities"),
            "handicap_market_implied_probability": handicap.get("market_probabilities"),
            "handicap_value_analysis": handicap.get("value_analysis"),
            "betting_recommendations": prediction.get("betting_recommendations"),
            "analysis_disclaimer": "仅做数据分析，不构成投注建议。",
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

    def refresh_sporttery_odds(
        self,
        *,
        include_history: bool = False,
        history_start: str = "2026-06-23",
        history_end: str = "2026-06-28",
    ) -> dict[str, Any]:
        cleared = self.db.clear_non_sporttery_market_fields()
        self.ensure_sporttery_lottery_snapshot()
        fetched_at = datetime.now(timezone.utc).isoformat()
        history = self._refresh_sporttery_history(history_start, history_end) if include_history else None
        try:
            events = self.providers.sporttery_odds_provider.fetch_odds()
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            return {
                "source": "https://m.sporttery.cn/mjc/jsq/zqspf/",
                "mode": "snapshot_fallback",
                "updated": len(SPORTTERY_LOTTERY_SNAPSHOT),
                "cleared_non_sporttery_markets": cleared,
                "match_numbers": [market["match_no"] for market in SPORTTERY_LOTTERY_SNAPSHOT],
                "last_error": str(exc),
                "last_updated": fetched_at,
                "grouped_by_date": {},
                "unmatched_matches": [],
                "history": history,
            }
        grouped: dict[str, list[dict[str, Any]]] = {}
        unmatched: list[dict[str, Any]] = []
        for event in events:
            fixture = self._fixture_for_sporttery_event(event)
            self.db.save_sporttery_odds_snapshot(event, matched_fixture_id=fixture.get("id") if fixture else None)
            if not fixture:
                unmatched.append(event)
            grouped.setdefault(str(event.get("date") or "unknown"), []).append(event)
        upsert_summary = self._upsert_sporttery_events(events)
        updated = upsert_summary["updated"]
        mode = "live" if events else "empty_live"
        print(
            "[INFO] Sporttery odds refresh: "
            f"fetched={len(events)} matched={updated} "
            f"unmatched={len(unmatched)} filtered={len(upsert_summary['filtered_matches'])}"
        )
        return {
            "source": "https://m.sporttery.cn/mjc/jsq/zqspf/",
            "mode": mode,
            "updated": updated,
            "cleared_non_sporttery_markets": cleared,
            "sample_count": len(events),
            "last_updated": fetched_at,
            "grouped_by_date": grouped,
            "unmatched_matches": unmatched,
            "matched_count": updated,
            "failed_count": 0,
            "filtered_matches": upsert_summary["filtered_matches"],
            "match_results": upsert_summary["match_results"],
            "empty_reason": "sporttery current page returned no matches" if not events else None,
            "history": history,
        }

    def _refresh_sporttery_history(self, start_date: str, end_date: str) -> dict[str, Any]:
        provider = self.providers.sporttery_odds_provider
        fetcher = getattr(provider, "fetch_historical_odds", None)
        if fetcher is None:
            return {
                "history_available": False,
                "updated": 0,
                "covered_dates": [],
                "reason": "sporttery historical odds endpoint is not available; current selling window only.",
            }
        try:
            events = fetcher(start_date, end_date)
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            return {
                "history_available": False,
                "updated": 0,
                "covered_dates": [],
                "reason": str(exc),
            }
        upsert_summary = self._upsert_sporttery_events(events)
        updated = upsert_summary["updated"]
        for event in events:
            event["is_historical"] = True
            fixture = self._fixture_for_sporttery_event(event)
            self.db.save_sporttery_odds_snapshot(event, matched_fixture_id=fixture.get("id") if fixture else None)
        return {
            "history_available": bool(events),
            "updated": updated,
            "filtered_matches": upsert_summary["filtered_matches"],
            "match_results": upsert_summary["match_results"],
            "covered_dates": sorted({str(event.get("date")) for event in events if event.get("date")}),
            "reason": None if events else "No sporttery historical odds were returned; keeping historical matches without odds.",
        }

    def _upsert_sporttery_events(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        updated = 0
        filtered: list[dict[str, Any]] = []
        match_results: list[dict[str, Any]] = []
        for event in events:
            home_team = canonical_team(str(event.get("home_team") or ""))
            away_team = canonical_team(str(event.get("away_team") or ""))
            fixture = self._find_lyihub_fixture_for_market(home_team, away_team, str(event.get("date") or ""))
            if not fixture:
                match_results.append(
                    {
                        "match_num": event.get("match_num"),
                        "home_team": event.get("home_team"),
                        "away_team": event.get("away_team"),
                        "status": "unmatched",
                        "reason": "no_local_fixture_match",
                    }
                )
                continue
            h2h = event.get("h2h") or {}
            handicap = event.get("handicap") or {}
            totals = event.get("totals") or {}
            has_h2h = {"home", "draw", "away"} <= set(h2h)
            has_handicap = {"home", "draw", "away"} <= set(handicap)
            has_totals = {"over", "under"} <= set(totals)
            if not (has_h2h or has_handicap or has_totals):
                filtered.append({**event, "reason": "no_supported_market"})
                match_results.append(
                    {
                        "match_num": event.get("match_num"),
                        "fixture_id": fixture.get("id"),
                        "home_team": event.get("home_team"),
                        "away_team": event.get("away_team"),
                        "status": "filtered",
                        "reason": "no_supported_market",
                    }
                )
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
                    "market_home": h2h.get("home") if has_h2h else None,
                    "market_draw": h2h.get("draw") if has_h2h else None,
                    "market_away": h2h.get("away") if has_h2h else None,
                    "market_over_2_5": totals.get("over"),
                    "market_under_2_5": totals.get("under"),
                    "market_source": f"China Sporttery live {event.get('match_num') or ''}".strip(),
                    "market_handicap": handicap if has_handicap else None,
                    "market_handicap_line": event.get("handicap_line"),
                }
            )
            updated += 1
            reason = "full_market" if has_h2h else "handicap_only" if has_handicap else "totals_only"
            match_results.append(
                {
                    "match_num": event.get("match_num"),
                    "fixture_id": fixture.get("id"),
                    "home_team": event.get("home_team"),
                    "away_team": event.get("away_team"),
                    "status": "updated",
                    "reason": reason,
                    "has_h2h": has_h2h,
                    "has_handicap": has_handicap,
                    "has_totals": has_totals,
                }
            )
        return {"updated": updated, "filtered_matches": filtered, "match_results": match_results}

    def _find_lyihub_fixture_for_market(self, home_team: str, away_team: str, date: str) -> dict[str, Any] | None:
        for row in self.db.list_lyihub_matches():
            if date and row.get("date") != date:
                continue
            row_home_candidates = {str(row.get("home_team") or ""), str(row.get("home_team_zh") or "")}
            row_away_candidates = {str(row.get("away_team") or ""), str(row.get("away_team_zh") or "")}
            if home_team in row_home_candidates and away_team in row_away_candidates:
                return row
        return None

    def _fixture_for_sporttery_event(self, event: dict[str, Any]) -> dict[str, Any] | None:
        home_team = canonical_team(str(event.get("home_team") or ""))
        away_team = canonical_team(str(event.get("away_team") or ""))
        return self._find_lyihub_fixture_for_market(home_team, away_team, str(event.get("date") or ""))


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
        if isinstance(fixture.get("sportmonks_features"), str):
            try:
                fixture["sportmonks_features"] = json.loads(fixture["sportmonks_features"])
            except (TypeError, ValueError):
                fixture["sportmonks_features"] = {}

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
        stage_blend_weights = self._stage_blend_weights(fixture, model_weight_run["weights"])
        model_weight_run["stage"] = stage_bucket_for_fixture(fixture)
        model_weight_run["stage_blend_weights"] = stage_blend_weights
        ensemble = blend_probabilities(
            elo={key: elo[key] for key in ("home", "draw", "away")},
            poisson=poisson,
            monte_carlo=monte_carlo,
            market=market_payload,
            xgboost=xgboost,
            config=EnsembleConfig(weights=stage_blend_weights),
        )
        draw_adjustment = self._stage_draw_adjustment(
            fixture=fixture,
            probabilities={key: ensemble[key] for key in ("home", "draw", "away")},
            home_profile=home_profile,
            away_profile=away_profile,
            market_payload=market_payload,
        )
        probabilities = {
            "home": ensemble["home"],
            "draw": ensemble["draw"],
            "away": ensemble["away"],
        }
        if draw_adjustment["applied"]:
            probabilities = draw_adjustment["probabilities"]
            ensemble.update(probabilities)
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
        over25_summary = self._over25_summary(
            poisson=poisson,
            monte_carlo=monte_carlo,
            xgboost=xgboost,
            score_matrix=final_score_matrix,
            odds_markets=odds_markets,
        )
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
            "over25_prob": over25_summary["final_over25_prob"],
            "under25_prob": over25_summary["under25_prob"],
            "xgboost_over25_prob": over25_summary["xgboost_over25_prob"],
            "monte_carlo_over25_prob": over25_summary["monte_carlo_over25_prob"],
            "score_heatmap_over25_prob": over25_summary["score_heatmap_over25_prob"],
            "final_over25_prob": over25_summary["final_over25_prob"],
            "over25_market_prob": over25_summary["over25_market_prob"],
            "over25_edge": over25_summary["over25_edge"],
            "over25_kelly": over25_summary["over25_kelly"],
            "over25_value": over25_summary["value"],
            "over25_summary": over25_summary,
            "model_blend_weights": {
                "dixon_coles_elo": ensemble["weights"].get("elo", 0.0),
                "poisson": ensemble["weights"].get("poisson", 0.0),
                "monte_carlo": ensemble["weights"].get("monte_carlo", 0.0),
                "market": ensemble["weights"].get("market", 0.0),
                "xgboost": ensemble["weights"].get("xgboost", 0.0),
                "draw_adjustment": draw_adjustment.get("weight", 0.0),
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
            "stage_calibration": {
                "stage": model_weight_run["stage"],
                "stage_blend_weights": stage_blend_weights,
                "draw_adjustment": draw_adjustment,
            },
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
        if strength and self._squad_strength_cache_current(strength):
            return strength
        if allow_empty:
            self.recompute_all_squad_strengths(force=True)
            refreshed = self.db.get_squad_strength(team)
            if refreshed and self._squad_strength_cache_current(refreshed):
                return refreshed
            raw = self._raw_squad_strength_for_team(team)
            normalized = self._standardize_squad_strengths({team: raw}, pool_note="single-team fallback").get(team, raw)
            self.db.save_squad_strength(team, normalized)
            return normalized
        squad = self.db.get_team_squad(team)
        if squad or strength or self.db.list_player_power_rankings(team):
            self.recompute_all_squad_strengths(force=True)
            refreshed = self.db.get_squad_strength(team)
            if refreshed:
                return refreshed
        raise KeyError(f"Unknown strength: {team}")

    def _squad_strength_cache_current(self, strength: dict[str, Any]) -> bool:
        required = {
            "attack_line_strength",
            "midfield_line_strength",
            "defense_line_strength",
            "goalkeeper_strength",
            "paper_strength_source",
            "fallback_fields",
        }
        if not required <= set(strength):
            return False
        return str(strength.get("model_version") or "") == "world-cup-line-strength-v1"

    def _upgrade_legacy_strength(self, team: str, strength: dict[str, Any]) -> dict[str, Any] | None:
        if not {"attack_strength", "midfield_control_strength", "defense_gk_strength"} <= set(strength):
            return None
        attack = float(strength.get("attack_strength") or 70)
        midfield = float(strength.get("midfield_control_strength") or 70)
        defense_gk = float(strength.get("defense_gk_strength") or 70)
        goalkeeper = float(strength.get("goalkeeper_strength") or defense_gk)
        defense = float(strength.get("defense_line_strength") or round((defense_gk * 2) - goalkeeper, 2))
        starting = float(strength.get("starting_xi_strength") or round((attack + midfield + defense + goalkeeper) / 4, 2))
        bench = float(strength.get("bench_strength") or round(max(52, starting - 7), 2))
        upgraded = dict(strength)
        upgraded.update(
            {
                "team": team,
                "attack_line_strength": attack,
                "midfield_line_strength": midfield,
                "defense_line_strength": defense,
                "goalkeeper_strength": goalkeeper,
                "squad_depth": float(strength.get("squad_depth") or round(0.65 * starting + 0.35 * bench, 2)),
                "starting_xi_strength": starting,
                "bench_strength": bench,
                "paper_strength_source": strength.get("paper_strength_source") or "legacy cached squad strength",
                "line_strength_source": strength.get("line_strength_source") or "legacy cached squad strength",
                "fallback_fields": strength.get("fallback_fields") or [],
                "fifa_power_coverage": float(strength.get("fifa_power_coverage") or 0.0),
                "model_version": "roster-strength-v2",
            }
        )
        return upgraded

    def _team_power_rankings_strength(self, team: str) -> dict[str, Any] | None:
        rankings = self.db.list_player_power_rankings(team)
        if not rankings:
            return None
        players = [
            {
                "name": row["player_name"],
                "position": row.get("position") or "Midfielder",
                "fifa_power_rating": row.get("rating"),
                "power_ranking_source": row.get("source") or "FIFA power rankings",
                "stats_status": "complete",
            }
            for row in rankings
            if row.get("rating") is not None
        ]
        if not players:
            return None
        strength = aggregate_team_strength(team, players)
        strength["model_version"] = "team-power-rankings-v1"
        strength["paper_strength_source"] = "FIFA power rankings"
        strength["line_strength_source"] = "FIFA power rankings team aggregate"
        strength["missing_player_stats"] = 0
        return strength

    def _team_performance_strength_fallback(self, team: str) -> dict[str, Any]:
        profile = next((item for item in self.db.list_team_profiles() if item.get("team") == team), {})
        attack = self._clamp(58 + 12 * float(profile.get("attack_rating") or profile.get("xg_for") or 1.0), 52, 92)
        midfield = self._clamp(float(profile.get("midfield_rating") or 64 + 18 * float(profile.get("form_rating") or 0.0)), 52, 90)
        defense_base = profile.get("defensive_stability")
        if defense_base is not None:
            defense = self._clamp(58 + 12 * float(defense_base), 52, 90)
        else:
            defense = self._clamp(84 - 10 * float(profile.get("defense_rating") or profile.get("xg_against") or 1.4), 52, 90)
        goalkeeper = self._clamp(defense + 1.5, 52, 90)
        starting = round((attack + midfield + defense + goalkeeper) / 4, 2)
        bench = round(max(52, starting - 7), 2)
        return {
            "team": team,
            "attack_strength": round(attack, 2),
            "midfield_control_strength": round(midfield, 2),
            "defense_gk_strength": round((defense + goalkeeper) / 2, 2),
            "attack_line_strength": round(attack, 2),
            "midfield_line_strength": round(midfield, 2),
            "defense_line_strength": round(defense, 2),
            "goalkeeper_strength": round(goalkeeper, 2),
            "squad_overall_strength": starting,
            "squad_depth": round(0.65 * starting + 0.35 * bench, 2),
            "starting_xi_strength": starting,
            "bench_strength": bench,
            "coverage": 0.0,
            "fifa_power_coverage": 0.0,
            "missing_player_stats": 0,
            "paper_strength_source": "本届世界杯表现 fallback",
            "line_strength_source": "本届世界杯表现 fallback",
            "fallback_fields": [
                "attack_line_strength",
                "midfield_line_strength",
                "defense_line_strength",
                "goalkeeper_strength",
            ],
            "model_version": "team-performance-fallback-v1",
        }

    def recompute_all_squad_strengths(self, force: bool = False) -> dict[str, Any]:
        teams = self._current_world_cup_teams()
        if not teams:
            teams = {profile["team"] for profile in self.db.list_team_profiles() if profile.get("team")}
            teams.update({ranking["team"] for ranking in self.db.list_player_power_rankings() if ranking.get("team")})
        raw_by_team: dict[str, dict[str, Any]] = {}
        for team in sorted(teams):
            raw_by_team[team] = self._raw_squad_strength_for_team(team)
        normalized_by_team = self._standardize_squad_strengths(raw_by_team)
        updated: list[str] = []
        fallback: list[str] = []
        for team, strength in normalized_by_team.items():
            self.db.save_squad_strength(team, strength)
            updated.append(team)
            if "fallback" in str(strength.get("paper_strength_source") or "").lower():
                fallback.append(team)
        summary = self._squad_strength_standardization_summary(normalized_by_team)
        return {
            "updated": len(updated),
            "fallback_count": len(fallback),
            "fallback_teams": fallback,
            "standardization": summary,
            "baseline_note": "50 = 本届世界杯48队平均水平",
            "model_versions": [
                "world-cup-line-strength-v1",
            ],
        }

    def _write_squad_strength_outputs(self, output_dir: Path) -> dict[str, str]:
        output_dir.mkdir(parents=True, exist_ok=True)
        teams = sorted(self._current_world_cup_teams())
        if not teams:
            teams = sorted({profile["team"] for profile in self.db.list_team_profiles() if profile.get("team")})
            teams.extend(
                sorted(
                    {
                        ranking["team"]
                        for ranking in self.db.list_player_power_rankings()
                        if ranking.get("team") and ranking.get("team") not in set(teams)
                    }
                )
            )
        strengths = {
            team: self.db.get_squad_strength(team)
            for team in teams
            if self.db.get_squad_strength(team)
        }
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "baseline_note": "50 = 本届世界杯48队平均水平",
            "source_priority": ["FIFA", "SportMonks", "FootballData.io", "ESPN", "historical/fallback"],
            "standardization": self._squad_strength_standardization_summary(strengths),
            "teams": [
                {
                    "team": team,
                    **strengths[team],
                }
                for team in sorted(strengths)
            ],
        }
        path = output_dir / "squad_strengths_updated.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"squad_strengths_updated_json": str(path)}

    def _current_world_cup_teams(self) -> set[str]:
        group_stage_teams: set[str] = set()
        teams: set[str] = set()
        for row in self.db.list_lyihub_matches():
            group = str(row.get("group") or row.get("stage") or "")
            for side in ("home_team", "away_team"):
                team = str(row.get(side) or "")
                if not self._is_concrete_team_name(team):
                    continue
                if "小组赛" in group or "Group" in group:
                    group_stage_teams.add(team)
                    teams.add(team)
        if group_stage_teams:
            return group_stage_teams
        if not teams:
            for date in self.available_dates():
                for row in self.db.list_web_fixtures(date):
                    group = str(row.get("group") or row.get("stage") or "")
                    for side in ("home_team", "away_team"):
                        team = str(row.get(side) or "")
                        if not self._is_concrete_team_name(team):
                            continue
                        if "小组赛" in group or "Group" in group:
                            group_stage_teams.add(team)
                            teams.add(team)
            if group_stage_teams:
                return group_stage_teams
        return teams

    def _is_concrete_team_name(self, team: str) -> bool:
        if not team:
            return False
        lowered = team.lower()
        if lowered.startswith(("winner ", "loser ", "tbd")):
            return False
        placeholder_tokens = ("胜者", "败者", "第", "/")
        return not any(token in team for token in placeholder_tokens)

    def _raw_squad_strength_for_team(self, team: str) -> dict[str, Any]:
        squad = self.db.get_team_squad(team)
        if squad:
            strength = aggregate_team_strength(team, squad.get("players", []))
        else:
            strength = self._team_power_rankings_strength(team) or self._team_performance_strength_fallback(team)
        return self._ensure_squad_overall(team, strength)

    def _ensure_squad_overall(self, team: str, strength: dict[str, Any]) -> dict[str, Any]:
        payload = dict(strength)
        attack = float(payload.get("attack_line_strength", payload.get("attack_strength", 50)) or 50)
        midfield = float(payload.get("midfield_line_strength", payload.get("midfield_control_strength", 50)) or 50)
        defense = float(payload.get("defense_line_strength", payload.get("defense_gk_strength", 50)) or 50)
        goalkeeper = float(payload.get("goalkeeper_strength", payload.get("defense_gk_strength", defense)) or defense)
        starting = float(payload.get("starting_xi_strength") or round((attack + midfield + defense + goalkeeper) / 4, 2))
        bench = float(payload.get("bench_strength") or round(max(35.0, starting - 8), 2))
        payload.update(
            {
                "team": team,
                "attack_strength": attack,
                "midfield_control_strength": midfield,
                "defense_gk_strength": round((defense + goalkeeper) / 2, 2),
                "attack_line_strength": attack,
                "midfield_line_strength": midfield,
                "defense_line_strength": defense,
                "goalkeeper_strength": goalkeeper,
                "starting_xi_strength": starting,
                "bench_strength": bench,
                "squad_overall_strength": float(payload.get("squad_overall_strength") or round((attack + midfield + defense + goalkeeper + starting + bench) / 6, 2)),
            }
        )
        return payload

    def _standardize_squad_strengths(
        self,
        raw_by_team: dict[str, dict[str, Any]],
        *,
        pool_note: str = "current World Cup teams",
    ) -> dict[str, dict[str, Any]]:
        fields = [
            "attack_line_strength",
            "midfield_line_strength",
            "defense_line_strength",
            "goalkeeper_strength",
            "squad_overall_strength",
            "starting_xi_strength",
            "bench_strength",
        ]
        if not raw_by_team:
            return {}
        scale = 10.0
        stats: dict[str, dict[str, float]] = {}
        for field in fields:
            values = [float(payload.get(field) or 50.0) for payload in raw_by_team.values()]
            mean_value = sum(values) / len(values)
            variance = sum((value - mean_value) ** 2 for value in values) / len(values)
            std_value = math.sqrt(variance)
            stats[field] = {"mean": mean_value, "std": std_value}
        normalized: dict[str, dict[str, Any]] = {}
        for team, raw in raw_by_team.items():
            payload = dict(raw)
            for field in fields:
                raw_value = float(raw.get(field) or 50.0)
                stat = stats[field]
                if stat["std"] < 1e-6:
                    normalized_value = 50.0
                else:
                    normalized_value = 50 + ((raw_value - stat["mean"]) / stat["std"]) * scale
                payload[f"{field}_raw"] = round(raw_value, 4)
                payload[field] = round(self._clamp(normalized_value, 30, 85), 2)
            payload["attack_strength"] = payload["attack_line_strength"]
            payload["midfield_control_strength"] = payload["midfield_line_strength"]
            payload["defense_gk_strength"] = round((payload["defense_line_strength"] + payload["goalkeeper_strength"]) / 2, 2)
            payload["squad_depth"] = round(0.65 * payload["starting_xi_strength"] + 0.35 * payload["bench_strength"], 2)
            payload["strength_baseline"] = "50 = 本届世界杯48队平均水平"
            payload["strength_baseline_value"] = 50
            payload["standardization_method"] = "z-score: 50 + (raw - mean) / std * 10, clamped to 30-85"
            payload["standardization_pool"] = pool_note
            payload["standardization_pool_size"] = len(raw_by_team)
            payload["standardization_scale"] = scale
            payload["standardization_bounds"] = {"min": 30, "max": 85}
            payload["data_source_priority"] = ["FIFA", "SportMonks", "FootballData.io", "ESPN", "historical/fallback"]
            payload["model_confidence"] = round(
                max(
                    float(payload.get("coverage", 0.0) or 0.0),
                    0.75 if float(payload.get("fifa_power_coverage", 0.0) or 0.0) > 0 else 0.45 if "fallback" in str(payload.get("paper_strength_source") or "").lower() else 0.6,
                ),
                4,
            )
            payload["model_version"] = "world-cup-line-strength-v1"
            normalized[team] = payload
        return normalized

    def _squad_strength_standardization_summary(self, strengths: dict[str, dict[str, Any]]) -> dict[str, Any]:
        fields = [
            "attack_line_strength",
            "midfield_line_strength",
            "defense_line_strength",
            "goalkeeper_strength",
            "squad_overall_strength",
            "starting_xi_strength",
            "bench_strength",
        ]
        summary: dict[str, Any] = {
            "baseline": "50 = 本届世界杯48队平均水平",
            "team_count": len(strengths),
            "method": "z-score",
            "scale": 10,
            "bounds": [30, 85],
        }
        for field in fields:
            values = [float(payload.get(field) or 0) for payload in strengths.values()]
            if values:
                summary[field] = {
                    "mean": round(sum(values) / len(values), 4),
                    "min": round(min(values), 4),
                    "max": round(max(values), 4),
                }
        return summary

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

    def _stage_blend_weights(self, fixture: dict[str, Any], calibrated_weights: dict[str, float] | None = None) -> dict[str, float]:
        stage = stage_bucket_for_fixture(fixture)
        if stage == "round_of_32":
            return {
                "elo": 0.0,
                "poisson": 0.25,
                "monte_carlo": 0.25,
                "xgboost": 0.30,
                "market": 0.15,
            }
        if stage == "later_knockout":
            return {
                "elo": 0.05,
                "poisson": 0.25,
                "monte_carlo": 0.25,
                "xgboost": 0.30,
                "market": 0.15,
            }
        if stage == "group":
            return calibrated_weights or {
                "elo": 0.15,
                "poisson": 0.25,
                "monte_carlo": 0.25,
                "xgboost": 0.25,
                "market": 0.10,
            }
        return calibrated_weights or {
            "elo": 0.15,
            "poisson": 0.25,
            "monte_carlo": 0.25,
            "xgboost": 0.25,
            "market": 0.10,
        }

    def _stage_draw_adjustment(
        self,
        *,
        fixture: dict[str, Any],
        probabilities: dict[str, float],
        home_profile: dict[str, Any],
        away_profile: dict[str, Any],
        market_payload: dict[str, Any],
    ) -> dict[str, Any]:
        stage = stage_bucket_for_fixture(fixture)
        if stage not in {"round_of_32", "later_knockout"}:
            return {"applied": False, "weight": 0.0, "probabilities": probabilities}

        normalized = normalize_outcomes(probabilities)
        home_under = float(home_profile.get("under25_stability", 0.5))
        away_under = float(away_profile.get("under25_stability", 0.5))
        under25_stability = self._clamp((home_under + away_under) / 2, 0.0, 1.0)
        defensive_matchup = self._clamp(
            (
                float(home_profile.get("defensive_stability", 0.5))
                + float(away_profile.get("defensive_stability", 0.5))
            )
            / 2,
            0.0,
            1.5,
        )
        strength_gap = abs(
            float(home_profile.get("strength_rating") or home_profile.get("elo") or 1700)
            - float(away_profile.get("strength_rating") or away_profile.get("elo") or 1700)
        )
        close_strength_factor = self._clamp(1.0 - strength_gap / 180.0, 0.0, 1.0)
        low_score_knockout_factor = self._clamp(
            0.45 * under25_stability + 0.35 * defensive_matchup + 0.20 * close_strength_factor,
            0.0,
            1.0,
        )
        market_probs = (
            market_payload.get("market_probability_no_vig")
            or market_payload.get("implied_probability_no_vig")
            or {}
            if market_payload.get("available")
            else {}
        )
        market_draw = float(market_probs.get("draw", normalized["draw"])) if market_probs else normalized["draw"]
        market_draw_calibration = self._clamp(market_draw - normalized["draw"], -0.04, 0.04)
        r32_prior = 0.012 if stage == "round_of_32" else 0.008
        draw_boost = self._clamp(
            r32_prior + 0.022 * low_score_knockout_factor + market_draw_calibration,
            0.0,
            0.04 if stage == "round_of_32" else 0.03,
        )
        original_leader = max(normalized.items(), key=lambda item: item[1])[0]
        adjusted = dict(normalized)
        non_draw_total = adjusted["home"] + adjusted["away"]
        if non_draw_total > 0:
            adjusted["home"] -= draw_boost * (adjusted["home"] / non_draw_total)
            adjusted["away"] -= draw_boost * (adjusted["away"] / non_draw_total)
            adjusted["draw"] += draw_boost
        adjusted = normalize_outcomes(adjusted)
        adjusted_leader = max(adjusted.items(), key=lambda item: item[1])[0]
        if adjusted_leader == "draw" and original_leader != "draw" and normalized[original_leader] - normalized["draw"] > 0.035:
            excess = adjusted["draw"] - adjusted[original_leader] + 0.001
            if excess > 0:
                adjusted["draw"] -= excess
                adjusted[original_leader] += excess
                adjusted = normalize_outcomes(adjusted)
        return {
            "applied": draw_boost > 0,
            "weight": 0.05 if stage == "round_of_32" else 0.03,
            "probabilities": adjusted,
            "draw_tendency": round(low_score_knockout_factor, 6),
            "under25_stability": round(under25_stability, 6),
            "low_score_knockout_factor": round(low_score_knockout_factor, 6),
            "defensive_matchup_factor": round(defensive_matchup, 6),
            "market_draw_calibration": round(market_draw_calibration, 6),
            "draw_boost": round(draw_boost, 6),
        }

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
                    "home": self.get_team_strength(match["home_team"], allow_empty=True),
                    "away": self.get_team_strength(match["away_team"], allow_empty=True),
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
        date = str(match.get("date") or "")
        is_wc2026 = date.startswith("2026-") and source != "historical_matches"
        if is_wc2026 and ("1/16" in stage or "round of 32" in stage.lower()):
            return 4.0
        if is_wc2026:
            return 3.0
        if source == "historical_matches":
            return 1.2 if self._is_knockout_stage(stage) else 0.5
        if self._is_knockout_stage(stage):
            return 1.2
        return 0.5

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

    def simulation_rankings(self) -> dict[str, Any]:
        alive = self._current_bracket_alive_teams()
        warnings: list[str] = []
        profiles = self.team_rankings()
        if alive:
            filtered = []
            for profile in profiles:
                team = str(profile.get("team") or "")
                if profile.get("eliminated") or team not in alive:
                    if profile.get("eliminated"):
                        warnings.append(f"{team} filtered because eliminated=true")
                    elif team:
                        warnings.append(f"{team} filtered because not in current bracket path")
                    continue
                filtered.append(profile)
        else:
            filtered = [profile for profile in profiles if not profile.get("eliminated")]
            warnings.append("No current bracket path teams were found; filtered only by eliminated flag.")
        return {
            "teams": filtered,
            "alive_teams": sorted(alive),
            "warnings": warnings[:50],
            "filtered_count": max(0, len(profiles) - len(filtered)),
        }

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
        power_rankings = self.db.list_player_power_rankings(canonical)
        strength = self.get_team_strength(canonical, allow_empty=True)
        return {
            "team": canonical,
            "display": display_team(canonical),
            "matches": matches,
            "players": players,
            "power_rankings": power_rankings,
            "squad_strength": strength,
            "coverage": {
                "matches": len(matches),
                "players": len(players),
                "players_with_ability": len([player for player in players if player.get("ability") is not None]),
                "players_with_fifa_power": len(power_rankings),
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
        ranking_payload = self.simulation_rankings()
        teams = ranking_payload["teams"][:8]
        if not teams:
            return {"champion_favorite": None, "nodes": [], "alive_teams": ranking_payload["alive_teams"], "warnings": ranking_payload["warnings"]}
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
            "alive_teams": ranking_payload["alive_teams"],
            "warnings": ranking_payload["warnings"],
            "filtered_count": ranking_payload["filtered_count"],
        }

    def _current_bracket_alive_teams(self) -> set[str]:
        bracket_teams: set[str] = set()
        eliminated: set[str] = set()
        for row in self.db.list_lyihub_matches():
            stage = row.get("stage") or row.get("group")
            if not self._is_knockout_stage(stage):
                continue
            for side in ("home_team", "away_team"):
                team = str(row.get(side) or "")
                if team and not team.lower().startswith(("winner ", "loser ", "tbd")):
                    bracket_teams.add(team)
            if self._is_final_row(row):
                result = self._lyihub_finished_match_payload(row)
                if result.get("loser"):
                    eliminated.add(str(result["loser"]))
                if result.get("winner"):
                    bracket_teams.add(str(result["winner"]))
        for profile in self.db.list_team_profiles():
            if profile.get("eliminated") and profile.get("team"):
                eliminated.add(str(profile["team"]))
        return bracket_teams - eliminated

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
            return unavailable_market("中国体育彩票当前没有该比赛胜平负赔率，或网页抓取失败。")
        market_source = str(fixture.get("market_source") or "")
        if not self._is_sporttery_market_source(market_source):
            return unavailable_market("当前赔率不是中国体育彩票来源，已忽略。")
        return market_from_decimal_odds(
            {
                "home": fixture["market_home"],
                "draw": fixture["market_draw"],
                "away": fixture["market_away"],
            },
            provider=market_source,
            market_key="h2h",
        )

    def _is_sporttery_market_source(self, source: str) -> bool:
        value = str(source or "").lower()
        return "sporttery" in value or "中国体育彩票" in value or "竞彩" in value

    def _odds_markets(self, fixture: dict[str, Any], h2h_market: dict[str, Any]) -> dict[str, Any]:
        market_source = str(fixture.get("market_source") or "China Sporttery")
        if not self._is_sporttery_market_source(market_source):
            return market_bundle(
                h2h=h2h_market,
                handicap=unavailable_market("让球胜平负盘口不可用：当前赔率不是中国体育彩票来源。"),
                totals=unavailable_market("大小球盘口不可用：当前赔率不是中国体育彩票来源。"),
            )
        totals = None
        if fixture.get("market_over_2_5") and fixture.get("market_under_2_5"):
            totals = market_from_decimal_odds(
                {
                    "over": fixture["market_over_2_5"],
                    "under": fixture["market_under_2_5"],
                },
                provider=market_source,
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
                    provider=market_source,
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

    def _over25_summary(
        self,
        *,
        poisson: dict[str, Any],
        monte_carlo: dict[str, Any],
        xgboost: dict[str, Any],
        score_matrix: list[dict[str, Any]],
        odds_markets: dict[str, Any],
    ) -> dict[str, Any]:
        poisson_over = float(poisson.get("over_2_5") or 0.0)
        monte_carlo_over = float(monte_carlo.get("over_2_5", poisson_over))
        heatmap_over = float(totals_probability(self._matrix_from_serialized(score_matrix), 2.5)["over"])
        xgboost_over = float(xgboost.get("over25_prob", poisson_over))
        final_over = self._clamp(
            0.34 * poisson_over + 0.26 * monte_carlo_over + 0.22 * heatmap_over + 0.18 * xgboost_over,
            0.01,
            0.99,
        )
        totals_market = odds_markets.get("totals") or {}
        if not totals_market.get("available"):
            return {
                "poisson_over25_prob": poisson_over,
                "under25_prob": 1 - final_over,
                "xgboost_over25_prob": xgboost_over,
                "monte_carlo_over25_prob": monte_carlo_over,
                "score_heatmap_over25_prob": heatmap_over,
                "final_over25_prob": final_over,
                "over25_market_prob": None,
                "over25_edge": None,
                "over25_kelly": None,
                "value": {
                    "available": False,
                    "reason": "无赔率，仅模型概率。",
                },
            }
        market_probs = totals_market.get("market_probability_no_vig") or totals_market.get("implied_probability_no_vig") or {}
        odds = totals_market.get("odds") or {}
        market_over = float(market_probs.get("over", 0.0))
        edge = final_over - market_over
        kelly = kelly_fraction(final_over, float(odds.get("over", 0.0)))
        return {
            "poisson_over25_prob": poisson_over,
            "under25_prob": 1 - final_over,
            "xgboost_over25_prob": xgboost_over,
            "monte_carlo_over25_prob": monte_carlo_over,
            "score_heatmap_over25_prob": heatmap_over,
            "final_over25_prob": final_over,
            "over25_market_prob": market_over,
            "over25_edge": edge,
            "over25_kelly": {
                "full": kelly,
                "half": kelly * 0.5,
                "quarter": kelly * 0.25,
            },
            "value": {
                "available": True,
                "market_probability": market_over,
                "edge": edge,
                "kelly": kelly,
                "note": "仅数据分析，不构成投注建议。",
            },
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
            if provider.get("role") == "official_cn_odds_public_web_fallback"
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
            "sporttery_priority_note": "赔率仅使用中国体育彩票网页数据；其他历史赔率源不会参与今日比赛或可投注比赛计算。",
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
        coverage = min(
            max(home_coverage, float(home.get("model_confidence", 0.0) or 0.0)),
            max(away_coverage, float(away.get("model_confidence", 0.0) or 0.0)),
        )
        if coverage <= 0:
            return 1.0, 1.0, {
                "available": False,
                "home_xg_multiplier": 1.0,
                "away_xg_multiplier": 1.0,
                "coverage": 0.0,
            }
        home_delta = (
            (float(home.get("attack_line_strength", home.get("attack_strength", 50))) - float(away.get("defense_line_strength", away.get("defense_gk_strength", 50)))) * 0.012
            + (float(home.get("midfield_line_strength", home.get("midfield_control_strength", 50))) - float(away.get("midfield_line_strength", away.get("midfield_control_strength", 50))))
            * 0.006
        )
        away_delta = (
            (float(away.get("attack_line_strength", away.get("attack_strength", 50))) - float(home.get("defense_line_strength", home.get("defense_gk_strength", 50)))) * 0.012
            + (float(away.get("midfield_line_strength", away.get("midfield_control_strength", 50))) - float(home.get("midfield_line_strength", home.get("midfield_control_strength", 50))))
            * 0.006
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
        payload = enrich_fixture({
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
            "sportmonks_features": json.loads(row.get("sportmonks_features") or "{}")
            if isinstance(row.get("sportmonks_features"), str)
            else row.get("sportmonks_features"),
        })
        payload["odds_available"] = bool(
            payload.get("market_source")
            and (
                payload.get("market_home")
                or payload.get("market_handicap")
                or payload.get("market_over_2_5")
            )
        )
        payload["odds_source"] = payload.get("market_source")
        payload["historical_without_odds"] = payload.get("status") == "final" and not payload["odds_available"]
        payload["has_prediction_record"] = self.db.get_prediction(str(payload["id"])) is not None
        return payload

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
        response["odds_available"] = False
        response["odds_source"] = None
        response["historical_without_odds"] = response.get("status") == "final"
        response["has_prediction_record"] = self.db.get_prediction(str(response["id"])) is not None
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
            enriched["over25_summary"] = prediction.get("over25_summary")
            enriched["has_prediction_record"] = True
        except (KeyError, ValueError, TypeError, RuntimeError):
            predicted_score = None
            enriched["has_prediction_record"] = bool(enriched.get("has_prediction_record"))
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
