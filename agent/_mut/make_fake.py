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


def _refuse_leg(correct, n_scored, n_no_hit=0, n_missing=0, n_hard=0):
    """拒答侧的三层结构（signal / text_judge / process）。

    ★ 这三层是第六版加的。假数据生成器一开始没跟上，
      渲染器读 `process` 直接 KeyError —— 变异测试自己先崩了。
      教训和本项目一贯的一样：**先怀疑测试自己**，
      但这里确实是测试数据过期，不是渲染器的问题。

    n_hard：拿到 no_hit 却仍然作答的题数 —— 第六版起这是【主指标】，
      假数据必须能造出"有硬答"和"无硬答"两种形态，
      否则测不到判定分支。
    """
    n_hit = max(0, n_scored - n_no_hit - n_missing)
    return {
        "n_refuse_total": n_scored,
        "n_scored": n_scored,
        "n_error": 0,
        "correct": correct,
        "rate": correct / n_scored if n_scored else 0.0,
        "signal": {
            "n_with_signal": n_no_hit + n_hit,
            "n_no_hit": n_no_hit,
            "n_hit": n_hit,
            "n_missing": n_missing,
            "no_hit_rate": (n_no_hit / (n_no_hit + n_hit))
            if (n_no_hit + n_hit) else None,
        },
        "text_judge": {
            "n_refused": correct,
            "rate": correct / n_scored if n_scored else None,
            "n_undecidable": 0,
        },
        "process": {
            "n_saw_no_hit": n_no_hit,
            "n_saw_but_answered": n_hard,
            "hard_answer_rate": (n_hard / n_no_hit) if n_no_hit else None,
            "n_refused_without_signal": 0,
        },
    }


def _run(d_rate, a_rate, d_n=61, a_n=61, ans_n=44, calls=1.8, n_hard=0):
    return {
        "direct": {
            "refuse": _refuse_leg(round(d_rate * d_n), d_n),
            "answer": _leg(round(0.90 * ans_n), ans_n),
        },
        "agent": {
            "refuse": _refuse_leg(round(a_rate * a_n), a_n,
                                   n_no_hit=round(0.66 * a_n),
                                   n_missing=round(0.05 * a_n),
                                   n_hard=n_hard),
            "answer": _leg(round(0.88 * ans_n), ans_n, calls=calls),
        },
    }


def _with_per_q(data, canned=40, live=21, live_ref=18):
    """给假数据补上 per_question（直打拆解段的数据源）。

    ★ 反向验证时发现的覆盖缺口：
      没有 per_question 时，直打拆解那段整段消失，
      而变异测试照样全绿 —— 因为没有断言盯着它。
      这个函数就是给 M6 造数据的。
    """
    out = copy.deepcopy(data)
    for r in out["runs"]:
        rows = ([{"id": f"c{i}", "type": "refuse", "knowledge_hit": False,
                  "answer_refused": True, "answer_len": 21,
                  "answer_head": "知识库里没有能回答这个问题的资料，我不编。"}
                 for i in range(canned)]
                + [{"id": f"l{i}", "type": "refuse", "knowledge_hit": True,
                    "answer_refused": i < live_ref, "answer_len": 100,
                    "answer_head": "答案"}
                   for i in range(live)])
        out.setdefault("per_question", {})[str(r.get("run", 0))] = {
            "direct": rows, "agent": [],
        }
    return out


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

    # M3：真的硬答（工具说了 no_hit，模型仍作答）→ 必须报出来
    m3 = {"baseline": BASE, "runs": []}
    for hard in (6, 5, 4):
        m3["runs"].append(_run(0.639, 0.656, n_hard=hard))
    cases["M3_real_hard_answer"] = m3

    # M4：有 no_hit 信号但硬答数为 0 → 必须说"未发现硬答"
    m4 = {"baseline": BASE, "runs": []}
    for _ in range(3):
        m4["runs"].append(_run(0.639, 0.607, n_hard=0))
    cases["M4_no_hard_answer"] = m4

    # M5：单遍 + 有硬答 → 单遍不判定回归，但硬答数必须照实报
    m5 = {"baseline": BASE, "runs": [_run(0.639, 0.656, n_hard=6)]}
    cases["M5_single_run_with_hard"] = m5

    # M6：有 per_question → 直打侧必须拆出"服务硬短路 vs 真调模型"。
    #   这条是被反向验证逼出来的：把拆解段整段删掉，
    #   M1~M5 全绿 —— 说明没人盯它。合并数字 95%+ 看着漂亮，
    #   拆开后才发现 2/3 的拒答根本没调模型。
    m6 = {"baseline": BASE, "runs": [_run(0.639, 0.656, n_hard=4) for _ in range(3)]}
    cases["M6_direct_split"] = _with_per_q(m6)

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