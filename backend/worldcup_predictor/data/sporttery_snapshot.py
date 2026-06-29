from __future__ import annotations

from typing import Any


SPORTTERY_LOTTERY_SNAPSHOT: list[dict[str, Any]] = [
    {
        "fixture_id": "lyihub-54327932",
        "match_no": "周日073",
        "business_date": "2026-06-28",
        "date": "2026-06-29",
        "league": "世界杯",
        "home_team": "South Africa",
        "away_team": "Canada",
        "spf": {"home": 5.65, "draw": 3.50, "away": 1.50},
        "rqspf": {"line": "+1", "home": 2.25, "draw": 3.00, "away": 2.84},
    },
    {
        "fixture_id": "lyihub-54327935",
        "match_no": "周一074",
        "business_date": "2026-06-29",
        "date": "2026-06-30",
        "league": "世界杯",
        "home_team": "Brazil",
        "away_team": "Japan",
        "spf": {"home": 1.52, "draw": 3.56, "away": 5.25},
        "rqspf": {"line": "-1", "home": 2.77, "draw": 3.28, "away": 2.16},
    },
    {
        "fixture_id": "lyihub-54327933",
        "match_no": "周一075",
        "business_date": "2026-06-29",
        "date": "2026-06-30",
        "league": "世界杯",
        "home_team": "Germany",
        "away_team": "Paraguay",
        "spf": {"home": 1.24, "draw": 4.90, "away": 8.40},
        "rqspf": {"line": "-1", "home": 1.82, "draw": 3.65, "away": 3.28},
    },
    {
        "fixture_id": "lyihub-54327934",
        "match_no": "周一076",
        "business_date": "2026-06-29",
        "date": "2026-06-30",
        "league": "世界杯",
        "home_team": "Netherlands",
        "away_team": "Morocco",
        "spf": {"home": 1.89, "draw": 3.07, "away": 3.64},
        "rqspf": {"line": "-1", "home": 3.82, "draw": 3.58, "away": 1.70},
    },
    {
        "fixture_id": "lyihub-54327937",
        "match_no": "周二077",
        "business_date": "2026-06-30",
        "date": "2026-07-01",
        "league": "世界杯",
        "home_team": "Ivory Coast",
        "away_team": "Norway",
        "spf": {"home": 3.70, "draw": 3.33, "away": 1.79},
        "rqspf": {"line": "+1", "home": 1.79, "draw": 3.70, "away": 3.33},
    },
    {
        "fixture_id": "lyihub-54327936",
        "match_no": "周二078",
        "business_date": "2026-06-30",
        "date": "2026-07-01",
        "league": "世界杯",
        "home_team": "France",
        "away_team": "Sweden",
        "spf": {"home": 1.18, "draw": 5.50, "away": 10.00},
        "rqspf": {"line": "-1", "home": 1.67, "draw": 3.85, "away": 3.70},
    },
]


def sporttery_snapshot_ids() -> set[str]:
    return {str(item["fixture_id"]) for item in SPORTTERY_LOTTERY_SNAPSHOT}
