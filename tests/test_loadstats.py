# -*- coding: utf-8 -*-
"""压测统计（`kb/loadstats.py`）的判据：分位数不准，容量结论就全错。

## 盯的三条
  ① 空输入返回 None（缺失≠0，不能显示 0ms）；
  ② 最近秩法：5 个数、p95 应取**第 5 个**（`ceil(0.95*5)=5`），不是第 4 个；
  ③ 吞吐 = 成功数 / 秒，0 秒返回 None（不除零、不编造）。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest                                   # noqa: E402

from kb.loadstats import percentile, summarize_latencies, throughput  # noqa: E402


def test_empty_is_missing_not_zero() -> None:
    assert percentile([], 0.95) is None
    assert summarize_latencies([])["p95_ms"] is None


def test_p95_uses_nearest_rank() -> None:
    values = [100, 200, 300, 400, 500]
    assert percentile(values, 0.95) == 500, "5 个数的 p95 应是第 5 个"
    assert percentile(values, 0.50) == 300
    assert percentile([42], 0.95) == 42


def test_bad_p_raises() -> None:
    with pytest.raises(ValueError):
        percentile([1, 2, 3], 1.5)


def test_summary_reports_all_fields() -> None:
    s = summarize_latencies([100, 200, 300, 400, 500])
    assert s["n"] == 5 and s["max_ms"] == 500.0 and s["mean_ms"] == 300.0


def test_throughput_handles_zero_seconds() -> None:
    assert throughput(3, 0) is None
    assert throughput(3, 2) == 1.5
