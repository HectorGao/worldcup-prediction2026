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


def test_ui_theme_title_author_and_display_only_constraints():
    html = Path("index.html").read_text(encoding="utf-8")
    css = Path("src/styles.css").read_text(encoding="utf-8")

    assert "<title>2026世界杯预测系统</title>" in html
    assert "2026世界杯<span>预测系统</span>" in html
    assert "作者 hectorgao" in html
    assert "15 分钟轮询" not in html
    assert "Dixon-Coles + Elo" not in html
    assert "阵容权重可调" not in html
    assert "--color-stone-canvas: #fafaf9" in css
    assert "--color-cyan-signal: #3ba6f1" in css
    assert "h1 span::after" in css
    assert ".match-row:has(.status-pill:not([data-status=\"final\"]))" in css
    assert "backdrop-filter: blur(14px)" in css


def test_heatmap_palette_is_preserved_while_probability_text_is_readable():
    css = Path("src/styles.css").read_text(encoding="utf-8")

    assert ".heat-level-1 {\n  background: #eef1ef;" in css
    assert ".heat-level-6 {\n  background: #c77c15;" in css
    assert ".region-home,\n.handicap-region-home em {\n  background: #cbeed8;" in css
    assert ".region-away,\n.handicap-region-away em {\n  background: #c9d9f6;" in css
    assert ".heat-cell:not(.heat-level-6) span" in css
    assert "color: var(--color-ink-black);" in css


def test_prediction_bars_use_distinct_semantic_colors():
    css = Path("src/styles.css").read_text(encoding="utf-8")

    assert ".home-fill {\n  background: linear-gradient(90deg, #2f8f5b, #3fb77a);" in css
    assert ".draw-fill {\n  background: linear-gradient(90deg, #8f6515, #d8aa3e);" in css
    assert ".away-fill {\n  background: linear-gradient(90deg, #23549a, #3b78d8);" in css
    assert ".mini-meter.strength-elite .bar-fill {\n  background: linear-gradient(90deg, #dfa64a, #c77c15);" in css
    assert ".mini-meter.strength-strong .bar-fill {\n  background: linear-gradient(90deg, #f4d28c, #dfa64a);" in css
    assert ".mini-meter.strength-medium .bar-fill {\n  background: linear-gradient(90deg, #dce9e2, #cbeed8);" in css
    assert ".mini-meter.strength-weak .bar-fill {\n  background: linear-gradient(90deg, rgba(201, 217, 246, 0.38), #c9d9f6);" in css
    assert ".mini-meter.strength-very-weak .bar-fill {\n  background: linear-gradient(90deg, rgba(201, 217, 246, 0.08), rgba(201, 217, 246, 0.52));" in css


def test_market_model_delta_bars_use_semantic_colors():
    css = Path("src/styles.css").read_text(encoding="utf-8")
    js = Path("src/main.js").read_text(encoding="utf-8")

    assert "title === 'Market' ? 'market-card'" in js
    assert 'model-market-row outcome-${escapeAttr(item.outcome)}' in js
    assert ".model-market-row.outcome-home .bar-track .bar-fill" in css
    assert "background: linear-gradient(90deg, #2f8f5b, #3fb77a);" in css
    assert ".model-market-row.outcome-draw .bar-track .bar-fill" in css
    assert "background: linear-gradient(90deg, #8f6515, #d8aa3e);" in css
    assert ".model-market-row.outcome-away .bar-track .bar-fill" in css
    assert "background: linear-gradient(90deg, #23549a, #3b78d8);" in css
    assert ".model-card.market-card .bar-fill" in css
    assert ".model-market-row .bar-track:nth-of-type(2) .bar-fill" in css
    assert "opacity: 0.42;" in css
    assert ".model-market-row strong.value" in css
    assert ".model-market-row strong.efficient" in css
