# -*- coding: utf-8 -*-
"""报告：带 `schema_version`、自带指标说明、可被看板/CI 直接消费。

## 为什么 schema 要版本化
请求级报告现在是"人看"，以后会变成"CI 读 + 看板读 + 回归对比读"。
一旦字段含义变更（例如 `citation_precision` 改名 `citation_validity`），
没有版本号的话，消费方只会看到"某个字段突然没了"，然后把缺失当 0。

## 为什么报告要自带指标说明
`metric_specs` 里每个指标都写清 unit 与 missing 含义。
这样 `null` 不会被误读成 0 —— 这是本仓库反复踩过的坑（"缺失 ≠ 零"）。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

SCHEMA_VERSION = "request-eval/1"

#: 打印顺序（只影响可读性；新增指标会自动落到"其他指标"一行）
_PRINT_ORDER: tuple[tuple[str, str, str], ...] = (
    ("answer", "应答", "rate"),
    ("refuse", "拒答", "rate"),
    ("deny", "越权", "rate"),
    ("context_recall", "context_recall", "raw"),
    ("context_precision", "context_precision", "raw"),
    ("ndcg@3", "nDCG@3", "raw"),
    ("budget_cut_rate", "budget_cut_rate", "raw"),
    ("citation_validity", "citation_validity", "raw"),
    ("uncited_paragraphs_per_answer", "未标注段落/答案", "raw"),
    ("p95_ms", "p95_ms", "ms"),
)


def build_report(*, suite: str, mode: str, cases: Sequence[Mapping[str, Any]],
                 rows: Sequence[Mapping[str, Any]], metrics: Mapping[str, Any],
                 specs: Mapping[str, Mapping[str, str]],
                 meta: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "suite": suite,
        "mode": mode,
        "n_cases": len(rows),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "meta": dict(meta or {}),
        # ★ 指标说明书放在指标前面：读的人先知道"null 是什么意思"
        "metric_specs": {k: dict(v) for k, v in specs.items()},
        "aggregate": dict(metrics),
        "rows": list(rows),
    }


def write_report(report: Mapping[str, Any], path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(dict(report), ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def format_summary(report: Mapping[str, Any]) -> str:
    """人看的摘要。**只读 aggregate**，所以新增指标不会让打印崩。"""
    agg = report.get("aggregate") or {}
    lines = [f"场景 {report.get('suite')}（{report.get('mode')}，n={report.get('n_cases')}）"]
    shown: set[str] = set()
    for key, label, fmt in _PRINT_ORDER:
        if key not in agg:
            continue
        shown.add(key)
        val = agg.get(key)
        if isinstance(val, dict):                      # 三类题：passed/total/rate/wilson
            if val.get("total"):
                lines.append(f"  {label}: {val['passed']}/{val['total']} = "
                             f"{val['rate']:.1%}  Wilson {val['wilson_95']}")
            continue
        if val is None:
            lines.append(f"  {label}: null（见 metric_specs 的 missing 说明）")
        elif fmt == "ms":
            lines.append(f"  {label}: {val}ms")
        else:
            lines.append(f"  {label}: {val}")

    rest = {k: v for k, v in agg.items()
            if k not in shown and k not in ("buckets", "failure_buckets", "notes", "n_cases")}
    if rest:
        # ★ 字典型指标（如 by_category）不进"一行式"打印：会撑爆终端；单独摘要一行
        scalars = {k: v for k, v in rest.items() if not isinstance(v, dict)}
        if scalars:
            lines.append("  其他指标：" + " ".join(f"{k}={v}" for k, v in scalars.items()))
        cats = rest.get("by_category") or {}
        if cats:
            lines.append("  分组：" + " ".join(
                f"{cat}(n={v.get('n')})" for cat, v in sorted(cats.items())))
    if agg.get("buckets"):
        lines.append(f"  归因：{agg['buckets']}")
    for note in (agg.get("notes") or []):
        lines.append(f"  · {note}")
    return "\n".join(lines)
