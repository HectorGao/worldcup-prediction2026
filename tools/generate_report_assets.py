#!/usr/bin/env python3
"""Generate reproducible public final-report metrics and SVG assets from SQLite."""

from __future__ import annotations

import argparse
import html
import json
import math
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def percent(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def svg(title: str, lines: list[str], bars: list[tuple[str, float, str]] | None = None) -> str:
    bars = bars or []
    height = max(260, 105 + len(lines) * 28 + len(bars) * 36)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="{height}" viewBox="0 0 1200 {height}" role="img" aria-labelledby="title desc">',
        '<rect width="1200" height="100%" fill="#f8fafc"/>',
        '<rect x="28" y="24" width="1144" height="4" rx="2" fill="#35a5f2"/>',
        f'<title id="title">{html.escape(title)}</title>',
        '<desc id="desc">Generated from the canonical World Cup result database.</desc>',
        f'<text x="48" y="72" font-family="Arial, PingFang SC, sans-serif" font-size="30" font-weight="700" fill="#132238">{html.escape(title)}</text>',
    ]
    y = 112
    for line in lines:
        parts.append(f'<text x="48" y="{y}" font-family="Arial, PingFang SC, sans-serif" font-size="19" fill="#35465a">{html.escape(line)}</text>')
        y += 28
    for label, value, color in bars:
        bounded = min(1.0, max(0.0, value))
        parts.extend(
            [
                f'<text x="48" y="{y + 17}" font-family="Arial, PingFang SC, sans-serif" font-size="17" fill="#35465a">{html.escape(label)}</text>',
                f'<rect x="360" y="{y}" width="720" height="20" rx="10" fill="#dce5ed"/>',
                f'<rect x="360" y="{y}" width="{720 * bounded:.1f}" height="20" rx="10" fill="{color}"/>',
                f'<text x="1100" y="{y + 17}" font-family="Arial, PingFang SC, sans-serif" font-size="16" text-anchor="end" fill="#35465a">{bounded * 100:.1f}%</text>',
            ]
        )
        y += 36
    parts.append('</svg>')
    return "\n".join(parts) + "\n"


