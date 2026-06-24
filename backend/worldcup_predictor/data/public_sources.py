from __future__ import annotations

import html
import os
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import httpx

from .training import HistoricalMatch, parse_historical_csv


WIKIPEDIA_WORLD_CUP_URL = "https://en.wikipedia.org/wiki/2026_FIFA_World_Cup"
WIKIPEDIA_PARSE_URL = "https://en.wikipedia.org/w/api.php"
HISTORICAL_RESULTS_URL = "https://raw.githubusercontent.com/martj42/international_results/master/results.csv"
KAGGLE_DATASET = "martj42/international-football-results-from-1872-to-2017"

TEAM_ALIASES = {
    "Korea Republic": "South Korea",
    "Czechia": "Czech Republic",
    "USA": "United States",
    "Türkiye": "Turkey",
    "Cabo Verde": "Cape Verde",
    "Côte d'Ivoire": "Ivory Coast",
    "IR Iran": "Iran",
    "Curaçao": "Curacao",
    "DR Congo": "DR Congo",
    "Congo DR": "DR Congo",
}

GROUP_MATCH_DATES = {
    "Group A": ["2026-06-11", "2026-06-11", "2026-06-18", "2026-06-18", "2026-06-24", "2026-06-24"],
    "Group B": ["2026-06-12", "2026-06-13", "2026-06-18", "2026-06-18", "2026-06-24", "2026-06-24"],
    "Group C": ["2026-06-13", "2026-06-13", "2026-06-19", "2026-06-19", "2026-06-24", "2026-06-24"],
    "Group D": ["2026-06-12", "2026-06-13", "2026-06-19", "2026-06-19", "2026-06-25", "2026-06-25"],
    "Group E": ["2026-06-14", "2026-06-14", "2026-06-20", "2026-06-20", "2026-06-25", "2026-06-25"],
    "Group F": ["2026-06-14", "2026-06-14", "2026-06-20", "2026-06-20", "2026-06-25", "2026-06-25"],
    "Group G": ["2026-06-15", "2026-06-15", "2026-06-21", "2026-06-21", "2026-06-26", "2026-06-26"],
    "Group H": ["2026-06-15", "2026-06-15", "2026-06-21", "2026-06-21", "2026-06-26", "2026-06-26"],
    "Group I": ["2026-06-16", "2026-06-16", "2026-06-22", "2026-06-22", "2026-06-26", "2026-06-26"],
    "Group J": ["2026-06-16", "2026-06-16", "2026-06-22", "2026-06-22", "2026-06-27", "2026-06-27"],
    "Group K": ["2026-06-17", "2026-06-17", "2026-06-23", "2026-06-23", "2026-06-27", "2026-06-27"],
    "Group L": ["2026-06-17", "2026-06-17", "2026-06-23", "2026-06-23", "2026-06-27", "2026-06-27"],
}


