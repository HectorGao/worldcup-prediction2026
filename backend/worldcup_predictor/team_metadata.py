from __future__ import annotations

from typing import Any


TEAM_METADATA: dict[str, dict[str, str]] = {
    "Argentina": {"zh": "阿根廷", "iso2": "AR", "flag": "🇦🇷"},
    "Algeria": {"zh": "阿尔及利亚", "iso2": "DZ", "flag": "🇩🇿"},
    "Australia": {"zh": "澳大利亚", "iso2": "AU", "flag": "🇦🇺"},
    "Austria": {"zh": "奥地利", "iso2": "AT", "flag": "🇦🇹"},
    "Belgium": {"zh": "比利时", "iso2": "BE", "flag": "🇧🇪"},
    "Bosnia and Herzegovina": {"zh": "波黑", "iso2": "BA", "flag": "🇧🇦"},
    "Brazil": {"zh": "巴西", "iso2": "BR", "flag": "🇧🇷"},
    "Canada": {"zh": "加拿大", "iso2": "CA", "flag": "🇨🇦"},
    "Cape Verde": {"zh": "佛得角", "iso2": "CV", "flag": "🇨🇻"},
    "Colombia": {"zh": "哥伦比亚", "iso2": "CO", "flag": "🇨🇴"},
    "Croatia": {"zh": "克罗地亚", "iso2": "HR", "flag": "🇭🇷"},
    "Curacao": {"zh": "库拉索", "iso2": "CW", "flag": "🇨🇼"},
    "Czech Republic": {"zh": "捷克", "iso2": "CZ", "flag": "🇨🇿"},
    "DR Congo": {"zh": "刚果民主共和国", "iso2": "CD", "flag": "🇨🇩"},
    "Ecuador": {"zh": "厄瓜多尔", "iso2": "EC", "flag": "🇪🇨"},
    "Egypt": {"zh": "埃及", "iso2": "EG", "flag": "🇪🇬"},
    "England": {"zh": "英格兰", "iso2": "GB-ENG", "flag": "🏴"},
    "France": {"zh": "法国", "iso2": "FR", "flag": "🇫🇷"},
    "Germany": {"zh": "德国", "iso2": "DE", "flag": "🇩🇪"},
    "Ghana": {"zh": "加纳", "iso2": "GH", "flag": "🇬🇭"},
    "Haiti": {"zh": "海地", "iso2": "HT", "flag": "🇭🇹"},
    "Iran": {"zh": "伊朗", "iso2": "IR", "flag": "🇮🇷"},
    "Iraq": {"zh": "伊拉克", "iso2": "IQ", "flag": "🇮🇶"},
    "Italy": {"zh": "意大利", "iso2": "IT", "flag": "🇮🇹"},
    "Ivory Coast": {"zh": "科特迪瓦", "iso2": "CI", "flag": "🇨🇮"},
    "Japan": {"zh": "日本", "iso2": "JP", "flag": "🇯🇵"},
    "Jordan": {"zh": "约旦", "iso2": "JO", "flag": "🇯🇴"},
    "Mexico": {"zh": "墨西哥", "iso2": "MX", "flag": "🇲🇽"},
    "Morocco": {"zh": "摩洛哥", "iso2": "MA", "flag": "🇲🇦"},
    "Netherlands": {"zh": "荷兰", "iso2": "NL", "flag": "🇳🇱"},
    "New Zealand": {"zh": "新西兰", "iso2": "NZ", "flag": "🇳🇿"},
    "Norway": {"zh": "挪威", "iso2": "NO", "flag": "🇳🇴"},
    "Panama": {"zh": "巴拿马", "iso2": "PA", "flag": "🇵🇦"},
    "Paraguay": {"zh": "巴拉圭", "iso2": "PY", "flag": "🇵🇾"},
    "Portugal": {"zh": "葡萄牙", "iso2": "PT", "flag": "🇵🇹"},
    "Qatar": {"zh": "卡塔尔", "iso2": "QA", "flag": "🇶🇦"},
    "Saudi Arabia": {"zh": "沙特阿拉伯", "iso2": "SA", "flag": "🇸🇦"},
    "Scotland": {"zh": "苏格兰", "iso2": "GB-SCT", "flag": "🏴"},
    "Senegal": {"zh": "塞内加尔", "iso2": "SN", "flag": "🇸🇳"},
    "South Africa": {"zh": "南非", "iso2": "ZA", "flag": "🇿🇦"},
    "South Korea": {"zh": "韩国", "iso2": "KR", "flag": "🇰🇷"},
    "Spain": {"zh": "西班牙", "iso2": "ES", "flag": "🇪🇸"},
    "Sweden": {"zh": "瑞典", "iso2": "SE", "flag": "🇸🇪"},
    "Switzerland": {"zh": "瑞士", "iso2": "CH", "flag": "🇨🇭"},
    "Tunisia": {"zh": "突尼斯", "iso2": "TN", "flag": "🇹🇳"},
    "Turkey": {"zh": "土耳其", "iso2": "TR", "flag": "🇹🇷"},
    "United States": {"zh": "美国", "iso2": "US", "flag": "🇺🇸"},
    "Uruguay": {"zh": "乌拉圭", "iso2": "UY", "flag": "🇺🇾"},
    "Uzbekistan": {"zh": "乌兹别克斯坦", "iso2": "UZ", "flag": "🇺🇿"},
}


def display_team(team: str) -> dict[str, str]:
    metadata = TEAM_METADATA.get(team, {})
    return {
        "name": team,
        "zh": metadata.get("zh", team),
        "flag": metadata.get("flag", "🏳️"),
        "iso2": metadata.get("iso2", ""),
    }


def enrich_fixture(fixture: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(fixture)
    home = display_team(str(fixture.get("home_team", "")))
    away = display_team(str(fixture.get("away_team", "")))
    enriched.update(
        {
            "home_team_zh": home["zh"],
            "away_team_zh": away["zh"],
            "home_flag": home["flag"],
            "away_flag": away["flag"],
            "home_iso2": home["iso2"],
            "away_iso2": away["iso2"],
        }
    )
    return enriched


def enrich_profile(profile: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(profile)
    team = display_team(str(profile.get("team", "")))
    enriched["team_zh"] = team["zh"]
    enriched["flag"] = team["flag"]
    enriched["iso2"] = team["iso2"]
    return enriched
