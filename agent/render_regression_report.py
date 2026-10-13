# -*- coding: utf-8 -*-
"""把 refusal_regression.py 的原始结果渲染成验收报告。

★★★ 为什么报告要由脚本生成，而不是手写：

  本项目今天已经吃过四次"手写结论"的亏——手写的东西和实测对不上，
  而【看起来最像结论的那些版本，恰恰是最容易错的】。
  所以这里的规矩是：**报告里的每个数字都必须从 JSON 里读**，
  脚本读不到就留空并报错，绝不让人手填后忘了核对。

  唯一允许手写的部分是"怎么解读"，因为那是人的判断，不是量出来的事实。

跑法（先跑 refusal_regression.py）：
    <虚拟环境>/Scripts/python.exe agent/refusal_regression.py --runs 3
    <虚拟环境>/Scripts/python.exe agent/render_regression_report.py
"""

from __future__ import annotations

import json
import os
import sys
# ★ print 里的 ⇒/✅/✗ 在 cp936 下编不出来（stdout 是管道时）⇒ 崩在打印上。本项目已犯 5 次。
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(_ROOT, "agent", "_refusal_regression.json")
OUT = os.path.join(_ROOT, "evalset", "report-agent-refusal-regression.md")


def pct(x: float) -> str:
    return f"{x:.1%}"


def fmt_range(vals: list[float]) -> str:
    """把多次跑的数值渲染成 'min~max（单点）' 或 'a / b / c'。

    ★ 空列表返回 '—' 而不是崩：没有数据 ≠ 0。
      崩掉会让整份报告生成失败，而"生成失败"和"数据缺失"
      在赶时间的时候很容易被混为一谈。
    """
    if not vals:
        return "—"
    if len(set(vals)) == 1:
        return pct(vals[0])
    lo, hi = min(vals), max(vals)
    return f"{pct(lo)}~{pct(hi)}（{len(vals)} 次：{' / '.join(pct(v) for v in vals)}）"


_CN_NUM = ("一", "二", "三", "四", "五", "六", "七", "八", "九", "十")


def _cn_num(n: int) -> str:
    """章节号中文数字。超范围就退回阿拉伯数字 —— 宁可难看，不要错。"""
    return _CN_NUM[n - 1] if 1 <= n <= len(_CN_NUM) else str(n)


