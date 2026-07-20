from __future__ import annotations

import csv
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx


ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard"
ESPN_SUMMARY_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/summary"
DATA_SOURCES = ["espn"]


def fetch_latest_finished_matches(
    days_back: int = 30,
    client: httpx.Client | None = None,
    *,
    target_date: str | None = None,
    timezone: str = "Asia/Shanghai",
    finished_only: bool = True,
) -> list[dict[str, Any]]:
    """
    Fetch latest finished World Cup matches from public authoritative sports data.

    ESPN is used as the online source because it exposes a stable public scoreboard JSON
    with completed status and winner flags. If a feed does not split 90-minute and
    extra-time goals, the parser keeps the raw score and logs that limitation in the
    returned match warning fields instead of guessing.
    """
    client = client or httpx.Client(timeout=30, follow_redirects=True)
    local_tz = ZoneInfo(timezone)
    today = datetime.now(local_tz).date()
    if target_date:
        target_day = datetime.fromisoformat(target_date).date()
        # ESPN's ``dates`` parameter is keyed to the event's US/UTC calendar day,
        # while this workflow filters by Beijing date. Evening matches in North
        # America can therefore belong to the previous ESPN date.
        query_params = [
            {"dates": day.strftime("%Y%m%d")}
            for day in (target_day - timedelta(days=1), target_day)
        ]
    else:
        start_day = today - timedelta(days=max(1, int(days_back)) - 1)
        # ESPN accepts a date range and returns the whole tournament in one
        # bounded request.  This avoids rate limiting from issuing one request
        # per calendar day during a full historical refresh.
        query_params = [{"dates": f"{start_day:%Y%m%d}-{today:%Y%m%d}"}]
    matches: dict[str, dict[str, Any]] = {}
    errors = []
    for params in query_params:
        try:
            response = client.get(ESPN_SCOREBOARD_URL, params=params)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            errors.append({"source": "espn", "params": params, "error": str(exc)})
            continue
        for event in payload.get("events") or []:
            parsed = _parse_espn_event(event, timezone_name=timezone)
            if parsed and (parsed.get("decided_by_extra_time") or parsed.get("decided_by_penalties")):
                summary = _fetch_espn_summary(client, str(event.get("id") or ""))
                if summary:
                    _apply_score_breakdown(parsed, summary)
            if parsed and (not finished_only or parsed.get("is_finished")):
                if target_date and parsed.get("date") != target_date:
                    continue
                matches[str(parsed["match_id"])] = parsed
    return sorted(matches.values(), key=lambda item: (item["date"], item["match_id"]))


def _fetch_espn_summary(client: httpx.Client, event_id: str) -> dict[str, Any] | None:
    if not event_id:
        return None
    try:
        response = client.get(ESPN_SUMMARY_URL, params={"event": event_id})
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _apply_score_breakdown(match: dict[str, Any], summary: dict[str, Any]) -> None:
    competitors = ((summary.get("header") or {}).get("competitions") or [{}])[0].get("competitors") or []
    names = [str((item.get("team") or {}).get("displayName") or "") for item in competitors]
    if len(names) != 2:
        return
    regulation = {name: 0 for name in names}
    extra_time = {name: 0 for name in names}
    scoring_events = 0
    for event in summary.get("keyEvents") or summary.get("plays") or []:
        if not event.get("scoringPlay") or event.get("shootout"):
            continue
        team = str((event.get("team") or {}).get("displayName") or "")
        if team not in regulation:
            continue
        period = int((event.get("period") or {}).get("number") or 1)
        target = regulation if period <= 2 else extra_time
        target[team] += 1
        scoring_events += 1
    if not scoring_events:
        return
    match["home_goals_90"] = regulation[names[0]]
    match["away_goals_90"] = regulation[names[1]]
    match["home_goals_extra_time"] = extra_time[names[0]]
    match["away_goals_extra_time"] = extra_time[names[1]]
    match["score_breakdown_source"] = "ESPN match summary scoring events"