class PublicWorldCupScraper:
    def __init__(self, client: httpx.Client | None = None):
        self.client = client or httpx.Client(
            timeout=30,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "world-cup-prediction-local/0.1 "
                    "(research dashboard; contact: local-user)"
                )
            },
        )

    def fetch_world_cup_fixtures(self) -> list[dict[str, Any]]:
        payload = self._get_json(
            WIKIPEDIA_PARSE_URL,
            params={
                "action": "parse",
                "page": "2026_FIFA_World_Cup",
                "prop": "text",
                "format": "json",
            },
        )
        html_text = payload["parse"]["text"]["*"]
        return self.parse_wikipedia_world_cup(html_text)

    def fetch_historical_matches(self) -> list[HistoricalMatch]:
        if os.getenv("KAGGLE_USERNAME") and os.getenv("KAGGLE_KEY"):
            kaggle_matches = self._try_kaggle_historical_matches()
            if kaggle_matches:
                return kaggle_matches
        return self.parse_historical_results_csv(self._get_text(HISTORICAL_RESULTS_URL))

    def parse_historical_results_csv(self, text: str) -> list[HistoricalMatch]:
        return parse_historical_csv(text)

    def parse_wikipedia_world_cup(self, html_text: str) -> list[dict[str, Any]]:
        groups = self._parse_group_members(html_text)
        fixtures: list[dict[str, Any]] = []
        seen: set[str] = set()
        pattern = re.compile(
            r'href="(?P<url>https://www\.fifa\.com/en/match-centre/match/[^"]+)">'
            r'"(?P<title>[^"]+?) \| First Stage \| FIFA World Cup 2026"',
            re.S,
        )
        group_counts: dict[str, int] = {}
        for match in pattern.finditer(html_text):
            title = html.unescape(match.group("title"))
            if " vs " not in title:
                continue
            home, away = [part.strip() for part in title.split(" vs ", 1)]
            canonical_home = self._canonical_team(home)
            canonical_away = self._canonical_team(away)
            match_id = match.group("url").rstrip("/").split("/")[-1]
            if match_id in seen:
                continue
            seen.add(match_id)
            group = self._group_for_pair(canonical_home, canonical_away, groups)
            group_index = group_counts.get(group or "", 0)
            if group:
                group_counts[group] = group_index + 1
            match_date = self._date_for_group_match(group, group_index)
            fixtures.append(
                {
                    "id": f"web-{match_id}",
                    "date": match_date,
                    "kickoff": match_date,
                    "home_team": canonical_home,
                    "away_team": canonical_away,
                    "group": group,
                    "venue": self._venue_hint(home, away),
                    "status": "scheduled",
                    "home_score": None,
                    "away_score": None,
                    "source_url": match.group("url"),
                }
            )
        return fixtures

    def _get_text(self, url: str, params: dict[str, str] | None = None) -> str:
        try:
            response = self.client.get(url, params=params)
            response.raise_for_status()
            return response.text
        except httpx.HTTPStatusError:
            return self._curl_text(url, params=params)

    def _get_json(self, url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        import json

        text = self._get_text(url, params=params)
        return json.loads(text)

    def _curl_text(self, url: str, params: dict[str, str] | None = None) -> str:
        if params:
            from urllib.parse import urlencode

            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}{urlencode(params)}"
        result = subprocess.run(
            ["curl", "-sL", "-A", "world-cup-prediction-local/0.1", url],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return result.stdout

    def _try_kaggle_historical_matches(self) -> list[HistoricalMatch]:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            try:
                subprocess.run(
                    ["kaggle", "datasets", "download", "-d", KAGGLE_DATASET, "-p", str(target)],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
            except (FileNotFoundError, subprocess.SubprocessError):
                return []
            archives = list(target.glob("*.zip"))
            if not archives:
                return []
            with zipfile.ZipFile(archives[0]) as archive:
                for name in archive.namelist():
                    if name.endswith("results.csv"):
                        return parse_historical_csv(archive.read(name).decode("utf-8"))
        return []

    def _parse_group_members(self, html_text: str) -> dict[str, set[str]]:
        groups: dict[str, set[str]] = {}
        group_matches = list(
            re.finditer(
                r'<h3 id="Group_([A-L])">Group ([A-L])</h3>|id="Group_([A-L])">Group ([A-L])</h3>',
                html_text,
            )
        )
        for index, match in enumerate(group_matches):
            group_letter = match.group(1) or match.group(3)
            group = f"Group {group_letter}"
            start = match.end()
            end = group_matches[index + 1].start() if index + 1 < len(group_matches) else len(html_text)
            segment = html_text[start:end]
            teams = set(
                self._canonical_team(html.unescape(team))
                for team in re.findall(
                    r'<a href="/wiki/[^"]+_national_[^"]+_team"(?: title="[^"]+")?>([^<]+)</a>',
                    segment,
                )
            )
            if teams:
                groups[group] = teams
        return groups

    def _group_for_pair(self, home: str, away: str, groups: dict[str, set[str]]) -> str | None:
        for group, teams in groups.items():
            if home in teams and away in teams:
                return group
        return None

    def _date_for_group_match(self, group: str | None, group_index: int) -> str:
        dates = GROUP_MATCH_DATES.get(group or "")
        if not dates:
            return "2026-06-16"
        return dates[min(group_index, len(dates) - 1)]

    def _canonical_team(self, team: str) -> str:
        return TEAM_ALIASES.get(team, team)

    def _venue_hint(self, home: str, away: str) -> str | None:
        first_match_venues = {
            ("France", "Senegal"): "New York New Jersey Stadium",
            ("Iraq", "Norway"): "Boston Stadium",
            ("Argentina", "Algeria"): "Kansas City Stadium",
            ("Austria", "Jordan"): "San Francisco Bay Area Stadium",
        }
        return first_match_venues.get((home, away))
