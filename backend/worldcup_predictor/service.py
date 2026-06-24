from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from .data.providers import ProviderRegistry
from .data.public_sources import HISTORICAL_RESULTS_URL, WIKIPEDIA_PARSE_URL, PublicWorldCupScraper
from .data.rosters import ApiFootballRosterProvider
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
from .prediction.metrics import evaluate_result
from .prediction.odds import devig
from .roster_strength import aggregate_team_strength, player_strength
from .team_metadata import display_team, enrich_fixture, enrich_profile


class WorldCupService:
    def __init__(self, db_path: str | Path = "data/worldcup.sqlite3"):
        self.db = Database(db_path)
        self.providers = ProviderRegistry()
        self.public_scraper = PublicWorldCupScraper()
        self.roster_provider = ApiFootballRosterProvider()

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
        if self.db.count_fixtures(date) == 0:
            self.sync_date(date)
        effective_date = date
        if self.db.count_fixtures(effective_date) == 0:
            effective_date = self.default_match_date(date)
        rows = self.db.list_fixtures(effective_date) + self.db.list_web_fixtures(effective_date)
        return [self._fixture_response(row) for row in self._dedupe_match_rows(rows)]

    def available_dates(self) -> list[str]:
        return self.db.available_dates()

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
        dates = self.available_dates()
        if not dates or requested_date in dates:
            return requested_date
        past_or_today = [date for date in dates if date <= requested_date]
        return past_or_today[-1] if past_or_today else dates[0]

    def predict_fixture(self, fixture_id: str, roster_weight: float = 0.25) -> dict[str, Any]:
        fixture = self.db.get_fixture(fixture_id)
        if not fixture:
            fixture = self.db.get_web_fixture(fixture_id)
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
        probabilities = {
            "home": calibrated_outcomes.home,
            "draw": calibrated_outcomes.draw,
            "away": calibrated_outcomes.away,
        }
        evaluation = None
        if fixture["status"] == "final" and fixture["home_score"] is not None:
            evaluation = evaluate_result(probabilities, fixture["home_score"], fixture["away_score"])

        payload = {
            "fixture": self._fixture_response(fixture),
            "source_status": self.data_source_health(),
            "probabilities": probabilities,
            "expected_goals": expected_goals(calibrated_matrix),
            "score_matrix": self._serialize_score_matrix(calibrated_matrix),
            "top_scorelines": top_scorelines(calibrated_matrix, limit=6),
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
            "btts": btts_probability(calibrated_matrix),
            "totals": {
                "1.5": totals_probability(calibrated_matrix, 1.5),
                "2.5": totals_probability(calibrated_matrix, 2.5),
                "3.5": totals_probability(calibrated_matrix, 3.5),
            },
            "model_blend_weights": {
                "dixon_coles_elo": 0.65 if market else 1.0,
                "market_calibration": 0.35 if market else 0.0,
                "llm_vote": 0.0,
                "llm_vote_cap": 0.15,
            },
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
            "llm_vote_audit": {
                "enabled": False,
                "weight_cap": 0.15,
                "votes": [],
                "note": "多模型投票接口已预留；未配置模型 API 时不影响数值预测。",
            },
            "chinese_report": self._report_for_fixture(fixture, probabilities, calibrated_matrix),
            "post_match_evaluation": evaluation,
        }
        self.db.save_prediction(fixture_id, payload)
        return payload

    def get_prediction(self, fixture_id: str) -> dict[str, Any]:
        prediction = self.db.get_prediction(fixture_id)
        return prediction or self.predict_fixture(fixture_id)

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

    def get_team_squad(self, team: str) -> dict[str, Any]:
        squad = self.db.get_team_squad(team)
        if not squad:
            raise KeyError(f"Unknown squad: {team}")
        players = squad.get("players", [])
        completed = len([player for player in players if player.get("stats_status") == "complete"])
        squad["coverage"] = round(completed / len(players), 4) if players else 0.0
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
        return health

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
            + [self.roster_provider.validate()]
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

    def save_web_fixtures(self, fixtures: list[dict[str, Any]], source_name: str) -> None:
        for fixture in fixtures:
            self.db.upsert_web_fixture(fixture, source_name=source_name)

    def save_historical_matches(self, matches: list[HistoricalMatch]) -> None:
        for match in matches:
            self.db.save_historical_match(match)
        profiles = build_team_profiles(matches, as_of="2026-06-16", half_life_years=5.0)
        for team, profile in profiles.items():
            self.db.save_team_profile(team, profile)

    def team_rankings(self) -> list[dict[str, Any]]:
        return self._team_profiles(compact=True)

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
                "used_for_data": False,
                "note": "参考站仅用于展示结构参考，未作为比赛数据源。",
            },
            "web_sources": {
                "fixture_count": self.db.web_fixture_count(),
                "historical_match_count": self.db.historical_match_count(),
                "available_dates": self.available_dates(),
            },
        }

    def _market_probabilities(self, fixture: dict[str, Any]) -> dict[str, float] | None:
        if not fixture.get("market_home"):
            return None
        return devig(
            {
                "home": fixture["market_home"],
                "draw": fixture["market_draw"],
                "away": fixture["market_away"],
            }
        )

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

    def _score_matrix_from_inputs(
        self,
        home_xg: float,
        away_xg: float,
        model_inputs: dict[str, Any],
    ) -> dict[tuple[Any, Any], float]:
        return score_matrix(home_xg=home_xg, away_xg=away_xg, rho=model_inputs.get("rho", -0.025), max_goals=7)

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
        })

    def _serialize_score_matrix(self, matrix: dict[tuple[Any, Any], float]) -> list[dict[str, Any]]:
        return [
            {"home_goals": home, "away_goals": away, "probability": probability}
            for (home, away), probability in matrix.items()
        ]

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