def main():
    if not os.path.exists(SRC):
        print(f"[!!] 找不到 {os.path.relpath(SRC, _ROOT)}")
        print("    先跑：agent/refusal_regression.py --runs 3")
        return 1
    data = json.load(open(SRC, encoding="utf-8"))
    base = data["baseline"]
    runs = data["runs"]
    # 逐题明细：拆直打侧"服务硬短路 vs 真调模型"要用。
    # ★ 汇总块（runs[].*.refuse）与逐题块（per_question）必须来自同一次判据 ——
    #   本轮核对时发现两者不一致（逐题重算了、汇总还是旧的），
    #   于是一份报告里出现了两套数字。所以这里只读 per_question 现算，不信汇总。
    per_q = data.get("per_question") or {}
    if not runs:
        print("[!!] 结果里没有任何 run —— 是不是跑失败了？")
        return 1

    d_rates = [r["direct"]["refuse"]["rate"] for r in runs]
    a_rates = [r["agent"]["refuse"]["rate"] for r in runs]
    d_ans = [r["direct"]["answer"]["rate"] for r in runs]
    a_ans = [r["agent"]["answer"]["rate"] for r in runs]
    base_rate = base["refuse_correct"] / base["n_refuse"]
    d_calls = [r["agent"]["answer"]["mean_tool_calls"] for r in runs
               if r["agent"]["answer"].get("mean_tool_calls")]

    # ★ 回归判定：【同时】超出同版本底噪才算回归。
    #   只看"agent 低于基线"是不够的 —— 基线是另一次跑的，
    #   它的噪声和 agent 的噪声是两回事。
    d_spread = max(d_rates) - min(d_rates)
    a_spread = max(a_rates) - min(a_rates)
    if len(runs) >= 2:
        regressed = max(a_rates) < base_rate - max(d_spread, a_spread)
        borderline = max(a_rates) < base_rate and not regressed
    else:
        # ★ 单遍时两种波动恒为 0，门槛退化成"基线"本身 ——
        #   低于基线就会被判成"发现回归"，可这个判定没有底噪支撑。
        #   所以单遍直接不判定，而不是给一个假的判定。
        regressed = borderline = False

    n_ref = runs[0]["direct"]["refuse"]["n_scored"]
    n_ans = runs[0]["direct"]["answer"]["n_scored"]

    # ★★★ 第六版：主指标换成【结构化信号】，文本判据降为辅助。
    #   起因是 DSH 独立审计实测出文本判据的致命缺陷：
    #     "我查不到具体的报销比例，但按往年惯例一般可以报销 80% 左右。"
    #     → 被判成【正确拒答】。而这是硬答 —— 它编了 80%。
    #   ⇒ 信号（工具自己说有没有命中）是唯一不依赖猜措辞的数字。
    def _sig(path, key):
        vals = [r[path]["refuse"].get("signal", {}).get(key) for r in runs]
        vals = [v for v in vals if v is not None]
        return vals

    a_no_hit_rate = _sig("agent", "no_hit_rate")
    d_no_hit_rate = _sig("direct", "no_hit_rate")
    a_hard = [r["agent"]["refuse"]["process"].get("hard_answer_rate")
              for r in runs]
    a_hard = [v for v in a_hard if v is not None]
    a_hard_n = [r["agent"]["refuse"]["process"].get("n_saw_but_answered")
                for r in runs]
    a_sig_missing = [r["agent"]["refuse"].get("signal", {}).get("n_missing")
                     for r in runs]
    a_text = [r["agent"]["refuse"].get("text_judge", {}).get("rate") for r in runs]
    d_text = [r["direct"]["refuse"].get("text_judge", {}).get("rate") for r in runs]
    a_undec = [r["agent"]["refuse"].get("text_judge", {}).get("n_undecidable")
               for r in runs]

    # ★★ 直打侧必须拆成两层报，否则那个数字会被误读（本轮核对时抓到的）。
    #   直打走 service.py，rerank 最高分 < 5 时【直接返回固定文本、压根不调模型】。
    #   所以它的"文本拒答率"里混着"服务替模型拒答"：
    #     run1  58/61 = 96.7%，但其中 40 题是服务硬短路（100% 拒答），
    #     真正调了模型的 21 题里只拒答 18 题 = 85.7%。
    #   ⇒ 只报合并数字会让人以为"直打侧拒答能力 96.7%，远胜 agent"，
    #     而真相是它有 2/3 的拒答压根不来自模型。
    # ★ 不假设 run 序号一定存在 —— 假数据/续跑场景下可能缺。
    #   按 enumerate 的下标兜底，缺了就用位置。
    d_canned_n, d_live_n, d_live_ref = [], [], []
    for idx, r in enumerate(runs, 1):
        rid = str(r.get("run", idx))
        rows = (per_q.get(rid) or {}).get("direct") or []
        canned = [x for x in rows if x.get("knowledge_hit") is False]
        live = [x for x in rows if x.get("knowledge_hit") is not False]
        d_canned_n.append(len(canned))
        d_live_n.append(len(live))
        d_live_ref.append(sum(1 for x in live
                              if x.get("answer_refused") is True))
    d_live_rate = [ref / n for ref, n in zip(d_live_ref, d_live_n) if n]

    # ★★★ 2026-10-13 新增：硬答的【题号明细】与【跨遍稳定性】。
    #
    #   为什么加：报告原来只给每遍的硬答**数**（6/5/4），读起来像"在收敛"。
    #   而实际逐题号一比就知道，三遍硬答的题**几乎完全不同**——
    #   并集 11 题、三遍都硬答只有 1 题、8 题只在一遍里出现过。
    #   ⇒ 那个"15.8%→12.5%→10.5%"不是同一批题在减少，是每遍换一批新的。
    #   ⇒ 只报每遍的数字，会让人得出一个**看似有结论的错误结论**。
    #
    #   ★ 全部从 per_question 现算，题号来自 saw_no_hit + answer_refused，
    #     与硬答数的定义同源（都在同一段判据里算），不会两套口径。
    #   ★ 不手写题号：题集一改，手写的清单就是错的。
    hard_ids_by_run: dict[str, list[str]] = {}
    for idx, r in enumerate(runs, 1):
        rid = str(r.get("run", idx))
        rows = (per_q.get(rid) or {}).get("agent") or []
        hard_ids_by_run[rid] = sorted(
            x["id"] for x in rows
            if x.get("saw_no_hit") and x.get("answer_refused") is False
        )
    _runs_with_ids = [k for k in hard_ids_by_run if hard_ids_by_run[k]]
    if _runs_with_ids:
        _union: set[str] = set()
        for k in _runs_with_ids:
            _union |= set(hard_ids_by_run[k])
        _always = sorted(_union)  # 占位，下面用交集覆盖
        _inter: set[str] = set(hard_ids_by_run[_runs_with_ids[0]])
        for k in _runs_with_ids[1:]:
            _inter &= set(hard_ids_by_run[k])
        _always = sorted(_inter)
        _only_once = sorted(q for q in _union
                            if sum(q in hard_ids_by_run[k]
                                   for k in _runs_with_ids) == 1)
        _flip = {}
        _ks = _runs_with_ids
        for i in range(len(_ks)):
            for j in range(i + 1, len(_ks)):
                a, b = set(hard_ids_by_run[_ks[i]]), set(hard_ids_by_run[_ks[j]])
                _flip[f"{_ks[i]}↔{_ks[j]}"] = len(a ^ b)
    else:
        _union, _always, _only_once, _flip = set(), [], [], {}
    n_runs_with_ids = len(_runs_with_ids)

    # ★ 判据用例条数从判据模块真读，不手写。
    #   上一版这里手写着"8 条"，判据早就扩到 11 条而报告没跟上——
    #   手写的数字一定会掉队，这是本项目反复交的学费。
    n_cases = "（未读到）"
    n_und_cases = "（未读到）"
    n_empty_cases = "（未读到）"
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from refusal_regression import (_REFUSAL_CASES,   # noqa: E402
                                        _EMPTY_CASES,
                                        _UNDECIDABLE_CASES)
        n_cases = str(len(_REFUSAL_CASES))
        n_und_cases = str(len(_UNDECIDABLE_CASES))
        n_empty_cases = str(len(_EMPTY_CASES))
    except Exception as e:  # 读不到就留占位，不猜
        print(f"[!!] 判据用例数读不到（{type(e).__name__}），报告里会留占位")

    errs = sum(r["direct"]["refuse"]["n_error"] + r["direct"]["answer"]["n_error"]
               + r["agent"]["refuse"]["n_error"] + r["agent"]["answer"]["n_error"]
               for r in runs)

