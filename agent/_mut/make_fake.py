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


def _with_per_q(data, canned=40, live=21, live_ref=18, hard_ids=None):
    """给假数据补上 per_question（直打拆解段 + 逐遍硬答题号明细的数据源）。

    ★ 反向验证时发现的覆盖缺口：
      没有 per_question 时，直打拆解那段整段消失，
      而变异测试照样全绿 —— 因为没有断言盯着它。
      这个函数就是给 M6 造数据的。

    ★★ 2026-10-13 再补一处：原来 `"agent": []`（空列表）——
      于是 2026-10-13 新加的「逐遍硬答题号明细」那一节，
      在【全部 6 个用例里都是"未算出"分支】，变异测试压根没覆盖它。
      这是第二遍出现的同一个病："新增的段没有数据源，测试照样全绿"。
      ⇒ 现在按 hard_ids 造出与真数据同形的 agent 侧逐题记录。
        hard_ids 给的是"这一遍里硬答的题号列表"，None 表示按位置造前 N 个。
    """
    out = copy.deepcopy(data)
    for idx, r in enumerate(out["runs"]):
        rows = ([{"id": f"c{i}", "type": "refuse", "knowledge_hit": False,
                  "answer_refused": True, "answer_len": 21,
                  "answer_head": "知识库里没有能回答这个问题的资料，我不编。"}
                 for i in range(canned)]
                + [{"id": f"l{i}", "type": "refuse", "knowledge_hit": True,
                    "answer_refused": i < live_ref, "answer_len": 100,
                    "answer_head": "答案"}
                   for i in range(live)])
        # ★ agent 侧逐题：前 n_no_hit 题都是 saw_no_hit=True，
        #   其中 hard_ids 里的那些 answer_refused=False（即硬答）。
        #   形状必须与真 JSON 一致，否则渲染器走不到那条分支。
        a_no_hit = round(0.66 * 61)
        hard_set = set(hard_ids or [])
        agent_rows = []
        for i in range(a_no_hit):
            qid = f"q{i}"
            is_hard = qid in hard_set
            agent_rows.append({
                "id": qid, "type": "refuse",
                "signal": "no_hit", "saw_no_hit": True,
                "n_signal_missing": 0,
                "answer_refused": (not is_hard),
                "rounds": 2, "n_tool_calls": 1,
                "answer_len": 100,
                "answer_head": "答案",
                "error": None,
            })
        out.setdefault("per_question", {})[str(r.get("run", idx + 1))] = {
            "direct": rows, "agent": agent_rows,
        }
    return out


def _with_per_q_multi(data, hard_ids_per_run):
    """逐遍给不同的硬答题号（hard_ids_per_run 与 runs 一一对应）。

    ★ 为什么不能用"每遍各调一次 _with_per_q 再合并"：
      单遍调用时 enumerate 从 0 起、键恒为 "run1"，三遍会互相覆盖。
      ⇒ 必须在【同一个循环】里造，键才跟渲染器的 enumerate(runs, 1) 对得上。
    """
    out = copy.deepcopy(data)
    pq = out.setdefault("per_question", {})
    for idx, r in enumerate(out["runs"]):
        ids = set(hard_ids_per_run[idx] if idx < len(hard_ids_per_run) else [])
        rid = str(r.get("run", idx + 1))
        # 直打侧沿用单遍版的默认形状（够 M6 那一节用）
        rows = ([{"id": f"c{i}", "type": "refuse", "knowledge_hit": False,
                  "answer_refused": True, "answer_len": 21,
                  "answer_head": "知识库里没有能回答这个问题的资料，我不编。"}
                 for i in range(40)]
                + [{"id": f"l{i}", "type": "refuse", "knowledge_hit": True,
                    "answer_refused": i < 18, "answer_len": 100,
                    "answer_head": "答案"}
                   for i in range(21)])
        agent_rows = [{
            "id": f"q{i}", "type": "refuse",
            "signal": "no_hit", "saw_no_hit": True, "n_signal_missing": 0,
            "answer_refused": (f"q{i}" not in ids),
            "rounds": 2, "n_tool_calls": 1, "answer_len": 100,
            "answer_head": "答案", "error": None,
        } for i in range(40)]
        pq[rid] = {"direct": rows, "agent": agent_rows}
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
    #   ★ 题号刻意造成"三遍只重合 1 题" —— 与真数据同形
    #   （实测：并集 11 题、每遍都硬答只有 r34、只出现一遍的有 8 题）。
    #   为什么要同形：这一节的全部价值就在于"别把逐遍数字读成收敛"，
    #   如果假数据里三遍题号完全一致，那节会渲染成"稳定存在"，
    #   断言就永远测不到"该报警报了没有"。
    m3 = {"baseline": BASE, "runs": []}
    _m3_ids = [["q0", "q10", "q20", "q30", "q31", "q32"],
               ["q0", "q10", "q33", "q34", "q35"],
               ["q0", "q20", "q21", "q22"]]
    for hard in (6, 5, 4):
        m3["runs"].append(_run(0.639, 0.656, n_hard=hard))
    # ★ 三遍的题号要【各自】给，不能只给第 1 遍 ——
    #   "跨遍重合 1 题"这个结论只有三遍都有数据才算得出来，
    #   只给一遍的话另两遍是空集，交集算出来是空的，测的就不是同一件事。
    cases["M3_real_hard_answer"] = _with_per_q_multi(m3, _m3_ids)

    # M7：三遍的硬答题**两两完全不重叠**（交集为空）。
    #   ★ 刻意与 M3 用【不同的数据形状】——M3 是"交集 1 题"，
    #     M7 是"交集 0 题"。如果两者用同一份假数据，
    #     那么 M3 通过就已经证明了 M7 的判据能响，
    #     M7 再跑一遍就是"同源变异配同源判据"，不携带额外信息量。
    #     （与本项目"检出 ≠ 证明"那条同族。）
    m7 = {"baseline": BASE, "runs": []}
    for hard in (5, 5, 5):
        m7["runs"].append(_run(0.639, 0.656, n_hard=hard))
    cases["M7_stability"] = _with_per_q_multi(
        m7, [["q0", "q1", "q2", "q3", "q4"],
             ["q5", "q6", "q7", "q8", "q9"],
             ["q10", "q11", "q12", "q13", "q14"]])

    # M8（2026-10-13）：三遍题号【完全一致】。
    #   这是 M7 的反向 —— 检验"不能读成改善"这句话【不会】在
    #   真的稳定存在时也照说。写了就说明这条警告是常开的，
    #   而常开的警告等于没有警告。
    m8 = {"baseline": BASE, "runs": [_run(0.639, 0.656, n_hard=3) for _ in range(3)]}
    cases["M8_stable_across_runs"] = _with_per_q_multi(
        m8, [["q0", "q1", "q2"], ["q0", "q1", "q2"], ["q0", "q1", "q2"]])

    # M4：有 no_hit 信号但硬答数为 0 → 必须说"未发现硬答"
    m4 = {"baseline": BASE, "runs": []}
    for _ in range(3):
        m4["runs"].append(_run(0.639, 0.607, n_hard=0))
    cases["M4_no_hard_answer"] = _with_per_q(m4, hard_ids=[])

    # M5：单遍 + 有硬答 → 单遍不判定回归，但硬答数必须照实报
    m5 = {"baseline": BASE, "runs": [_run(0.639, 0.656, n_hard=6)]}
    cases["M5_single_run_with_hard"] = _with_per_q(
        m5, hard_ids=["q0", "q10", "q20", "q30", "q31", "q32"])

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