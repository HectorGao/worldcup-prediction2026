from __future__ import annotations

from typing import Any


SPORTTERY_LOTTERY_SNAPSHOT: list[dict[str, Any]] = [
    {
        "fixture_id": "lyihub-54327935",
        "match_no": "周一074",
        "business_date": "2026-06-29",
        "date": "2026-06-30",
        "league": "世界杯",
        "home_team": "Brazil",
        "away_team": "Japan",
        "spf": {"home": 1.49, "draw": 3.72, "away": 5.28},
        "rqspf": {"line": "-1", "home": 2.71, "draw": 3.15, "away": 2.26},
    },
    {
        "fixture_id": "lyihub-54327933",
        "match_no": "周一075",
        "business_date": "2026-06-29",
        "date": "2026-06-30",
        "league": "世界杯",
        "home_team": "Germany",
        "away_team": "Paraguay",
        "spf": {"home": 1.22, "draw": 5.00, "away": 9.10},
        "rqspf": {"line": "-1", "home": 1.69, "draw": 4.05, "away": 3.44},
    },
    {
        "fixture_id": "lyihub-54327934",
        "match_no": "周一076",
        "business_date": "2026-06-29",
        "date": "2026-06-30",
        "league": "世界杯",
        "home_team": "Netherlands",
        "away_team": "Morocco",
        "spf": {"home": 1.97, "draw": 3.00, "away": 3.47},
        "rqspf": {"line": "-1", "home": 4.18, "draw": 3.48, "away": 1.66},
    },
    {
        "fixture_id": "lyihub-54327937",
        "match_no": "周二077",
        "business_date": "2026-06-30",
        "date": "2026-07-01",
        "league": "世界杯",
        "home_team": "Ivory Coast",
        "away_team": "Norway",
        "spf": {"home": 3.77, "draw": 3.35, "away": 1.77},
        "rqspf": {"line": "+1", "home": 1.83, "draw": 3.63, "away": 3.25},
    },
    {
        "fixture_id": "lyihub-54327936",
        "match_no": "周二078",
        "business_date": "2026-06-30",
        "date": "2026-07-01",
        "league": "世界杯",
        "home_team": "France",
        "away_team": "Sweden",
        "spf": {"home": 1.16, "draw": 5.80, "away": 10.50},
        "rqspf": {"line": "-1", "home": 1.60, "draw": 3.95, "away": 3.98},
    },
    {
        "fixture_id": "lyihub-54327939",
        "match_no": "周二079",
        "business_date": "2026-06-30",
        "date": "2026-07-01",
        "league": "世界杯",
        "home_team": "Mexico",
        "away_team": "Ecuador",
        "spf": {"home": 2.00, "draw": 2.70, "away": 3.86},
        "rqspf": {"line": "-1", "home": 4.50, "draw": 3.40, "away": 1.63},
    },
    {
        "fixture_id": "lyihub-54327940",
        "match_no": "周三080",
        "business_date": "2026-07-01",
        "date": "2026-07-02",
        "league": "世界杯",
        "home_team": "England",
        "away_team": "DR Congo",
        "spf": {"home": 1.17, "draw": 5.25, "away": 12.00},
        "rqspf": {"line": "-1", "home": 1.71, "draw": 3.50, "away": 3.86},
    },
    {
        "fixture_id": "lyihub-54327943",
        "match_no": "周三081",
        "business_date": "2026-07-01",
        "date": "2026-07-02",
        "league": "世界杯",
        "home_team": "Belgium",
        "away_team": "Senegal",
        "spf": {"home": 1.99, "draw": 3.00, "away": 3.42},
        "rqspf": {"line": "-1", "home": 4.15, "draw": 3.60, "away": 1.64},
    },
    {
        "fixture_id": "lyihub-54327941",
        "match_no": "周三082",
        "business_date": "2026-07-01",
        "date": "2026-07-02",
        "league": "世界杯",
        "home_team": "United States",
        "away_team": "Bosnia and Herzegovina",
        "spf": {"home": 1.27, "draw": 4.50, "away": 8.40},
        "rqspf": {"line": "-1", "home": 1.93, "draw": 3.50, "away": 3.08},
    },
]


def sporttery_snapshot_ids() -> set[str]:
    return {str(item["fixture_id"]) for item in SPORTTERY_LOTTERY_SNAPSHOT}


def sporttery_window_anchor_date() -> str:
    return min(str(item["business_date"]) for item in SPORTTERY_LOTTERY_SNAPSHOT)


def sporttery_window_match_start_date() -> str:
    return min(str(item["date"]) for item in SPORTTERY_LOTTERY_SNAPSHOT)