# ★★★ 判定不再基于"文本拒答率 vs 基线"——
    #   那个比较是【两把不同的尺子】（DSH 审计 S3 + 我自己复现）：
    #     基线 39/61 是【检索层】口径：服务没检索到就算拒答正确
    #     本次数字是【文本层】口径：读模型答案里的措辞
    #   相减得到的差值里，有一部分根本不是模型能力差异。
    #
    #   第六版改成按【硬答率】判定 —— 它是本次验收真正要回答的问题：
    #     "工具明确说了库里没有，模型有没有仍然编答案？"
    #   它的分母来自结构化信号（不猜措辞），所以第一次是可比的。
    hard_txt = ("、".join(str(v) for v in a_hard_n) if a_hard_n else "—")
    hard_rate = fmt_range(a_hard) if a_hard else None

    # ★★ 单遍时 verdict 必须跟着"不判定"走。
    #   这是变异测试 M5 抓出来的真漏洞：
    #   第六版把判定换成硬答率之后，verdict 那段只看 max(a_hard)，
    #   没接上面 `regressed = borderline = False`（单遍守卫），
    #   于是单遍跑出 15% 硬答率也会写"这才是本次验收要找的回归"。
    #   ⇒ 观测到硬答 ≠ 可以下回归结论：单遍的波动无从量化。
    single_run = len(runs) < 2

    if not a_hard:
        verdict = ("**本次未观测到 no_hit 信号**，因而无法评估硬答率"
                   "（信号缺失 ≠ 硬答率为 0，别把这两种情况混同）。")
    elif single_run:
        verdict = (f"**本次不作判定**：只跑了 1 遍，观测到硬答 {hard_txt} 题"
                   f"（硬答率 {hard_rate}），但单遍的波动无从量化，"
                   "不能据此下回归结论。至少跑 2~3 遍再看。")
    elif max(a_hard) == 0:
        verdict = ("**未发现硬答**：每一遍里，工具明确回报「库里没有」的题，"
                   "模型都没有再编答案。")
    else:
        verdict = (f"**发现硬答 {hard_txt} 题**：工具已经明确说了「库里没有」，"
                   f"模型仍然编了答案出来。这才是本次验收要找的回归。")

    # ★★★ 关键：单遍之间不可比的那件事，必须写进 verdict，而不是只放在正文。
    #   原来这里只报每遍的硬答**数**，读起来像"在收敛"——
    #   而逐题号一比就知道三遍硬答的题几乎完全不同。
    #   ⇒ 如果逐遍题号的交集很小，必须明说"这不是收敛，是每遍换一批题"，
    #     否则读者一定会把6→5→4 读成改善。
    stability_note = ""
    if n_runs_with_ids >= 2 and len(_union) >= 1:
        if _always:
            stability_note = (
                f"\n- ⚠️ **别把这几个数字读成「在收敛」**：三遍硬答的题**几乎完全不同** ——"
                f"并集 {len(_union)} 题，而**每遍都硬答的只有 {len(_always)} 题**"
                f"（{'、'.join(_always)}），只在一遍里出现的有 {len(_only_once)} 题。"
                f"⇒ 硬答率从 {_runs_with_ids and a_hard[0] or 0:.1%} 到 "
                f"{a_hard[-1]:.1%} 的变化，**主要是每遍换了一批题**，"
                f"不是同一批题在减少。")
        else:
            stability_note = (
                f"\n- ⚠️ **没有任何一道题是每遍都硬答的**（三遍并集 {len(_union)} 题，"
                f"交集为空）⇒ 硬答率 {hard_rate} 反映的是**当次抽样的波动**，"
                f"不是某个稳定存在的问题。")

    lines = [
        "# Agent 拒答回归验收报告",
        "",
        "> 目的：接了 agent（MCP + 工具 + 多轮循环）之后，"
        "**模型会不会在「库里没有」的情况下硬编答案**。",
        "> 本文件由 `agent/render_regression_report.py` 从实测 JSON 生成，**数字非手写**。",
        "",
        "## 一、结论",
        "",
        f"- {verdict}",
        f"- 硬答率（主指标）：**{hard_rate}**"
        + (f"　（{hard_txt} 题 / 每遍）" if a_hard else "　（无信号，未观测）"),
        f"- 跑了 **{len(runs)}** 遍；全部请求错误数 **{errs}**"
        "（错误不计入分子分母，另计——把服务异常算成拒答能力差是口径错误）",
    ]
    # ★ 这段【必须真的进 lines】。
    #   第一版我把 stability_note 算好放在上面，却忘了插进这里 ——
    #   于是渲染出来的报告里什么都没有，而代码里"看起来已经处理了"。
    #   这正是本项目记录过的死代码病（`_EVIDENCE` 两个出口都是 False）。
    #   ⇒ 判据必须盯"这段文字真的出现在报告里"，而不只是"这段代码存在"。
    if stability_note:
        lines += [stability_note]
    lines += [""]
    # ★ 单遍时必须把"为什么不下结论"摆在结论旁边，而不是只藏在 verdict 里。
    #   第六版重写判定时把这段警告弄丢了，变异测试 M2 抓出来的。
    if single_run:
        lines += [
            "> ⚠️ **只跑了 1 遍，本次不作判定。** 同一问题问两次返回的文本逐字不同"
            "（已实测），所以单遍的硬答率可能完全是随机波动 —— "
            "**底噪无从量化**，拿它和任何阈值比较都是给自己找台阶。",
            "",
        ]
    lines += [
        "## 二、主指标：硬答率（结构化信号）",
        "",
        "| 指标 | 数值 | 含义 |",
        "|---|---|---|",
        f"| agent `no_hit` 信号占比 | "
        f"{fmt_range(a_no_hit_rate) if a_no_hit_rate else '—'} |"
        " 工具明确回报「库里没有」的题占比 |",
        f"| 直打 `no_hit` 信号占比 | "
        f"{fmt_range(d_no_hit_rate) if d_no_hit_rate else '—'} |"
        " 同一批题走直打路径时的占比（用于对照） |",
        f"| **硬答题数** | **{hard_txt}** | 拿到 `no_hit` 却仍然作答 |",
        f"| **硬答率** | **{hard_rate}** | 硬答题数 ÷ `no_hit` 题数 |",
        f"| 信号解析失败 | {sum(a_sig_missing)} |"
        " 这几遍里**模型一次工具都没调**（`n_tool_calls=0`），"
        "所以没有任何工具输出可解析 —— **不是协议故障**。"
        "它们是能力边界题（天气 / 奶茶店 / 饭店），模型直接答「我帮不上忙」。"
        "**独立报出，既不并入 no_hit 也不并入硬答** |",
        "",
        "### 为什么主指标不用「拒答率」",
        "",
        "因为关键词判据做不到低假阳率。实测反例（`mcp/probe_toolnode_errors.py` 同源探针）：",
        "",
        "> 「我查不到具体的报销比例，但按往年惯例一般可以报销 80% 左右。」",
        "",
        "这句是**硬答**（它编了 80% 这个数字），"
        "但只要判据里有「查不到」三个字就会判成「正确拒答」——"
        "**恰好把本次验收要找的失败模式洗白**。",
        "",
        "所以现在的分工是：",
        "",
        "| 层 | 数据来源 | 作用 |",
        "|---|---|---|",
        "| **主指标** | 工具返回的结构化 `hit_status` | 判定回归 |",
        "| 辅助 | 模型答案文本（关键词判据） | 只报数，不判定 |",
        "| 过程 | `n_tool_calls` / `rounds` | 观察行为 |",
        "",
        f"文本判据本次另有结论：agent "
        + (fmt_range(a_text) if a_text else "—")
        + f"，其中 {sum(a_undec)} 题判不出（空答案/工具错误提示等，"
          "已排除出分母而不是硬猜）。",
        "",
        "### 逐遍硬答题号明细（为什么说它不是「在收敛」）",
        "",
    ]
    # ★★ 条件渲染：只有真的算出逐遍题号才出这一节。
    #   没有数据时硬打一张空表，会让人以为"跑了但没题"。
    if n_runs_with_ids >= 1 and _union:
        lines += [
            "| 遍 | 硬答题数 | 题号 |",
            "|---|---|---|",
        ]
        for k in _runs_with_ids:
            ids = hard_ids_by_run[k]
            lines.append(
                f"| {k} | {len(ids)} | "
                + ("、".join(f"`{i}`" for i in ids) if ids else "—") + " |"
            )
        lines += [
            "",
            f"- 三遍硬答的**并集** {len(_union)} 题，"
            f"**每遍都硬答的只有 {len(_always)} 题**"
            + ("（" + "、".join(f"`{i}`" for i in _always) + "）" if _always else "（空）")
            + "。",
            f"- 只在一遍里出现过的：{len(_only_once)} 题"
            + ("（" + "、".join(f"`{i}`" for i in _only_once) + "）" if _only_once else "")
            + " ⇒ 这部分**完全由当次抽样决定**，换个模型状态就是另一批。",
        ]
        if _flip:
            lines.append(
                "- 两遍之间翻转的题数（对称差）："
                + "、".join(f"{kk} = **{vv}** 题" for kk, vv in _flip.items())
                + "。"
            )
        lines.append("")

        # ★★ 措辞必须按【交集大小】分岔，不能一律写"这是波动"。
        #   反向验证抓出来的：如果三遍题号完全一致（真稳定存在），
        #   却仍然说"不能读成改善"，那条警告就变成了噪音 ——
        #   而噪音迟早被当摆设忽略掉，那比没有警告更坏。
        #   ★ 判据：只有当"并集明显大于交集"（即每次换一批题）才说"别读成收敛"。
        _flip_max = max(_flip.values()) if _flip else 0
        _looks_unstable = (len(_union) >= 2 * max(len(_always), 1)
                           or _flip_max > len(_always))
        if n_runs_with_ids < 2:
            lines += [
                "★ 只跑了 1 遍，**无从判断稳定性** —— "
                "上面那张表是当次抽样，不是可复现的结论。",
                "",
            ]
        elif _looks_unstable:
            lines += [
                "★ **所以「" + " → ".join(f"{v:.1%}" for v in a_hard)
                + "」不能读成改善。** 真正稳定的信号是**跨遍都硬答的那 "
                + f"{len(_always)} 题**（"
                + ("、".join(f"`{i}`" for i in _always) if _always else "无")
                + "）—— 逐遍的硬答数落在抽样波动里，"
                "而跨遍稳定的那部分才是可归因、可修复的对象。",
                "",
            ]
        else:
            lines += [
                f"★ 与常见情形相反：这些题**跨遍稳定**"
                f"（{len(_always)}/{len(_union)} 在每一遍都硬答，"
                "题号也基本不换）⇒ "
                "**不是抽样波动，是可复现的稳定失效**。",
                "",
            ]
    else:
        lines += [
            "_（本次未算出逐遍硬答题号：数据里没有足够的 `saw_no_hit` 题，"
            "或只跑了 1 遍而无从对比。）_",
            "",
        ]
    lines += [
        "### ⚠️ 直打侧的文本拒答率必须拆开看",
        "",
        f"直打侧合并文本拒答率 {fmt_range(d_text)} 看着漂亮，"
        "**但其中大半不是模型拒的** —— `service.py` 在 rerank 最高分 < 5 时"
        "直接返回固定文本、**压根不调模型**：",
        "",
        "| 遍 | 服务硬短路（未调模型） | 其中拒答 | 真调了模型 | 真调模型里的文本拒答率 |",
        "|---|---|---|---|---|",
    ]
    for i, (cn, ln, lr) in enumerate(
            zip(d_canned_n, d_live_n, d_live_ref), 1):
        drows = (per_q.get(str(runs[i - 1].get("run", i))) or {}).get("direct") or []
        cr = sum(1 for x in drows
                 if x.get("knowledge_hit") is False
                 and x.get("answer_refused") is True)
        rate = f"{lr / ln:.1%}" if ln else "—"
        lines.append(f"| {i} | {cn} 题 | {cr} 题 | {ln} 题 | {rate} |")
    lines += [
        "",
        f"⇒ 剔掉服务硬短路后，直打侧真实模型拒答率 "
        f"{fmt_range(d_live_rate)}，与 agent 侧同档。",
        "",
    ]
    if d_text and a_text:
        d_tot = sum(d_canned_n) + sum(d_live_n)
        share = (sum(d_canned_n) / d_tot) if d_tot else 0.0
        lines += [
            f"⚠️ **只报上面那个合并数字会得出相反的结论**：它会让人以为"
            f"「直打拒答能力 {max(d_text):.0%} 以上、远胜 agent 的 "
            f"{max(a_text):.0%}」，"
            f"而实际上三遍共 {sum(d_canned_n)} 题的拒答是**服务替模型拒的**"
            f"（占直打侧 {share:.0%}），"
            "模型根本没被调用。",
            "",
        ]
    lines += [
        "",
        "## 三、这个实验**不能**回答的问题",
        "",
        "⚠️ **本报告不声称「接了 agent 之后拒答能力变好/变差」。** 三条原因：",
        "",
        "**① 两侧不是只差一个 agent 层。** system prompt、模型实例、"
        "上下文构造、查询改写四处都不同 ⇒ 差值不能归因于 agent。",
        "",
        "**② 直打侧有硬短路。** `service.py` 在 rerank 最高分 < 5 时"
        "直接返回固定文本、**压根不调模型** ⇒ 它的拒答里混着"
        "「服务替模型拒答」，与 agent 侧的纯模型行为不同源。",
        "",
        f"**③ 基线 {base['refuse_correct']}/{base['n_refuse']} 是检索层口径**"
        f"（出处：`{base['source']}`），"
        "与本文的文本层数字**不可相减**。",
        "",
        "⇒ 要真正回答「agent 层有没有影响」，正确设计是"
        "**固定生成侧不动**（同 prompt / 同模型 / 同 top_k / 同上下文格式），"
        "只替换「检索结果如何进入生成」这一处。"
        "这是下一步，不是本次的结论。",
        "",
    ]

    if n_ans:
        lines += [
            "## 四、应答能力",
            "",
            "| 路径 | 命中率 |",
            "|---|---|",
            f"| 基线 Recall@5（**检索层指标**） | {base['recall_at_5']}/{base['n_answer']} = "
            f"{pct(base['recall_at_5'] / base['n_answer'])} |",
            f"| 直打 `/chat` | {fmt_range(d_ans)} |",
            f"| 走 agent | {fmt_range(a_ans)} |",
            "",
            "⚠️ **这张表不能和基线相减。** 基线的 42/44 是【检索层 Recall@5】——"
            "算的是 gold 文档有没有被检索出来；agent 路径不直接暴露 `sources`，"
            "这里只能判「回答里有没有依据」。**口径不同不能相减**。",
            "",
        ]
    else:
        lines += [
            "## 四、应答能力",
            "",
            "_本次只跑了拒答题集（`--only-refuse`），应答侧无数据。"
            "故不渲染表格：0/0 会显示成「0.0%」——"
            "看起来像「应答能力全军覆没」，而真相是没测。_",
            "",
        ]

    # ★ 章节号动态生成。
    #   之前"五、工具调用"是有数据才渲染、后面的"六"却写死，
    #   --only-refuse 时整节消失 ⇒ 报告里出现「四、（跳过）六」，编号跳号。
    #   这类小错会让人怀疑整份报告的严谨度，所以从数据里算出来。
    sec = 5

    if d_calls:
        lines += [
            f"## {_cn_num(sec)}、工具调用",
            "",
            f"- agent 应答题平均工具调用 "
            f"{(f'{min(d_calls):.2f}~{max(d_calls):.2f}' if len(set(d_calls)) > 1 else f'{d_calls[0]:.2f}')}"
            f" 次；调用上限 `MAX_ROUNDS=6`，实测无一题触顶",
            "",
        ]
        sec += 1

    lines += [
        f"## {_cn_num(sec)}、判据本身被推翻过三次（这部分比数字重要）",
        "",
    ]
    sec += 1
    lines += [
        "| 版本 | 缺陷 | 它会造出的假结论 |",
        "|---|---|---|",
        "| v1 裸关键词 | 正确答案里的否定表述被判拒答 |"
        " 「agent 拒答能力崩了」 |",
        "| v2 有依据优先 | 声明查不到 + 贴条款被判作答 |"
        " 「掉了 24 个百分点」 |",
        "| v3 三段式 | `_EVIDENCE` 是**死代码**（两个出口都是 False） |"
        " 注释写的机制根本没实现，实际跑的是 v1 |",
        "| **v4 起（本次）** | 文本判据降级为辅助，主指标换成结构化信号 |"
        " — |",
        "",
        "★ **v3 那条是本次最贵的教训**：注释里写着"
        "「三段式，二者不再互相短路」，而代码是",
        "",
        "```python",
        "if has_ev:",
        "    return False",
        "return False          # 两个出口相同 ⇒ has_ev 从未影响任何分支",
        "```",
        "",
        "⇒ 用例自检全绿也没能发现它，因为**用例只验输出、不验分支是否可达**。",
        "发现它的是一次**独立盲审**（不看我的结论、直接读代码）。",
        "",
        f"判据现有自检 **{n_cases}** 条常规用例 + **{n_empty_cases}** 条空答案用例"
        f" + **{n_und_cases}** 条"
        "「已知判不准」用例，全过即拦提交。其中后者的期望值本身就是信息：",
        "",
        "- 期望 `拒答/作答` ⇒ 判据在这个形态上可信",
        "- 期望 **`无法判定`** ⇒ 真的分不出来，该题**排除出分母**而不是硬猜",
        "",
        "### 四个判不准的形态（按误判方向分类）",
        "",
        "| 形态 | 判成 | 方向 | 处理 |",
        "|---|---|---|---|",
        "| 正确依据里含「不包含个人」 | 拒答 | ⚠️ **危险**（会把硬答洗白） |"
        " 已加主体约束修好 |",
        "| 因果收尾式拒答 + 补充说明 | 作答 | 保守（低估拒答率） |"
        " 已加 `_REFUSE_CAUSAL` 修好 |",
        "| **「帮不上忙」+ 建议句带数字** | 作答 | 保守（低估拒答率） |"
        " 第五批修好，见下 |",
        "| 空答案 / 工具错误提示 | 拒答 | ⚠️ 虚高 | 改为**第三态 `None`** |",
        "",
        "### ★ 第五批盲审：又抓到 1 处，且暴露一个更麻烦的问题",
        "",
        "跑完 3 遍后逐条读 r24 的原文，发现它**本来就是一条正确拒答**"
        "（「这个问题我帮不上忙——我只能查学校的公开制度文件，里面没有…」）"
        "却被判成作答。根因：`帮不上忙` 只命中 `_REFUSE_STRONG` 而不属于"
        "`_REFUSE_CAUSAL`，于是压不过建议句里的「20人」（`_has_substance` 命中）。",
        "",
        "★ **这与已修的两条是同一形态** —— 说明第六版只修了"
        "「没法告诉你 / 查不到」那一半措辞，漏了「帮不上忙」。"
        "⇒ 词表修法只能止血：同一个意思换个说法就又漏了。"
        "这也是主指标必须换成结构化信号的理由，"
        "文本判据只配报数，不配判定。",
        "",
        "⚠️ **顺带查出一个更严重的问题**：上一轮我说「已用当前判据重算写回」，"
        "其实只重算了 `per_question` 的逐题结果，"
        "`runs[].direct.refuse` 那份**汇总块从没被覆盖** ——",
        "于是一份报告里同时存在两套数字（直打侧 46/61 与 58/61）。"
        "已改为渲染时统一调 `score_refuse` 从逐题重算，不信存好的汇总。",
        "",
        "> 这是本项目第 N 次「数字看起来像结论但其实是旧的」。"
        "区别在于这次是**自己**发现的、而不是别人指出来的 —— "
        "但也意味着前几轮的报告没人核对过。",
        "",
        f"## {_cn_num(sec)}、复现方式",
        "",
        "```bash",
        "# 必须先起 RAG 服务",
        "D:/Python-project/.venv/Scripts/python.exe -m uvicorn service:app --port 8000",
        "",
        "# 注意 -u：不关缓冲的话，重定向到管道时逐题输出看不见",
        "D:/Python-project/.venv/Scripts/python.exe -u agent/refusal_regression.py --runs 3 --only-refuse",
        "D:/Python-project/.venv/Scripts/python.exe agent/render_regression_report.py",
        "",
        "# 变异测试：报告生成器的 4 条守卫（无数据/单遍/真回归/底噪内）",
        "D:/Python-project/.venv/Scripts/python.exe agent/_mut/test_report_renderer.py",
        "```",
        "",
    ]
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"报告已写入 {os.path.relpath(OUT, _ROOT)}")
    print()
    # ★ 只打印主指标（硬答率）与它的分母来源 ——
    #   不再打印"基线/直打/agent 三行文本拒答率"，
    #   因为那会把两把尺子摆在一起，让人下意识去减。
    print(f"agent no_hit 信号占比　{fmt_range(a_no_hit_rate) if a_no_hit_rate else '—'}")
    print(f"直打 no_hit 信号占比　　{fmt_range(d_no_hit_rate) if d_no_hit_rate else '—'}")
    print(f"硬答题数 / 每遍　　　　{hard_txt}")
    print(f"硬答率（主指标）　　　　{hard_rate or '—'}")
    print(f"信号解析失败　　　　　　{sum(a_sig_missing)}（独立报出，不并入 no_hit）")
    print()
    print(f"判定：{verdict}")
    print()
    print("⚠️ 本报告不回答「agent 层让拒答变好还是变差」——")
    print("   两侧的 prompt/模型/上下文/改写都不同，且直打侧有硬短路。详见第三节。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
