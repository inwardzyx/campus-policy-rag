# -*- coding: utf-8 -*-
"""压测与成本统计用的小工具（纯函数，好测）。

## 为什么单独一个模块
`scripts/load_test.py` 要算 p50/p95、按并发档汇总；这些公式如果写在脚本里，
就只能靠"跑一遍看着像"来验收。抽出来之后可以用固定输入断言，
并且能配一条变异体证明"改坏了会红"。
"""
from __future__ import annotations

import math
from typing import Iterable, Sequence


def percentile(values: Sequence[float], p: float) -> float | None:
    """最近秩法百分位：p=0.95 表示"至少 95% 的值 ≤ 它"。

    ★ 空输入返回 None（缺失 ≠ 0）—— 压测没跑出数据时，报告里必须是"没有值"，
      不能显示 0ms 让人以为"快得离谱"。
    """
    data = sorted(float(v) for v in values)
    if not data:
        return None
    if not 0 < p <= 1:
        raise ValueError(f"p 必须在 (0, 1] 之间：{p}")
    idx = min(len(data) - 1, max(0, math.ceil(p * len(data)) - 1))
    return data[idx]


def summarize_latencies(values: Iterable[float]) -> dict:
    data = [float(v) for v in values]
    if not data:
        return {"n": 0, "p50_ms": None, "p95_ms": None, "max_ms": None, "mean_ms": None}
    return {
        "n": len(data),
        "p50_ms": round(percentile(data, 0.50), 1),
        "p95_ms": round(percentile(data, 0.95), 1),
        "max_ms": round(max(data), 1),
        "mean_ms": round(sum(data) / len(data), 1),
    }


def throughput(n_ok: int, seconds: float) -> float | None:
    return round(n_ok / seconds, 3) if seconds > 0 else None