def collect_metrics(database: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        has_rebuild_state = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'canonical_result_rebuild_state'"
        ).fetchone()
        result_query = (
            """
            SELECT results.* FROM canonical_match_results AS results
            JOIN canonical_result_rebuild_state AS state ON state.id = 1 AND state.rebuild_id = results.rebuild_id
            ORDER BY results.match_key
            """
            if has_rebuild_state
            else "SELECT * FROM canonical_match_results ORDER BY match_key"
        )
        results = [dict(row) for row in connection.execute(result_query)]
        predictions = [dict(row) for row in connection.execute("SELECT fixture_id, payload_json, created_at FROM predictions")]
    finally:
        connection.close()

    result_by_key = {str(row["match_key"]): row for row in results}
    evaluations: list[dict[str, Any]] = []
    for row in predictions:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            continue
        evaluation = payload.get("evaluation") or {}
        key = evaluation.get("canonical_match_key")
        if not key or key not in result_by_key:
            continue
        evaluations.append({"fixture_id": row["fixture_id"], "evaluation": evaluation, "payload": payload, "result": result_by_key[key]})

    evaluated = len(evaluations)
    outcome_hits = sum(bool(item["evaluation"].get("outcome_hit")) for item in evaluations)
    exact_hits = sum(bool(item["evaluation"].get("exact_score_hit")) for item in evaluations)
    home_errors = [float(item["evaluation"].get("home_goal_error", 0)) for item in evaluations]
    away_errors = [float(item["evaluation"].get("away_goal_error", 0)) for item in evaluations]
    error_values = home_errors + away_errors
    total_goal_errors = [
        abs(
            int(item["evaluation"].get("predicted_home_score", 0))
            + int(item["evaluation"].get("predicted_away_score", 0))
            - int(item["evaluation"].get("actual_home_score", 0))
            - int(item["evaluation"].get("actual_away_score", 0))
        )
        for item in evaluations
    ]
    stage_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    timing_groups: Counter[str] = Counter()
    confidence_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    outcome_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    coverage_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    brier_scores: list[float] = []
    log_losses: list[float] = []
    for item in evaluations:
        result_home = int(item["result"].get("home_score_90") or 0)
        result_away = int(item["result"].get("away_score_90") or 0)
        actual = str(item["evaluation"].get("actual_outcome") or ("home" if result_home > result_away else "away" if result_home < result_away else "draw"))
        stage_groups[str(item["result"].get("stage") or "Unknown")].append(item)
        timing_groups[str(item["evaluation"].get("prediction_timing") or "unknown")] += 1
        outcome_groups[actual].append(item)
        stage = str(item["result"].get("stage") or "")
        coverage_groups["group_stage" if "group" in stage.lower() or "小组赛" in stage else "knockout"].append(item)
        probabilities = item["payload"].get("probabilities") or {}
        has_odds = bool(
            item["payload"].get("market_available")
            or item["payload"].get("lottery_market", {}).get("match_no")
            or item["payload"].get("odds_data_status", {}).get("market_available")
        )
        coverage_groups["with_odds" if has_odds else "without_odds"].append(item)
        if probabilities:
            confidence = max(float(probabilities.get(key, 0) or 0) for key in ("home", "draw", "away"))
            bucket = "High / 高" if confidence >= 0.60 else "Medium / 中" if confidence >= 0.45 else "Low / 低"
            confidence_groups[bucket].append(item)
            brier_scores.append(
                sum((float(probabilities.get(key, 0) or 0) - float(key == actual)) ** 2 for key in ("home", "draw", "away")) / 3
            )
            log_losses.append(-math.log(max(1e-12, float(probabilities.get(actual, 0) or 0))))

    stage_metrics = {
        stage: {
            "matches": len(items),
            "outcome_hit_rate": percent(sum(bool(item["evaluation"].get("outcome_hit")) for item in items), len(items)),
            "exact_score_hit_rate": percent(sum(bool(item["evaluation"].get("exact_score_hit")) for item in items), len(items)),
        }
        for stage, items in sorted(stage_groups.items())
    }
    confidence_metrics = {
        bucket: {"matches": len(items), "outcome_hit_rate": percent(sum(bool(item["evaluation"].get("outcome_hit")) for item in items), len(items))}
        for bucket, items in sorted(confidence_groups.items())
    }
    outcome_metrics = {
        outcome: {"matches": len(items), "outcome_hit_rate": percent(sum(bool(item["evaluation"].get("outcome_hit")) for item in items), len(items))}
        for outcome, items in sorted(outcome_groups.items())
    }
    group_vs_knockout = {
        name: {"matches": len(items), "outcome_hit_rate": percent(sum(bool(item["evaluation"].get("outcome_hit")) for item in items), len(items))}
        for name, items in coverage_groups.items()
        if name in {"group_stage", "knockout"}
    }
    odds_coverage = {
        name: {"matches": len(items), "outcome_hit_rate": percent(sum(bool(item["evaluation"].get("outcome_hit")) for item in items), len(items))}
        for name, items in coverage_groups.items()
        if name in {"with_odds", "without_odds"}
    }
    ordered_stages = sorted(stage_metrics.items(), key=lambda item: (item[1]["outcome_hit_rate"], item[1]["matches"], item[0]))
    exact_cases = [item for item in evaluations if item["evaluation"].get("exact_score_hit")]
    partial_cases = [item for item in evaluations if item["evaluation"].get("outcome_hit") and not item["evaluation"].get("exact_score_hit")]
    high_confidence_misses = [
        item
        for item in evaluations
        if max((item["payload"].get("probabilities") or {}).values(), default=0) >= 0.60 and not item["evaluation"].get("outcome_hit")
    ]
    case_groups = {
        "exact_hit": exact_cases[:1],
        "outcome_only": partial_cases[:1],
        "high_confidence_miss": high_confidence_misses[:1],
    }
    metrics = {
        "canonical_matches": len(results),
        "evaluated_predictions": evaluated,
        "unmatched_prediction_records": len(predictions) - evaluated,
        "outcome_hits": outcome_hits,
        "exact_score_hits": exact_hits,
        "outcome_hit_rate": percent(outcome_hits, evaluated),
        "exact_score_hit_rate": percent(exact_hits, evaluated),
        "score_mae": sum(error_values) / len(error_values) if error_values else 0.0,
        "score_rmse": math.sqrt(sum(value * value for value in error_values) / len(error_values)) if error_values else 0.0,
        "home_goal_mae": sum(home_errors) / len(home_errors) if home_errors else 0.0,
        "away_goal_mae": sum(away_errors) / len(away_errors) if away_errors else 0.0,
        "total_goal_mae": sum(total_goal_errors) / len(total_goal_errors) if total_goal_errors else 0.0,
        "brier_score": sum(brier_scores) / len(brier_scores) if brier_scores else None,
        "log_loss": sum(log_losses) / len(log_losses) if log_losses else None,
        "timing_groups": dict(sorted(timing_groups.items())),
        "stage_metrics": stage_metrics,
        "confidence_metrics": confidence_metrics,
        "outcome_metrics": outcome_metrics,
        "group_vs_knockout": group_vs_knockout,
        "odds_coverage": odds_coverage,
        "best_stage": ordered_stages[-1][0] if ordered_stages else None,
        "hardest_stage": ordered_stages[0][0] if ordered_stages else None,
        "case_groups": {
            name: [
                {
                    "result_display": item["result"].get("result_display"),
                    "predicted_score": item["evaluation"].get("predicted_score"),
                    "actual_score_90": item["evaluation"].get("actual_score_90"),
                }
                for item in items
            ]
            for name, items in case_groups.items()
        },
        "evaluation_basis": "90-minute score only; extra time and penalties are presentation details.",
        "timing_note": "Only predictions timestamped on or before the fixture date are eligible for pre-match analysis; later records are reported separately as reconstructed audits.",
    }
    return metrics, evaluations


