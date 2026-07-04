from pathlib import Path


def test_score_heatmap_includes_team_names_and_axis_labels():
    source = Path("src/main.js").read_text(encoding="utf-8")

    assert "function scoreHeatmap(heatmap, handicapAnalysis = {}, teams = {})" in source
    assert "score-match-title" in source
    assert "homeLabel" in source
    assert "awayLabel" in source
    assert "主队进球" in source
    assert "客队进球" in source
    assert "scoreHeatmap(prediction.score_heatmap || { matrix: prediction.score_matrix }, prediction.handicap_analysis, {" in source


def test_sporttery_refresh_summary_lists_unmatched_and_filtered_matches():
    source = Path("src/main.js").read_text(encoding="utf-8")

    assert "未匹配赔率比赛：" in source
    assert "被过滤赔率比赛：" in source
    assert "unmatched_matches" in source
    assert "filtered_matches" in source
    assert "match_results" in source


def test_line_strength_cards_use_world_cup_average_baseline():
    js = Path("src/main.js").read_text(encoding="utf-8")
    css = Path("src/styles.css").read_text(encoding="utf-8")

    assert "50 = 本届世界杯48队平均水平" in js
    assert "strength_baseline" in js
    assert "numeric >= 65 ? 'elite' : numeric >= 55 ? 'strong' : numeric >= 45 ? 'medium' : numeric >= 35 ? 'weak' : 'very-weak'" in js
    assert "strength-very-weak" in css
    assert "strength-baseline" in css
