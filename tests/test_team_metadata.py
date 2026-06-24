from worldcup_predictor.team_metadata import display_team, enrich_fixture


def test_team_metadata_localizes_chinese_names_and_flags():
    assert display_team("France") == {"name": "France", "zh": "法国", "flag": "🇫🇷", "iso2": "FR"}
    assert display_team("United States") == {
        "name": "United States",
        "zh": "美国",
        "flag": "🇺🇸",
        "iso2": "US",
    }


def test_fixture_enrichment_preserves_canonical_names_and_adds_display_fields():
    fixture = enrich_fixture({"home_team": "France", "away_team": "Senegal"})

    assert fixture["home_team"] == "France"
    assert fixture["away_team"] == "Senegal"
    assert fixture["home_team_zh"] == "法国"
    assert fixture["away_team_zh"] == "塞内加尔"
    assert fixture["home_flag"] == "🇫🇷"
    assert fixture["away_flag"] == "🇸🇳"