def generate_assets(database: Path, output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    metrics, evaluations = collect_metrics(database)
    output.joinpath("final_metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    overview = [
        f"Canonical matches / 归一唯一比赛：{metrics['canonical_matches']}",
        f"Evaluated saved predictions / 已评估保存预测：{metrics['evaluated_predictions']}",
        "Outcome and exact-score hits use the 90-minute result / 命中按90分钟赛果计算",
        f"Best stage / 最佳阶段：{metrics['best_stage'] or '--'} · Hardest / 最难阶段：{metrics['hardest_stage'] or '--'}",
    ]
    assets = {
        "overview.svg": svg("2026 World Cup Final Overview / 最终总览", overview, [("Outcome hit / 胜平负命中", metrics["outcome_hit_rate"], "#2f8f5b"), ("Exact score / 比分命中", metrics["exact_score_hit_rate"], "#c77c15")]),
        "hit-distribution.svg": svg("Hit Distribution / 命中分布", [f"Outcome hits / 胜平负命中：{metrics['outcome_hits']}", f"Exact-score hits / 比分命中：{metrics['exact_score_hits']}", f"Partial / 赛果命中但比分未中：{metrics['outcome_hits'] - metrics['exact_score_hits']}", f"Miss / 胜平负未中：{metrics['evaluated_predictions'] - metrics['outcome_hits']}"], [("Outcome / 胜平负", metrics["outcome_hit_rate"], "#2f8f5b"), ("Exact score / 比分", metrics["exact_score_hit_rate"], "#c77c15")]),
        "stage-performance.svg": svg("Stage Performance / 阶段表现", [f"{stage}: {data['matches']} matches / 场" for stage, data in metrics["stage_metrics"].items()], [(stage, data["outcome_hit_rate"], "#3b78d8") for stage, data in metrics["stage_metrics"].items()]),
        "predicted-vs-actual.svg": svg("Predicted vs Actual / 预测与实际", [f"{item['result'].get('result_display') or item['result'].get('match_key')}: {item['evaluation'].get('predicted_score', '--')} → {item['evaluation'].get('actual_score_90', '--')}" for item in evaluations[:6]] or ["No evaluated cases / 暂无评估案例"]),
        "score-errors.svg": svg("Score Errors / 比分误差", [f"Home MAE / 主队进球 MAE：{metrics['home_goal_mae']:.3f}", f"Away MAE / 客队进球 MAE：{metrics['away_goal_mae']:.3f}", f"Total-goal MAE / 总进球 MAE：{metrics['total_goal_mae']:.3f}", f"RMSE / 均方根误差：{metrics['score_rmse']:.3f}"], [("Outcome hit / 胜平负命中", metrics["outcome_hit_rate"], "#2f8f5b")]),
        "calibration.svg": svg("Probability Calibration / 概率校准", [f"Brier score / 布里尔分数：{metrics['brier_score']:.4f}" if metrics["brier_score"] is not None else "Brier score unavailable / 未提供概率记录", f"Log loss / 对数损失：{metrics['log_loss']:.4f}" if metrics["log_loss"] is not None else "Log loss unavailable / 未提供概率记录", *[f"{bucket}: {data['matches']} records / 记录" for bucket, data in metrics["confidence_metrics"].items()]], [(bucket, data["outcome_hit_rate"], "#35a5f2") for bucket, data in metrics["confidence_metrics"].items()]),
        "case-cards.svg": svg("Evaluation Cases / 评估样例", [f"{label}: {items[0].get('result_display') or '--'} · {items[0].get('predicted_score', '--')} → {items[0].get('actual_score_90', '--')}" for label, items in metrics["case_groups"].items() if items] or ["No evaluated cases / 暂无评估案例"]),
        "match-detail-flow.svg": svg("Match Card → Detail / 比赛卡片 → 单场详情", ["Schedule overview card / 赛程总览卡片", "Click detail / 点击查看详情", "Forecast, 90-minute result, extension/penalties, odds context, inputs, and evaluation / 预测、赛果、赔率、特征与命中"]),
        "technical-route.svg": svg("Reproducible Technical Route / 可复现技术路线", ["Raw result sources → canonical 104-match result table", "保存的预测记录 → 90-minute evaluation → metrics JSON → README SVG assets", "原始赛果来源 → 104场归一表 → 命中统计与公开报告"]),
    }
    for name, content in assets.items():
        output.joinpath(name).write_text(content, encoding="utf-8")
    output.joinpath("final_report.en.md").write_text(
        "# Final evaluation\n\n"
        "All outcome and score metrics use the 90-minute result. Extra time and penalties remain available as display details.\n\n"
        f"- Population / evaluated / unmatched predictions: {metrics['canonical_matches']} / {metrics['evaluated_predictions']} / {metrics['unmatched_prediction_records']}\n"
        f"- Outcome hits / rate: {metrics['outcome_hits']} / {metrics['outcome_hit_rate']:.1%}; exact-score hits / rate: {metrics['exact_score_hits']} / {metrics['exact_score_hit_rate']:.1%}\n"
        f"- Home / away / total-goal MAE: {metrics['home_goal_mae']:.3f} / {metrics['away_goal_mae']:.3f} / {metrics['total_goal_mae']:.3f}; score RMSE: {metrics['score_rmse']:.3f}\n"
        f"- Probability calibration: Brier {metrics['brier_score']:.3f}; log loss {metrics['log_loss']:.3f}\n"
        f"- Best / hardest stage by outcome hit rate: {metrics['best_stage']} / {metrics['hardest_stage']}\n"
        f"- Group stage / knockout outcome rate: {metrics['group_vs_knockout'].get('group_stage', {}).get('outcome_hit_rate', 0):.1%} / {metrics['group_vs_knockout'].get('knockout', {}).get('outcome_hit_rate', 0):.1%}\n"
        f"- Odds-covered / no-odds outcome rate: {metrics['odds_coverage'].get('with_odds', {}).get('outcome_hit_rate', 0):.1%} / {metrics['odds_coverage'].get('without_odds', {}).get('outcome_hit_rate', 0):.1%}; this is a descriptive coverage split, not a causal claim.\n"
        f"- Prediction timing: {metrics['timing_groups'].get('pre_match', 0)} pre-match and {metrics['timing_groups'].get('post_match_or_unknown', 0)} reconstructed post-match-or-unknown records.\n\n"
        f"{metrics['timing_note']}\n",
        encoding="utf-8",
    )
    output.joinpath("final_report.zh-CN.md").write_text(
        "# 最终评估\n\n"
        "胜平负和比分指标均以 90 分钟赛果为准；加时和点球保留为展示信息。\n\n"
        f"- 样本 / 已评估 / 未匹配预测：{metrics['canonical_matches']} / {metrics['evaluated_predictions']} / {metrics['unmatched_prediction_records']}\n"
        f"- 胜平负命中数 / 命中率：{metrics['outcome_hits']} / {metrics['outcome_hit_rate']:.1%}；精确比分命中数 / 命中率：{metrics['exact_score_hits']} / {metrics['exact_score_hit_rate']:.1%}\n"
        f"- 主队 / 客队 / 总进球 MAE：{metrics['home_goal_mae']:.3f} / {metrics['away_goal_mae']:.3f} / {metrics['total_goal_mae']:.3f}；比分 RMSE：{metrics['score_rmse']:.3f}\n"
        f"- 概率校准：Brier {metrics['brier_score']:.3f}；对数损失 {metrics['log_loss']:.3f}\n"
        f"- 按胜平负命中率的最佳 / 最难阶段：{metrics['best_stage']} / {metrics['hardest_stage']}\n"
        f"- 小组赛 / 淘汰赛胜平负命中率：{metrics['group_vs_knockout'].get('group_stage', {}).get('outcome_hit_rate', 0):.1%} / {metrics['group_vs_knockout'].get('knockout', {}).get('outcome_hit_rate', 0):.1%}\n"
        f"- 有赔率覆盖 / 无赔率覆盖胜平负命中率：{metrics['odds_coverage'].get('with_odds', {}).get('outcome_hit_rate', 0):.1%} / {metrics['odds_coverage'].get('without_odds', {}).get('outcome_hit_rate', 0):.1%}；仅为覆盖分组描述，不作因果结论。\n"
        f"- 预测时间：赛前 {metrics['timing_groups'].get('pre_match', 0)} 条；赛后或未知时间的重建记录 {metrics['timing_groups'].get('post_match_or_unknown', 0)} 条。\n\n"
        "仅在预测时间戳不晚于比赛日时纳入赛前分析；之后生成的记录作为重建审计单列展示。\n",
        encoding="utf-8",
    )
    return metrics


def update_readme_summaries(metrics: dict[str, Any], root: Path) -> None:
    def coverage(name: str) -> str:
        item = metrics["odds_coverage"].get(name, {})
        return f"{item.get('matches', 0)} ({item.get('outcome_hit_rate', 0):.1%})"

    summaries = {
        root / "README.md": "\n".join(
            [
                "<!-- GENERATED_FINAL_METRICS_START -->",
                "This block is generated from the final SQLite snapshot; 90-minute scores are the evaluation basis.",
                f"- Population / evaluated / missing predictions: {metrics['canonical_matches']} / {metrics['evaluated_predictions']} / {metrics['unmatched_prediction_records']}",
                f"- Outcome / exact-score hits: {metrics['outcome_hits']} ({metrics['outcome_hit_rate']:.1%}) / {metrics['exact_score_hits']} ({metrics['exact_score_hit_rate']:.1%})",
                f"- Home / away / total-goal MAE and score RMSE: {metrics['home_goal_mae']:.3f} / {metrics['away_goal_mae']:.3f} / {metrics['total_goal_mae']:.3f} / {metrics['score_rmse']:.3f}",
                f"- Brier / log loss: {metrics['brier_score']:.3f} / {metrics['log_loss']:.3f}; best / hardest stage: {metrics['best_stage']} / {metrics['hardest_stage']}",
                f"- Odds-covered / no-odds outcome rate: {coverage('with_odds')} / {coverage('without_odds')} (descriptive coverage split, not a causal claim).",
                f"- Timing: {metrics['timing_groups'].get('pre_match', 0)} pre-match records and {metrics['timing_groups'].get('post_match_or_unknown', 0)} reconstructed post-match-or-unknown records.",
                "<!-- GENERATED_FINAL_METRICS_END -->",
            ]
        ),
        root / "README.zh-CN.md": "\n".join(
            [
                "<!-- GENERATED_FINAL_METRICS_START -->",
                "以下区块由最终 SQLite 快照自动生成，评估口径为 90 分钟赛果。",
                f"- 总体 / 有效预测 / 缺失预测：{metrics['canonical_matches']} / {metrics['evaluated_predictions']} / {metrics['unmatched_prediction_records']}",
                f"- 胜平负 / 精确比分命中：{metrics['outcome_hits']}（{metrics['outcome_hit_rate']:.1%}）/ {metrics['exact_score_hits']}（{metrics['exact_score_hit_rate']:.1%}）",
                f"- 主队 / 客队 / 总进球 MAE 与比分 RMSE：{metrics['home_goal_mae']:.3f} / {metrics['away_goal_mae']:.3f} / {metrics['total_goal_mae']:.3f} / {metrics['score_rmse']:.3f}",
                f"- Brier / Log Loss：{metrics['brier_score']:.3f} / {metrics['log_loss']:.3f}；最佳 / 最难阶段：{metrics['best_stage']} / {metrics['hardest_stage']}",
                f"- 有赔率 / 无赔率的胜平负命中率：{coverage('with_odds')} / {coverage('without_odds')}（仅覆盖分组描述，不构成赔率因果贡献结论）。",
                f"- 时间口径：赛前记录 {metrics['timing_groups'].get('pre_match', 0)} 条；赛后或时间未知的重建审计 {metrics['timing_groups'].get('post_match_or_unknown', 0)} 条。",
                "<!-- GENERATED_FINAL_METRICS_END -->",
            ]
        ),
    }
    for path, summary in summaries.items():
        text = path.read_text(encoding="utf-8")
        start = "<!-- GENERATED_FINAL_METRICS_START -->"
        end = "<!-- GENERATED_FINAL_METRICS_END -->"
        if start not in text or end not in text:
            raise ValueError(f"README metrics markers missing from {path}")
        before, _, tail = text.partition(start)
        _, _, after = tail.partition(end)
        path.write_text(before + summary + after, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/worldcup.sqlite3"))
    parser.add_argument("--output", type=Path, default=Path("docs/assets"))
    args = parser.parse_args()
    metrics = generate_assets(args.database, args.output)
    update_readme_summaries(metrics, Path(__file__).resolve().parents[1])
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
