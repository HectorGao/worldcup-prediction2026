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
    stage_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    timing_groups: Counter[str] = Counter()
    confidence_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    brier_scores: list[float] = []
    for item in evaluations:
        stage_groups[str(item["result"].get("stage") or "Unknown")].append(item)
        timing_groups[str(item["evaluation"].get("prediction_timing") or "unknown")] += 1
        probabilities = item["payload"].get("probabilities") or {}
        if probabilities:
            confidence = max(float(probabilities.get(key, 0) or 0) for key in ("home", "draw", "away"))
            bucket = "High / 高" if confidence >= 0.60 else "Medium / 中" if confidence >= 0.45 else "Low / 低"
            confidence_groups[bucket].append(item)
            actual = str(item["evaluation"].get("actual_outcome") or "")
            brier_scores.append(
                sum((float(probabilities.get(key, 0) or 0) - float(key == actual)) ** 2 for key in ("home", "draw", "away")) / 3
            )

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
        "brier_score": sum(brier_scores) / len(brier_scores) if brier_scores else None,
        "timing_groups": dict(sorted(timing_groups.items())),
        "stage_metrics": stage_metrics,
        "confidence_metrics": confidence_metrics,
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
    ]
    assets = {
        "overview.svg": svg("2026 World Cup Final Overview / 最终总览", overview, [("Outcome hit / 胜平负命中", metrics["outcome_hit_rate"], "#2f8f5b"), ("Exact score / 比分命中", metrics["exact_score_hit_rate"], "#c77c15")]),
        "hit-distribution.svg": svg("Hit Distribution / 命中分布", [f"Outcome hits / 胜平负命中：{metrics['outcome_hits']}", f"Exact-score hits / 比分命中：{metrics['exact_score_hits']}"], [("Outcome / 胜平负", metrics["outcome_hit_rate"], "#2f8f5b"), ("Exact score / 比分", metrics["exact_score_hit_rate"], "#c77c15")]),
        "stage-performance.svg": svg("Stage Performance / 阶段表现", [f"{stage}: {data['matches']} matches / 场" for stage, data in metrics["stage_metrics"].items()], [(stage, data["outcome_hit_rate"], "#3b78d8") for stage, data in metrics["stage_metrics"].items()]),
        "predicted-vs-actual.svg": svg("Predicted vs Actual / 预测与实际", [f"Saved evaluated records / 已评估记录：{metrics['evaluated_predictions']}", "The underlying match result is the canonical 90-minute score / 基准为归一的90分钟比分"], [("Exact-score agreement / 比分一致", metrics["exact_score_hit_rate"], "#c77c15")]),
        "score-errors.svg": svg("Score Errors / 比分误差", [f"MAE / 平均绝对误差：{metrics['score_mae']:.3f}", f"RMSE / 均方根误差：{metrics['score_rmse']:.3f}"], [("Outcome hit / 胜平负命中", metrics["outcome_hit_rate"], "#2f8f5b")]),
        "calibration.svg": svg("Probability Calibration / 概率校准", [f"Brier score / 布里尔分数：{metrics['brier_score']:.4f}" if metrics["brier_score"] is not None else "Brier score unavailable / 未提供概率记录", *[f"{bucket}: {data['matches']} records / 记录" for bucket, data in metrics["confidence_metrics"].items()]], [(bucket, data["outcome_hit_rate"], "#35a5f2") for bucket, data in metrics["confidence_metrics"].items()]),
        "case-cards.svg": svg("Evaluation Cases / 评估样例", [f"{item['result'].get('result_display') or item['result'].get('match_key')}: {item['evaluation'].get('predicted_score', '--')} → {item['evaluation'].get('actual_score_90', '--')}" for item in evaluations[:5]] or ["No evaluated cases / 暂无评估案例"]),
        "technical-route.svg": svg("Reproducible Technical Route / 可复现技术路线", ["Raw result sources → canonical 104-match result table", "保存的预测记录 → 90-minute evaluation → metrics JSON → README SVG assets", "原始赛果来源 → 104场归一表 → 命中统计与公开报告"]),
    }
    for name, content in assets.items():
        output.joinpath(name).write_text(content, encoding="utf-8")
    output.joinpath("final_report.en.md").write_text(
        "# Final evaluation\n\n"
        f"- Canonical matches: {metrics['canonical_matches']}\n"
        f"- Evaluated saved predictions: {metrics['evaluated_predictions']}\n"
        f"- Outcome hit rate: {metrics['outcome_hit_rate']:.1%}\n"
        f"- Exact-score hit rate: {metrics['exact_score_hit_rate']:.1%}\n"
        f"- Score MAE / RMSE: {metrics['score_mae']:.3f} / {metrics['score_rmse']:.3f}\n\n"
        f"{metrics['timing_note']}\n",
        encoding="utf-8",
    )
    output.joinpath("final_report.zh-CN.md").write_text(
        "# 最终评估\n\n"
        f"- 归一唯一比赛：{metrics['canonical_matches']} 场\n"
        f"- 已评估保存预测：{metrics['evaluated_predictions']} 条\n"
        f"- 胜平负命中率：{metrics['outcome_hit_rate']:.1%}\n"
        f"- 比分命中率：{metrics['exact_score_hit_rate']:.1%}\n"
        f"- 比分 MAE / RMSE：{metrics['score_mae']:.3f} / {metrics['score_rmse']:.3f}\n\n"
        "仅在预测时间戳不晚于比赛日时纳入赛前分析；之后生成的记录作为重建审计单列展示。\n",
        encoding="utf-8",
    )
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/worldcup.sqlite3"))
    parser.add_argument("--output", type=Path, default=Path("docs/assets"))
    args = parser.parse_args()
    metrics = generate_assets(args.database, args.output)
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