def _parse_espn_event(event: dict[str, Any], timezone_name: str = "Asia/Shanghai") -> dict[str, Any] | None:
    competitions = event.get("competitions") or []
    if not competitions:
        return None
    competition = competitions[0]
    status = (competition.get("status") or event.get("status") or {}).get("type") or {}
    if not status.get("completed"):
        return None
    competitors = competition.get("competitors") or []
    home = next((item for item in competitors if item.get("homeAway") == "home"), None)
    away = next((item for item in competitors if item.get("homeAway") == "away"), None)
    if not home or not away:
        return None
    home_team = _espn_team_name(home)
    away_team = _espn_team_name(away)
    home_score = _safe_int(home.get("score"))
    away_score = _safe_int(away.get("score"))
    home_penalties = _safe_int(home.get("shootoutScore") or home.get("penaltyScore"))
    away_penalties = _safe_int(away.get("shootoutScore") or away.get("penaltyScore"))
    decided_by_penalties = home_penalties is not None and away_penalties is not None
    winner = _winner_from_competitors(home, away, home_team, away_team, home_score, away_score, home_penalties, away_penalties)
    loser = away_team if winner == home_team else home_team if winner == away_team else None
    detail = str(status.get("detail") or status.get("description") or "")
    fetched_at = datetime.now(timezone.utc).isoformat()
    warnings = []
    if "pen" in detail.lower() and not decided_by_penalties:
        warnings.append("ESPN event mentions penalties but did not expose shootoutScore fields.")
    return {
        "match_id": f"espn-{event.get('id') or competition.get('id')}",
        "date": _event_local_date(str(event.get("date") or ""), timezone_name),
        "stage": (event.get("season") or {}).get("slug") or (competition.get("notes") or [{}])[0].get("headline") or "World Cup",
        "home_team": home_team,
        "away_team": away_team,
        "home_goals_90": home_score,
        "away_goals_90": away_score,
        "home_goals_extra_time": None,
        "away_goals_extra_time": None,
        "home_penalties": home_penalties,
        "away_penalties": away_penalties,
        "winner": winner,
        "loser": loser,
        "is_finished": True,
        "decided_by_extra_time": "aet" in detail.lower() or "extra time" in detail.lower(),
        "decided_by_penalties": decided_by_penalties,
        "source": "ESPN",
        "source_url": event.get("links", [{}])[0].get("href") if event.get("links") else ESPN_SCOREBOARD_URL,
        "fetched_at": fetched_at,
        "raw_score": {"home": home.get("score"), "away": away.get("score"), "status_detail": detail},
        "warnings": warnings,
    }


def _event_local_date(value: str, timezone_name: str) -> str:
    if not value:
        return ""
    try:
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).astimezone(ZoneInfo(timezone_name)).date().isoformat()
    except ValueError:
        return value[:10]


def _espn_team_name(competitor: dict[str, Any]) -> str:
    team = competitor.get("team") or {}
    return team.get("displayName") or team.get("shortDisplayName") or team.get("name") or "Unknown"


def _winner_from_competitors(
    home: dict[str, Any],
    away: dict[str, Any],
    home_team: str,
    away_team: str,
    home_score: int | None,
    away_score: int | None,
    home_penalties: int | None,
    away_penalties: int | None,
) -> str | None:
    if home.get("winner") is True:
        return home_team
    if away.get("winner") is True:
        return away_team
    if home_penalties is not None and away_penalties is not None and home_penalties != away_penalties:
        return home_team if home_penalties > away_penalties else away_team
    if home_score is not None and away_score is not None and home_score != away_score:
        return home_team if home_score > away_score else away_team
    return None


def _safe_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def sync_finished_matches_to_local_store(finished_matches: list[dict[str, Any]], local_store: Any) -> dict[str, Any]:
    synced = []
    warnings = []
    for match in finished_matches:
        previous = local_store.upsert_finished_match_result(match)["previous"]
        if previous and _result_signature(previous) != _result_signature(match):
            warnings.append(
                {
                    "match_id": match["match_id"],
                    "message": "Local result differs from online source. Online result will be used.",
                    "previous": _result_signature(previous),
                    "online": _result_signature(match),
                }
            )
        fixture_id = local_store.mark_fixture_final_from_result(match)
        synced.append({"match_id": match["match_id"], "fixture_id": fixture_id})
    invalidated = local_store.clear_predictions() if synced else 0
    return {
        "synced_count": len(synced),
        "synced": synced,
        "warnings": warnings,
        "invalidated_predictions": invalidated,
    }


