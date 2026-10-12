# -*- coding: utf-8 -*-
"""给 render_regression_report.py 做变异测试的假数据（不是实测结果！）。

用途：验证报告生成器在三种边界下**不会输出假结论**：
  M1 应答侧 n_scored=0（--only-refuse 场景）→ 不该出现 "0.0%" 的应答表
  M2 只跑 1 遍 → 不该假装有波动区间
  M3 agent 掉到基线以下 → 该报"发现回归"

跑法见文件末尾 __main__。
"""

from __future__ import annotations

import copy
import json
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = {
    "source": "evalset/report-rerank-heldout.md（在线层/rerank，产品默认）",
    "n_refuse": 61, "refuse_correct": 39,
    "n_answer": 44, "recall_at_5": 42, "miss_rate": 1,
}


def _leg(rate_numer, n_scored, rate_denom=None, calls=None):
    return {
        "n_scored": n_scored,
        "correct": rate_numer,
        "rate": rate_numer / n_scored if n_scored else 0.0,
        "n_error": 0,
        "mean_tool_calls": calls,
    }


def _run(d_rate, a_rate, d_n=61, a_n=61, ans_n=44, calls=1.8):
    return {
        "direct": {
            "refuse": _leg(round(d_rate * d_n), d_n),
            "answer": _leg(round(0.90 * ans_n), ans_n),
        },
        "agent": {
            "refuse": _leg(round(a_rate * a_n), a_n),
            "answer": _leg(round(0.88 * ans_n), ans_n, calls=calls),
        },
    }


def build():
    """返回 {case_name: 完整 JSON}。"""
    cases = {}

    # M1：只跑拒答（--only-refuse 的真实形状）
    m1 = {"baseline": BASE, "runs": []}
    for d, a in [(0.639, 0.656), (0.623, 0.639), (0.639, 0.639)]:
        r = _run(d, a)
        r["direct"]["answer"] = _leg(0, 0)
        r["agent"]["answer"] = _leg(0, 0)
        m1["runs"].append(r)
    cases["M1_only_refuse"] = m1

    # M2：只跑 1 遍（波动无从谈起）
    m2 = {"baseline": BASE, "runs": [_run(0.639, 0.656)]}
    cases["M2_single_run"] = m2

    # M3：agent 真的掉了（应报回归）
    m3 = {"baseline": BASE, "runs": []}
    for d, a in [(0.639, 0.279), (0.623, 0.295), (0.639, 0.279)]:
        m3["runs"].append(_run(d, a))
    cases["M3_real_regression"] = m3

    # M4：边界 —— agent 略低于基线但落在底噪内（不该报回归）
    m4 = {"baseline": BASE, "runs": []}
    for d, a in [(0.639, 0.607), (0.607, 0.623), (0.639, 0.607)]:
        m4["runs"].append(_run(d, a))
    cases["M4_within_noise"] = m4

    return cases


# ★ 文件名一律带 FAKE_ 前缀。
#   这些是【编造的假数据】，一旦被当成实测结果提交，
#   就会凭空冒出一个"61 题拒答率 60.7%"的结论。
#   前缀是最便宜的一道保险 —— 它让人在 ls 里就能分清。
_PREFIX = "FAKE_"


if __name__ == "__main__":
    out = os.path.join(_ROOT, "agent", "_mut")
    os.makedirs(out, exist_ok=True)
    for name, data in build().items():
        p = os.path.join(out, _PREFIX + name + ".json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print("wrote", os.path.relpath(p, _ROOT))