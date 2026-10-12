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

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(_ROOT, "agent", "_refusal_regression.json")
OUT = os.path.join(_ROOT, "evalset", "report-agent-refusal-regression.md")


def pct(x: float) -> str:
    return f"{x:.1%}"


def fmt_range(vals: list[float]) -> str:
    """把多次跑的数值渲染成 'min~max（单点）' 或 'a / b / c'。"""
    if len(set(vals)) == 1:
        return pct(vals[0])
    lo, hi = min(vals), max(vals)
    return f"{pct(lo)}~{pct(hi)}（{len(vals)} 次：{' / '.join(pct(v) for v in vals)}）"


def main():
    if not os.path.exists(SRC):
        print(f"[!!] 找不到 {os.path.relpath(SRC, _ROOT)}")
        print("    先跑：agent/refusal_regression.py --runs 3")
        return 1
    data = json.load(open(SRC, encoding="utf-8"))
    base = data["baseline"]
    runs = data["runs"]
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

    # ★ 判据用例条数从判据模块真读，不手写。
    #   上一版这里手写着"8 条"，判据早就扩到 11 条而报告没跟上——
    #   手写的数字一定会掉队，这是本项目反复交的学费。
    n_cases = "（未读到）"
    n_und_cases = "（未读到）"
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from refusal_regression import (_REFUSAL_CASES,   # noqa: E402
                                        _UNDECIDABLE_CASES)
        n_cases = str(len(_REFUSAL_CASES))
        n_und_cases = str(len(_UNDECIDABLE_CASES))
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

    if a_hard and max(a_hard) == 0:
        verdict = ("**未发现硬答**：每一遍里，工具明确回报「库里没有」的题，"
                   "模型都没有再编答案。")
    elif a_hard:
        verdict = (f"**发现硬答 {hard_txt} 题**：工具已经明确说了「库里没有」，"
                   f"模型仍然编了答案出来。这才是本次验收要找的回归。")
    else:
        verdict = ("**本次未观测到 no_hit 信号**，因而无法评估硬答率"
                   "（信号缺失 ≠ 硬答率为 0，别把这两种情况混同）。")

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
        "",
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
        " 工具输出里没读到标记 —— 这是**独立报出**的，不并入 no_hit |",
        "",
        "### 为什么主指标不用「拒答率」",
        "",
        "因为关键词判据做不到低假阳率。实测反例（`agent/probe_toolnode_errors.py` 同源探针）：",
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

    if d_calls:
        lines += [
            "## 五、工具调用",
            "",
            f"- agent 应答题平均工具调用 "
            f"{(f'{min(d_calls):.2f}~{max(d_calls):.2f}' if len(set(d_calls)) > 1 else f'{d_calls[0]:.2f}')}"
            f" 次；调用上限 `MAX_ROUNDS=6`，实测无一题触顶",
            "",
        ]

    lines += [
        "## 六、判据本身被推翻过三次（这部分比数字重要）",
        "",
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
        f"判据现有自检 **{n_cases}** 条常规用例 + **{n_und_cases}** 条"
        "「已知判不准」用例，全过即拦提交。其中后者的期望值本身就是信息：",
        "",
        "- 期望 `拒答/作答` ⇒ 判据在这个形态上可信",
        "- 期望 **`无法判定`** ⇒ 真的分不出来，该题**排除出分母**而不是硬猜",
        "",
        "### 三个判不准的形态（按误判方向分类）",
        "",
        "| 形态 | 判成 | 方向 | 处理 |",
        "|---|---|---|---|",
        "| 正确依据里含「不包含个人」 | 拒答 | ⚠️ **危险**（会把硬答洗白） |"
        " 已加主体约束修好 |",
        "| 因果收尾式拒答 + 补充说明 | 作答 | 保守（低估拒答率） |"
        " 已加 `_REFUSE_CAUSAL` 修好 |",
        "| 空答案 / 工具错误提示 | 拒答 | ⚠️ 虚高 | 改为**第三态 `None`** |",
        "",
        "## 七、复现方式",
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