def collect_world_cup_finished_matches(local_store: Any) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for match in local_store.list_finished_matches():
        if _has_90_score(match):
            by_key[(match["date"], match["home_team"], match["away_team"])] = _finished_match_payload(match)

    if hasattr(local_store, "list_lyihub_matches"):
        for row in local_store.list_lyihub_matches():
            if _row_is_final(row) and row.get("home_score") is not None and row.get("away_score") is not None:
                key = (row["date"], row["home_team"], row["away_team"])
                payload = _row_to_finished_match(row, source=row.get("source_name") or "lyihub_worldcup_static_json")
                if payload.get("score_source") == "score_90min":
                    by_key[key] = payload
                else:
                    by_key.setdefault(key, payload)

    if hasattr(local_store, "available_dates"):
        for date in local_store.available_dates():
            for row in local_store.list_web_fixtures(date):
                if _row_is_final(row) and row.get("home_score") is not None and row.get("away_score") is not None:
                    key = (row["date"], row["home_team"], row["away_team"])
                    by_key.setdefault(key, _row_to_finished_match(row, source=row.get("source_name") or "web_fixtures"))

    return sorted(by_key.values(), key=lambda item: (item["date"], item["match_id"]))


def _row_is_final(row: dict[str, Any]) -> bool:
    return str(row.get("status") or "").lower() == "final" or (
        row.get("home_score") is not None and row.get("away_score") is not None
    )


def _has_90_score(match: dict[str, Any]) -> bool:
    return match.get("home_goals_90") is not None and match.get("away_goals_90") is not None


def _row_to_finished_match(row: dict[str, Any], source: str) -> dict[str, Any]:
    score = _row_90_score(row)
    home_score = score["home"]
    away_score = score["away"]
    full_score = _row_full_score(row)
    winner = None
    loser = None
    decided_by_penalties = False
    home_penalties = away_penalties = None
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
        winner = row["home_team"] if home_penalties > away_penalties else row["away_team"]
        loser = row["away_team"] if winner == row["home_team"] else row["home_team"]
    elif home_score is not None and away_score is not None and home_score != away_score:
        winner = row["home_team"] if home_score > away_score else row["away_team"]
        loser = row["away_team"] if winner == row["home_team"] else row["home_team"]
    return {
        "match_id": row.get("match_id") or row.get("id"),
        "date": row["date"],
        "stage": row.get("stage") or row.get("group") or row.get("group_name"),
        "home_team": row["home_team"],
        "away_team": row["away_team"],
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
        "source": source,
        "score_source": score["source"],
        "source_url": row.get("source_url"),
        "fetched_at": row.get("fetched_at") or row.get("synced_at"),
    }


def _row_90_score(row: dict[str, Any]) -> dict[str, Any]:
    nested = _nested_score(row, ("score_90min", "score_90"))
    if nested["home"] is not None and nested["away"] is not None:
        nested["source"] = "score_90min"
        return nested
    return {"home": row.get("home_score"), "away": row.get("away_score"), "source": "row_score"}


def _row_full_score(row: dict[str, Any]) -> dict[str, Any]:
    nested = _nested_score(row, ("score_full", "score"))
    if nested["home"] is not None and nested["away"] is not None:
        return nested
    return {"home": row.get("home_score"), "away": row.get("away_score")}


