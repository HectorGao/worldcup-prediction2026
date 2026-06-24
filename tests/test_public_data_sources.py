from pathlib import Path

from fastapi.testclient import TestClient

from worldcup_predictor.api import create_app
from worldcup_predictor.data.public_sources import PublicWorldCupScraper
from worldcup_predictor.data.training import HistoricalMatch, build_team_profiles, time_decay_weight
from worldcup_predictor.service import WorldCupService


WORLD_CUP_HTML = """
<h3 id="Group_I">Group I</h3>
<table class="wikitable"><tr><th>Team</th></tr>
<tr><th><a href="/wiki/France_national_football_team">France</a></th></tr>
<tr><th><a href="/wiki/Senegal_national_football_team">Senegal</a></th></tr>
<tr><th><a href="/wiki/Iraq_national_football_team">Iraq</a></th></tr>
<tr><th><a href="/wiki/Norway_national_football_team">Norway</a></th></tr>
</table>
<li id="cite_note-259"><span class="reference-text"><a rel="nofollow" class="external text" href="https://www.fifa.com/en/match-centre/match/17/285023/289273/400021490">"France vs Senegal | First Stage | FIFA World Cup 2026"</a>. FIFA. Retrieved May 1, 2026.</span></li>
<li id="cite_note-260"><span class="reference-text"><a rel="nofollow" class="external text" href="https://www.fifa.com/en/match-centre/match/17/285023/289273/400021488">"Iraq vs Norway | First Stage | FIFA World Cup 2026"</a>. FIFA. Retrieved May 1, 2026.</span></li>
"""


HISTORICAL_CSV = """date,home_team,away_team,home_score,away_score,tournament,city,country,neutral
2026-06-06,Belgium,Tunisia,5,0,Friendly,Brussels,Belgium,FALSE
2022-11-18,Egypt,Belgium,2,1,Friendly,Kuwait City,Kuwait,TRUE
2018-06-06,Belgium,Egypt,3,0,Friendly,Brussels,Belgium,FALSE
2005-02-09,Egypt,Belgium,4,0,Friendly,Cairo,Egypt,FALSE
"""


def test_wikipedia_parser_uses_public_page_structure_for_fixture_pairs():
    scraper = PublicWorldCupScraper()
    fixtures = scraper.parse_wikipedia_world_cup(WORLD_CUP_HTML)

    assert len(fixtures) == 2
    assert fixtures[0]["id"] == "web-400021490"
    assert fixtures[0]["group"] == "Group I"
    assert fixtures[0]["home_team"] == "France"
    assert fixtures[0]["away_team"] == "Senegal"
    assert fixtures[0]["source_url"].startswith("https://www.fifa.com/")


def test_historical_csv_builds_recently_weighted_profiles_and_h2h():
    scraper = PublicWorldCupScraper()
    matches = scraper.parse_historical_results_csv(HISTORICAL_CSV)
    profiles = build_team_profiles(matches, as_of="2026-06-16", half_life_years=5.0)

    recent_weight = time_decay_weight("2026-06-06", "2026-06-16", half_life_years=5.0)
    older_weight = time_decay_weight("2005-02-09", "2026-06-16", half_life_years=5.0)

    assert len(matches) == 4
    assert recent_weight > 0.99
    assert older_weight < 0.1
    assert profiles["Belgium"]["recent_weighted_matches"] > profiles["Egypt"]["recent_weighted_matches"]
    assert profiles["Belgium"]["elo"] > 1500
    assert profiles["Belgium"]["h2h"]["Egypt"]["matches"] == 3


def test_public_data_api_endpoints_and_date_fallback(tmp_path: Path):
    service = WorldCupService(db_path=tmp_path / "worldcup.sqlite3")
    service.save_web_fixtures(
        [
            {
                "id": "web-400021490",
                "date": "2026-06-16",
                "kickoff": "2026-06-16",
                "home_team": "France",
                "away_team": "Senegal",
                "group": "Group I",
                "venue": "New York New Jersey Stadium",
                "status": "scheduled",
                "source_url": "https://www.fifa.com/example",
            }
        ],
        source_name="wikipedia_fifa_links",
    )
    service.save_historical_matches(
        [
            HistoricalMatch(
                date="2026-06-06",
                home_team="Belgium",
                away_team="Tunisia",
                home_score=5,
                away_score=0,
                tournament="Friendly",
                neutral=False,
            )
        ]
    )

    assert service.available_dates() == ["2026-06-16"]
    assert service.default_match_date("2026-06-20") == "2026-06-16"
    assert service.list_matches("2026-06-20")[0]["id"] == "web-400021490"

    app = create_app(db_path=tmp_path / "worldcup.sqlite3")
    app.state.service = service
    client = TestClient(app)

    assert client.get("/api/matches/available-dates").json()["dates"] == ["2026-06-16"]
    assert client.get("/api/matches", params={"date": "2026-06-20"}).json()["date"] == "2026-06-16"
    assert client.get("/api/meta").json()["web_sources"]["fixture_count"] == 1
    ranking = client.get("/api/teams/rankings").json()["teams"][0]
    assert ranking["team"] == "Belgium"
    assert "h2h" not in ranking

    analysis = client.get("/api/matches/web-400021490/analysis").json()
    assert "head_to_head" in analysis
    assert "h2h" not in analysis["team_profiles"]["home"]