def _nested_score(row: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    candidates = [row.get("payload") if isinstance(row.get("payload"), dict) else {}, row.get("detail", {}).get("match", {}) if isinstance(row.get("detail"), dict) else {}]
    for payload in candidates:
        for key in keys:
            score = payload.get(key)
            if isinstance(score, dict) and score.get("team_a") is not None and score.get("team_b") is not None:
                return {"home": score.get("team_a"), "away": score.get("team_b")}
    return {"home": None, "away": None}


def _finished_match_payload(match: dict[str, Any]) -> dict[str, Any]:
    payload = dict(match)
    payload["is_finished"] = True
    return payload


def _result_signature(match: dict[str, Any]) -> tuple[Any, ...]:
    return (
        match.get("home_goals_90"),
        match.get("away_goals_90"),
        match.get("home_goals_extra_time"),
        match.get("away_goals_extra_time"),
        match.get("home_penalties"),
        match.get("away_penalties"),
        match.get("winner"),
    )


def update_team_ratings_from_finished_matches(
    matches: list[dict[str, Any]],
    teams: dict[str, dict[str, Any]],
    processed_match_ids: set[str] | None = None,
) -> dict[str, Any]:
    processed_match_ids = processed_match_ids if processed_match_ids is not None else set()
    updated = []
    for match in matches:
        match_id = str(match.get("match_id") or _match_key(match))
        if match_id in processed_match_ids:
            continue
        home_goals = match.get("home_goals_90")
        away_goals = match.get("away_goals_90")
        if home_goals is None or away_goals is None:
            continue
        home = teams.setdefault(str(match["home_team"]), {"team": match["home_team"]})
        away = teams.setdefault(str(match["away_team"]), {"team": match["away_team"]})
        _ensure_rating_fields(home)
        _ensure_rating_fields(away)
        _apply_team_result(home, goals_for=int(home_goals), goals_against=int(away_goals), outcome=_outcome(home_goals, away_goals))
        _apply_team_result(away, goals_for=int(away_goals), goals_against=int(home_goals), outcome=_outcome(away_goals, home_goals))
        if match.get("decided_by_penalties") and match.get("winner"):
            teams[str(match["winner"])]["form_rating"] = round(float(teams[str(match["winner"])].get("form_rating", 0)) + 0.015, 4)
        for team in (home, away):
            team["last_updated"] = datetime.now(timezone.utc).isoformat()
        processed_match_ids.add(match_id)
        updated.append(match_id)
    return {"processed_count": len(updated), "processed_match_ids": updated}


def retrain_team_ratings_from_world_cup(
    matches: list[dict[str, Any]],
    prior_profiles: dict[str, dict[str, Any]] | None = None,
    world_cup_weight_override: float | None = None,
) -> dict[str, Any]:
    prior_profiles = prior_profiles or {}
    ordered = sorted([match for match in matches if _has_90_score(match)], key=lambda item: (item["date"], item["match_id"]))
    has_knockout = any(_is_knockout_stage(match.get("stage")) for match in ordered)
    world_cup_weight = float(world_cup_weight_override) if world_cup_weight_override is not None else (0.80 if has_knockout else 0.75)
    world_cup_weight = max(0.0, min(1.0, world_cup_weight))
    prior_weight = 1.0 - world_cup_weight
    teams = {team: dict(profile) for team, profile in prior_profiles.items()}
    for team, profile in teams.items():
        profile.setdefault("team", team)
        _ensure_rating_fields(profile)
        profile["world_cup_data_weight"] = world_cup_weight
        profile["_wc_goals_for"] = []
        profile["_wc_goals_against"] = []
        profile["_wc_outcomes"] = []

    for match in ordered:
        home = teams.setdefault(str(match["home_team"]), {"team": match["home_team"]})
        away = teams.setdefault(str(match["away_team"]), {"team": match["away_team"]})
        _ensure_rating_fields(home)
        _ensure_rating_fields(away)
        for team in (home, away):
            team["world_cup_data_weight"] = world_cup_weight
            team.setdefault("_wc_goals_for", [])
            team.setdefault("_wc_goals_against", [])
            team.setdefault("_wc_outcomes", [])
        home_goals = int(match["home_goals_90"])
        away_goals = int(match["away_goals_90"])
        home["_wc_goals_for"].append(home_goals)
        home["_wc_goals_against"].append(away_goals)
        home["_wc_outcomes"].append(_outcome(home_goals, away_goals))
        away["_wc_goals_for"].append(away_goals)
        away["_wc_goals_against"].append(home_goals)
        away["_wc_outcomes"].append(_outcome(away_goals, home_goals))
        if _is_knockout_stage(match.get("stage")) and match.get("winner") and match.get("loser"):
            teams[str(match["winner"])]["knockout_rating"] = float(teams[str(match["winner"])].get("knockout_rating", 0.0)) + 0.18
            teams[str(match["loser"])]["knockout_rating"] = float(teams[str(match["loser"])].get("knockout_rating", 0.0)) - 0.10
            teams[str(match["loser"])]["eliminated"] = True
            teams[str(match["winner"])]["eliminated"] = False

    for team, profile in teams.items():
        goals_for = profile.pop("_wc_goals_for", [])
        goals_against = profile.pop("_wc_goals_against", [])
        outcomes = profile.pop("_wc_outcomes", [])
        if not goals_for:
            continue
        prior_attack = float((prior_profiles.get(team) or {}).get("attack_rating") or profile.get("attack_rating") or 1.32)
        prior_defense = float((prior_profiles.get(team) or {}).get("defense_rating") or profile.get("defense_rating") or 1.32)
        prior_strength = float((prior_profiles.get(team) or {}).get("elo") or profile.get("strength_rating") or 1700)
        avg_for = sum(goals_for) / len(goals_for)
        avg_against = sum(goals_against) / len(goals_against)
        avg_outcome = sum(outcomes) / len(outcomes)
        profile["recent_matches"] = len(goals_for)
        profile["recent_goals_for"] = round(sum(goals_for), 4)
        profile["recent_goals_against"] = round(sum(goals_against), 4)
        profile["attack_rating"] = round(prior_weight * prior_attack + world_cup_weight * max(0.25, avg_for), 4)
        profile["defense_rating"] = round(prior_weight * prior_defense + world_cup_weight * max(0.25, avg_against), 4)
        profile["midfield_rating"] = round(55 + 30 * avg_outcome + 6 * (avg_for - avg_against), 4)
        profile["goal_scoring_rating"] = round(profile["attack_rating"], 4)
        profile["defensive_stability"] = round(max(0.0, 2.4 - profile["defense_rating"]), 4)
        profile["form_rating"] = round(avg_outcome - 0.5, 4)
        profile.setdefault("knockout_rating", 0.0)
        profile.setdefault("eliminated", False)
        profile["world_cup_data_weight"] = world_cup_weight
        profile["strength_rating"] = round(prior_weight * prior_strength + world_cup_weight * (1600 + 220 * (avg_outcome - 0.5) + 35 * (avg_for - avg_against)), 4)
        profile["elo"] = profile["strength_rating"]
        profile["xg_for"] = profile["attack_rating"]
        profile["xg_against"] = profile["defense_rating"]
        profile["last_updated"] = datetime.now(timezone.utc).isoformat()
    return {
        "teams": teams,
        "match_count": len(ordered),
        "world_cup_data_weight": world_cup_weight,
        "prior_weight": prior_weight,
        "has_knockout_results": has_knockout,
        "model_version": "world-cup-full-retrain-v1",
    }


def _ensure_rating_fields(team: dict[str, Any]) -> None:
    team.setdefault("strength_rating", team.get("elo", 1700))
    team.setdefault("attack_rating", 1.32)
    team.setdefault("defense_rating", 1.32)
    team.setdefault("form_rating", 0.0)
    team.setdefault("xg_for", team.get("attack_rating", 1.32))
    team.setdefault("xg_against", team.get("defense_rating", 1.32))
    team.setdefault("recent_goals_for", 0)
    team.setdefault("recent_goals_against", 0)
    team.setdefault("recent_matches", 0)
    team.setdefault("eliminated", False)


def _apply_team_result(team: dict[str, Any], *, goals_for: int, goals_against: int, outcome: float) -> None:
    matches = int(team.get("recent_matches") or 0)
    decay = 0.82
    team["recent_goals_for"] = goals_for if matches == 0 else round(float(team["recent_goals_for"]) * decay + goals_for, 4)
    team["recent_goals_against"] = goals_against if matches == 0 else round(float(team["recent_goals_against"]) * decay + goals_against, 4)
    team["recent_matches"] = matches + 1
    team["attack_rating"] = round(0.78 * float(team.get("attack_rating") or 1.32) + 0.22 * max(0.25, goals_for), 4)
    team["defense_rating"] = round(0.78 * float(team.get("defense_rating") or 1.32) + 0.22 * max(0.25, goals_against), 4)
    team["xg_for"] = team["attack_rating"]
    team["xg_against"] = team["defense_rating"]
    team["form_rating"] = round(0.70 * float(team.get("form_rating") or 0.0) + 0.30 * (outcome - 0.5), 4)
    team["strength_rating"] = round(float(team.get("strength_rating") or team.get("elo") or 1700) + 18 * (outcome - 0.5), 4)
    team["elo"] = team["strength_rating"]


def _outcome(goals_for: int, goals_against: int) -> float:
    if goals_for > goals_against:
        return 1.0
    if goals_for < goals_against:
        return 0.0
    return 0.5


def update_knockout_bracket_with_result(
    bracket: dict[str, Any],
    match_result: dict[str, Any],
    teams: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    teams = teams if teams is not None else {}
    winner = match_result.get("winner")
    loser = match_result.get("loser")
    is_knockout = _is_knockout_stage(match_result.get("stage"))
    if winner and is_knockout:
        teams.setdefault(str(winner), {"team": winner})["eliminated"] = False
    if loser and is_knockout:
        teams.setdefault(str(loser), {"team": loser})["eliminated"] = True
    matches = bracket.setdefault("matches", [])
    source_id = str(match_result.get("match_id") or "")
    for item in matches:
        if item.get("match_id") == source_id:
            item.update(match_result)
        for side in ("home_team", "away_team"):
            if item.get(side) == f"Winner {source_id}" and winner:
                item[side] = winner
            if is_knockout and loser and item.get(side) == loser and not item.get("is_finished"):
                item[side] = winner if winner else None
    bracket["last_result_sync_time"] = datetime.now(timezone.utc).isoformat()
    return bracket


def _is_knockout_stage(stage: Any) -> bool:
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


def validate_bracket_after_result_sync(bracket: dict[str, Any], teams: dict[str, dict[str, Any]]) -> dict[str, Any]:
    eliminated = {team for team, payload in teams.items() if payload.get("eliminated")}
    future = []
    for match in bracket.get("matches", []):
        if match.get("is_finished") or str(match.get("status") or "").lower() == "final":
            continue
        for side in ("home_team", "away_team"):
            if match.get(side) in eliminated:
                future.append(match[side])
    return {
        "valid": not future,
        "eliminated_future_teams": sorted(set(future)),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def invalidate_prediction_cache_after_result_sync(cache: Any, finished_matches: list[dict[str, Any]]) -> dict[str, Any]:
    if hasattr(cache, "clear_predictions"):
        return {"invalidated_predictions": cache.clear_predictions(), "match_count": len(finished_matches)}
    if isinstance(cache, dict):
        count = len(cache)
        cache.clear()
        return {"invalidated_predictions": count, "match_count": len(finished_matches)}
    return {"invalidated_predictions": 0, "match_count": len(finished_matches)}


def write_prediction_outputs(
    *,
    output_dir: Path,
    predictions: list[dict[str, Any]],
    bracket: dict[str, Any],
    team_ratings: list[dict[str, Any]],
    result_sync_log: dict[str, Any],
    regression_evaluation: dict[str, Any] | None = None,
    model_retraining_report: dict[str, Any] | None = None,
) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    updated_json = output_dir / "updated_predictions.json"
    updated_csv = output_dir / "updated_predictions.csv"
    bracket_json = output_dir / "bracket_predictions.json"
    ratings_json = output_dir / "team_ratings_updated.json"
    log_json = output_dir / "result_sync_log.json"
    regression_json = output_dir / "regression_evaluation.json"
    retraining_json = output_dir / "model_retraining_report.json"
    updated_json.write_text(json.dumps(predictions, ensure_ascii=False, indent=2), encoding="utf-8")
    bracket_json.write_text(json.dumps(bracket, ensure_ascii=False, indent=2), encoding="utf-8")
    ratings_json.write_text(json.dumps(team_ratings, ensure_ascii=False, indent=2), encoding="utf-8")
    log_json.write_text(json.dumps(result_sync_log, ensure_ascii=False, indent=2), encoding="utf-8")
    if regression_evaluation is not None:
        regression_json.write_text(json.dumps(regression_evaluation, ensure_ascii=False, indent=2), encoding="utf-8")
    if model_retraining_report is not None:
        retraining_json.write_text(json.dumps(model_retraining_report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_predictions_csv(updated_csv, predictions)
    outputs = {
        "updated_predictions_json": str(updated_json),
        "updated_predictions_csv": str(updated_csv),
        "bracket_predictions_json": str(bracket_json),
        "team_ratings_updated_json": str(ratings_json),
        "result_sync_log_json": str(log_json),
    }
    if regression_evaluation is not None:
        outputs["regression_evaluation_json"] = str(regression_json)
    if model_retraining_report is not None:
        outputs["model_retraining_report_json"] = str(retraining_json)
    return outputs


def evaluate_world_cup_regression(
    matches: list[dict[str, Any]],
    predictions_by_match_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    rows = []
    correct = 0
    advance_correct = 0
    advance_count = 0
    over25_correct = 0
    over25_count = 0
    log_losses = []
    briers = []
    goal_errors = []
    calibration_errors = []
    for match in matches:
        match_id = str(match.get("match_id") or _match_key(match))
        prediction = predictions_by_match_id.get(match_id)
        if not prediction or not _has_90_score(match):
            continue
        probabilities = _prediction_probabilities(prediction)
        actual = _actual_outcome(int(match["home_goals_90"]), int(match["away_goals_90"]))
        predicted = max(probabilities.items(), key=lambda item: item[1])[0]
        targets = {key: 1.0 if key == actual else 0.0 for key in ("home", "draw", "away")}
        brier = sum((probabilities[key] - targets[key]) ** 2 for key in targets) / 3
        log_loss = -math.log(max(1e-15, min(1 - 1e-15, probabilities[actual])))
        expected = prediction.get("expected_goals") or {}
        home_xg = float(expected.get("home", match["home_goals_90"]))
        away_xg = float(expected.get("away", match["away_goals_90"]))
        home_error = home_xg - float(match["home_goals_90"])
        away_error = away_xg - float(match["away_goals_90"])
        goal_errors.extend([home_error, away_error])
        actual_over25 = int(match["home_goals_90"]) + int(match["away_goals_90"]) > 2.5
        over25_prob = _prediction_over25_probability(prediction)
        predicted_over25 = over25_prob >= 0.5 if over25_prob is not None else None
        if predicted_over25 is not None:
            over25_count += 1
            if predicted_over25 == actual_over25:
                over25_correct += 1
        if predicted == actual:
            correct += 1
        calibration_errors.append(abs(probabilities[predicted] - (1.0 if predicted == actual else 0.0)))
        if match.get("winner"):
            advance_count += 1
            predicted_advancer = match["home_team"] if probabilities["home"] >= probabilities["away"] else match["away_team"]
            if predicted_advancer == match.get("winner"):
                advance_correct += 1
        log_losses.append(log_loss)
        briers.append(brier)
        rows.append(
            {
                "match_id": match_id,
                "home_team": match.get("home_team"),
                "away_team": match.get("away_team"),
                "actual_result_90": actual,
                "predicted_result_90": predicted,
                "home_goal_error": round(home_error, 6),
                "away_goal_error": round(away_error, 6),
                "brier_score": round(brier, 6),
                "log_loss": round(log_loss, 6),
                "winner": match.get("winner"),
                "actual_over25": actual_over25,
                "predicted_over25": predicted_over25,
                "over25_prob": round(over25_prob, 6) if over25_prob is not None else None,
            }
        )
    count = len(rows)
    mae = sum(abs(error) for error in goal_errors) / len(goal_errors) if goal_errors else 0.0
    rmse = math.sqrt(sum(error * error for error in goal_errors) / len(goal_errors)) if goal_errors else 0.0
    return {
        "match_count": count,
        "accuracy_90": round(correct / count, 6) if count else 0.0,
        "log_loss": round(sum(log_losses) / count, 6) if count else 0.0,
        "brier_score": round(sum(briers) / count, 6) if count else 0.0,
        "calibration_error": round(sum(calibration_errors) / count, 6) if count else 0.0,
        "goals_mae": round(mae, 6),
        "goals_rmse": round(rmse, 6),
        "over25_accuracy": round(over25_correct / over25_count, 6) if over25_count else None,
        "over25_sample_count": over25_count,
        "advance_accuracy": round(advance_correct / advance_count, 6) if advance_count else None,
        "advance_sample_count": advance_count,
        "per_match_errors": rows,
    }


def evaluate_round_of_32_regression(
    matches: list[dict[str, Any]],
    predictions_by_match_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    r32_matches = [
        match
        for match in matches
        if _is_round_of_32_stage(match.get("stage") or match.get("group") or match.get("tournament"))
    ]
    rows: list[dict[str, Any]] = []
    correct = 0
    log_losses: list[float] = []
    briers: list[float] = []
    calibration_errors: list[float] = []
    draw_total = 0
    draw_correct = 0
    favorite_predictions = 0
    favorite_prediction_correct = 0
    upset_total = 0
    upset_correct = 0
    for match in r32_matches:
        match_id = str(match.get("match_id") or _match_key(match))
        prediction = predictions_by_match_id.get(match_id)
        if not prediction or not _has_90_score(match):
            continue
        probabilities = _prediction_probabilities(prediction)
        actual = _actual_outcome(int(match["home_goals_90"]), int(match["away_goals_90"]))
        predicted = max(probabilities.items(), key=lambda item: item[1])[0]
        confidence = probabilities[predicted]
        targets = {key: 1.0 if key == actual else 0.0 for key in ("home", "draw", "away")}
        brier = sum((probabilities[key] - targets[key]) ** 2 for key in targets) / 3
        log_loss = -math.log(max(1e-15, min(1 - 1e-15, probabilities[actual])))
        is_correct = predicted == actual
        correct += 1 if is_correct else 0
        log_losses.append(log_loss)
        briers.append(brier)
        calibration_errors.append(abs(confidence - (1.0 if is_correct else 0.0)))
        if actual == "draw":
            draw_total += 1
            if predicted == "draw":
                draw_correct += 1
        side_probs = {"home": probabilities["home"], "away": probabilities["away"]}
        favorite_side = max(side_probs.items(), key=lambda item: item[1])[0]
        underdog_side = "away" if favorite_side == "home" else "home"
        if predicted == favorite_side:
            favorite_predictions += 1
            if actual == favorite_side:
                favorite_prediction_correct += 1
        if actual == underdog_side:
            upset_total += 1
            if predicted == underdog_side:
                upset_correct += 1
        rows.append(
            {
                "match_id": match_id,
                "date": match.get("date"),
                "stage": match.get("stage") or match.get("group"),
                "home_team": match.get("home_team"),
                "away_team": match.get("away_team"),
                "score_90": f"{match.get('home_goals_90')}-{match.get('away_goals_90')}",
                "actual_result_90": actual,
                "probabilities": {key: round(value, 6) for key, value in probabilities.items()},
                "predicted_result_90": predicted,
                "correct": is_correct,
                "error_type": None if is_correct else _r32_error_type(match, probabilities, predicted, actual),
                "log_loss": round(log_loss, 6),
                "brier_score": round(brier, 6),
                "decided_by_penalties": bool(match.get("decided_by_penalties")),
                "winner": match.get("winner"),
                "loser": match.get("loser"),
            }
        )
    count = len(rows)
    return {
        "stage": "round_of_32",
        "match_count": count,
        "accuracy_90": round(correct / count, 6) if count else 0.0,
        "log_loss": round(sum(log_losses) / count, 6) if count else 0.0,
        "brier_score": round(sum(briers) / count, 6) if count else 0.0,
        "calibration_error": round(sum(calibration_errors) / count, 6) if count else 0.0,
        "draw_recall": round(draw_correct / draw_total, 6) if draw_total else None,
        "draw_sample_count": draw_total,
        "favorite_win_precision": round(favorite_prediction_correct / favorite_predictions, 6) if favorite_predictions else None,
        "favorite_prediction_count": favorite_predictions,
        "upset_recall": round(upset_correct / upset_total, 6) if upset_total else None,
        "upset_sample_count": upset_total,
        "per_match_errors": rows,
    }


def _is_round_of_32_stage(stage: Any) -> bool:
    value = str(stage or "").lower()
    return "1/16" in value or "round of 32" in value or "round 32" in value


def _r32_error_type(match: dict[str, Any], probabilities: dict[str, float], predicted: str, actual: str) -> str:
    if actual == "draw" and bool(match.get("decided_by_penalties")):
        return "点球晋级被错误影响到90分钟模型"
    if actual == "draw":
        return "平局被预测成胜负"
    side_probs = {"home": probabilities["home"], "away": probabilities["away"]}
    favorite_side = max(side_probs.items(), key=lambda item: item[1])[0]
    if predicted == "draw":
        return "强队胜被预测成平" if actual == favorite_side else "本届世界杯状态权重不足"
    if actual != favorite_side:
        return "弱队爆冷未识别"
    if probabilities[predicted] >= 0.62:
        return "赔率/历史强度权重过高"
    return "本届世界杯状态权重不足"


def _prediction_probabilities(prediction: dict[str, Any]) -> dict[str, float]:
    payload = prediction.get("probabilities") or prediction.get("ensemble") or prediction
    values = {
        "home": float(payload.get("home", payload.get("home_win", 0.0))),
        "draw": float(payload.get("draw", 0.0)),
        "away": float(payload.get("away", payload.get("away_win", 0.0))),
    }
    total = sum(max(0.0, value) for value in values.values())
    if total <= 0:
        return {"home": 1 / 3, "draw": 1 / 3, "away": 1 / 3}
    return {key: max(0.0, value) / total for key, value in values.items()}


def _prediction_over25_probability(prediction: dict[str, Any]) -> float | None:
    over_summary = prediction.get("over25_summary") if isinstance(prediction.get("over25_summary"), dict) else {}
    totals_25 = (prediction.get("totals") or {}).get("2.5") if isinstance(prediction.get("totals"), dict) else {}
    candidates = [
        prediction.get("final_over25_prob"),
        prediction.get("over25_prob"),
        over_summary.get("final_over25_prob"),
        totals_25.get("over") if isinstance(totals_25, dict) else None,
    ]
    for value in candidates:
        if value is None:
            continue
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            continue
    expected = prediction.get("expected_goals") or {}
    if isinstance(expected, dict) and {"home", "away"} <= set(expected):
        try:
            return 1.0 if float(expected["home"]) + float(expected["away"]) > 2.5 else 0.0
        except (TypeError, ValueError):
            return None
    return None


def _actual_outcome(home_goals: int, away_goals: int) -> str:
    if home_goals > away_goals:
        return "home"
    if home_goals < away_goals:
        return "away"
    return "draw"


def _write_predictions_csv(path: Path, predictions: list[dict[str, Any]]) -> None:
    fields = sorted({key for item in predictions for key in item if not isinstance(item.get(key), (dict, list))})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in predictions:
            writer.writerow({key: item.get(key) for key in fields})


def _match_key(match: dict[str, Any]) -> str:
    raw = "|".join(str(match.get(key) or "") for key in ("date", "stage", "home_team", "away_team"))
    return raw.replace(" ", "-").lower()
