# -*- coding: utf-8 -*-
"""兑现本仓的老规矩：**数字不许手写**。

本文件对 `evalset/report-token-cost.md` 里**承载数字的那几段**做定点替换：
数据全部从 `agent/_token_cost.json` 现算，脚本可重复运行、幂等。
叙述段落（结论、"为什么"、更正块的历史记录）**原样保留** —— 它们是项目资产，
不是数字。

★ 为什么要有这个脚本（而不是我手抄 141 个数）：
  这份报告的数字来源换了两次（先加 SIGNAL 计量字段，再把账目字段从标记行拿掉、
  工具侧的账改走重放），每次换都手抄一遍 = 每次都可能抄错，而且下次数据一来又过期。
  ★★ 反面教材就在本仓：`mutate_check_token.py` 里五条变异的"原文串"是按当批数字
  硬写的，DSH 重跑 `--runs 3` 之后数字全变，5 条同时注入失败。

★ 为什么用"替换"而不是"整篇重写"：本文件里那些**关于历史的更正块**（上一版错在哪、
  某个数是怎么来的）不该被生成器抹掉 —— 抹掉之后没人能回答"那 6,337 哪来的"。

跑法：
    <venv>/Scripts/python.exe scripts/render_token_report.py
    <venv>/Scripts/python.exe scripts/render_token_report.py --check   # 只报差异不改
写完必须：
    <venv>/Scripts/python.exe scripts/check_token_report.py     # 权威判据
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "agent", "_token_cost.json")
REPORT = os.path.join(ROOT, "evalset", "report-token-cost.md")


def load():
    d = json.load(open(DATA, encoding="utf-8"))
    direct = [r for r in d["direct"] if not r.get("error") and isinstance(r.get("usage_raw"), dict)]
    agent = [r for r in d["agent"] if not r.get("error")]
    runs = [r for r in d["runs"] if isinstance(r, dict)]
    return d, direct, agent, runs


def stats(direct, agent):
    n = len(agent)
    a_tot = sum(r["usage"]["total"] for r in agent)
    a_in = sum(r["usage"]["input"] for r in agent)
    a_out = sum(r["usage"]["output"] for r in agent)
    a_hit = sum(r["usage"]["cache_hit"] for r in agent)
    d_tot = sum(r["usage_raw"]["total_tokens"] for r in direct)
    d_in = sum(r["usage_raw"]["input_tokens"] for r in direct)
    d_out = sum(r["usage_raw"]["output_tokens"] for r in direct)
    # ★ "硬短路"= 直打侧 n_calls<3（没调生成）；题型列必须与它自洽
    hits = [r for r in direct if r["usage_raw"]["n_calls"] < 3]
    miss = [r for r in direct if r["usage_raw"]["n_calls"] >= 3]
    return {
        "n": n,
        "a_tot": a_tot / n, "a_in": a_in / n, "a_out": a_out / n,
        "a_hit_rate": a_hit / a_in * 100 if a_in else 0,
        "a_hit": a_hit, "a_in_sum": a_in, "a_out_sum": a_out, "a_tot_sum": a_tot,
        "d_tot": d_tot / n, "d_in": d_in / n, "d_out": d_out / n,
        "d_tot_sum": d_tot,
        "out_ratio": a_out / a_tot * 100 if a_tot else 0,
        "n_refuse": len(hits), "n_answer": len(miss),
        "refuse_in": sum(r["usage_raw"]["input_tokens"] for r in hits) / len(hits),
        "refuse_out": sum(r["usage_raw"]["output_tokens"] for r in hits) / len(hits),
        "refuse_tot": sum(r["usage_raw"]["total_tokens"] for r in hits) / len(hits),
        "ans_in": sum(r["usage_raw"]["input_tokens"] for r in miss) / len(miss),
        "ans_out": sum(r["usage_raw"]["output_tokens"] for r in miss) / len(miss),
        "ans_tot": sum(r["usage_raw"]["total_tokens"] for r in miss) / len(miss),
        "d_calls_total": sum(r["usage_raw"]["n_calls"] for r in direct),
        "a_calls_total": sum(r["usage"]["n_calls"] for r in agent),
        "d_calls_avg": sum(r["usage_raw"]["n_calls"] for r in direct) / n,
        "a_calls_avg": sum(r["usage"]["n_calls"] for r in agent) / n,
        "cost_sum": sum(r["usage"]["cost"] or 0 for r in agent),
        "cost_avg": sum(r["usage"]["cost"] or 0 for r in agent) / n,
        "missing_calls": sum(r["usage_raw"].get("missing_calls", 0) for r in direct),
        "missing_usage": sum(r["usage"].get("missing_usage", 0) for r in agent),
        "truncated": sum(1 for r in agent if r.get("truncated")),
    }


def per_question_table(direct, agent) -> str:
    dm = {r["id"]: r for r in direct}
    am = {r["id"]: r for r in agent}
    lines = ["| 题号 | 题型 | 直打调用 | 直打 total | agent 调用 | agent input | "
             "agent output | agent total | 缓存命中 | 成本(元) |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for qid in am:
        d, a = dm[qid]["usage_raw"], am[qid]["usage"]
        # ★ 题型必须由 n_calls 派生（检查器会独立复核这一条）
        typ = "拒答" if d["n_calls"] < 3 else "应答"
        lines.append(
            f"| {qid} | {typ} | {d['n_calls']} | {d['total_tokens']} | {a['n_calls']} | "
            f"{a['input']} | {a['output']} | {a['total']} | {a['cache_hit']} | "
            f"{a['cost']:.6f} |")
    return "\n".join(lines)


def runs_table(runs) -> str:
    lines = ["| 遍 | 直打 | agent 自身 | 工具侧 | 仅自身倍率 | **含工具倍率** |",
             "|---|---|---|---|---|---|"]
    for r in runs:
        lines.append(f"| {r['run']} | {r['direct_mean']:.0f} | "
                     f"{r['agent_own_mean']:.0f} | {r['tool_mean']:.0f} | "
                     f"{r['ratio_own']:.2f}× | **{r['ratio_all']:.2f}×** |")
    return "\n".join(lines)


def sub(text: str, pattern: str, repl: str, label: str, errs: list[str]) -> str:
    """定点替换；**匹配不到就报错**（不许静默跳过 —— 静默跳过 = 报告悄悄过期）。"""
    new, k = re.subn(pattern, repl, text, count=1, flags=re.M)
    if k != 1:
        errs.append(f"✗ 替换失败（匹配 {k} 处）：{label}\n    模式：{pattern}")
        return text
    return new


def count_runs_rows(text: str) -> int:
    """数一数"按 `check_token_report.py` 的口径"能解析出几行三遍表。

    ★ 为什么要在这里复刻一遍解析器：渲染器的输出**必须**满足下游解析器的形状，
      而"我感觉只有一个表了"是自检里最不可信的那种证据（本仓已有多次同源教训）。
      这里用一个**刻意独立写出来**的宽松复刻：剥掉引用/列表前缀后以 `|` 开头、
      切成恰好 6 格、首格是纯数字、末两格是倍率。
      口径与 `scripts/check_token_report.py::parse_runs_table` 对齐（那边是权威）。
    """
    n = 0
    for raw in text.splitlines():
        s = re.sub(r"^(?:>\s*)?(?:[-*]\s+)?", "", raw.strip())
        if not s.startswith("|"):
            continue
        cells = [c.replace("*", "").replace("`", "").strip()
                 for c in s.strip("|").split("|")]
        if len(cells) != 6 or not re.fullmatch(r"\d+", cells[0]):
            continue
        if ("×" in cells[4] or re.fullmatch(r"[\d.]+", cells[4])) and \
           ("×" in cells[5] or re.fullmatch(r"[\d.]+", cells[5])):
            n += 1
    return n


def render(text: str, s: dict, direct, agent, runs) -> tuple[str, list[str]]:
    errs: list[str] = []

    # ---- 1. 「拒答并不便宜」那张表 ----
    text = sub(text, r"\| 硬短路拒答（没调生成） \| \d+ \| \d+ \| \d+ \| \d+ \|",
               f"| 硬短路拒答（没调生成） | {s['n_refuse']} | {s['refuse_in']:.0f} | "
               f"{s['refuse_out']:.0f} | {s['refuse_tot']:.0f} |", "拒答表行", errs)
    text = sub(text, r"\| 调了生成作答 \| \d+ \| \d+ \| \d+ \| \d+ \|",
               f"| 调了生成作答 | {s['n_answer']} | {s['ans_in']:.0f} | "
               f"{s['ans_out']:.0f} | {s['ans_tot']:.0f} |", "应答表行", errs)
    text = sub(text, r"\*\*input 一分没省\*\*（\d+ / \d+ = \*\*\d+%\*\*）",
               f"**input 一分没省**（{s['refuse_in']:.0f} / {s['ans_in']:.0f} = "
               f"**{s['refuse_in'] / s['ans_in']:.0%}**）", "拒答 input 占比", errs)

    # ---- 2. 逐题表（整块重画，块内有 h1..hN 的唯一表头）----
    old_tbl = re.search(
        r"(\| 题号 \| 题型 \| 直打调用 \|.*?\n)((?:\|.*\n)+)", text)
    if not old_tbl:
        errs.append("✗ 找不到逐题表格")
    else:
        text = text[:old_tbl.start(2)] + per_question_table(direct, agent) + "\n" + \
            text[old_tbl.end(2):]

    # ---- 3. 均值那一行 ----
    text = sub(text,
               r"\*\*均值：直打 \d+ token/题\s*agent \d+ token/题\s*agent 是直打的 [\d.]+ 倍。\*\*",
               f"**均值：直打 {s['d_tot']:.0f} token/题　agent {s['a_tot']:.0f} token/题　"
               f"agent 是直打的 {s['a_tot'] / s['d_tot']:.2f} 倍。**", "均值行", errs)

    # ---- 4. 三遍表（整块重画）----
    # ★ 用**显式字符串定位**而不是正则，两个理由：
    #   ① 这张表整体躺在 markdown **引用块**里（每行 `> | 1 | ... |`）。
    #      本仓为这个坑记过一次：`line.strip().startswith("|")` 一行都抓不到，
    #      而解析器返回空、上层却报"表被删了" ⇒ 诊断把方向指偏了。
    #   ② 表格行的正则（`(?:\|.*\n)+`）在中文与 `**` 混排时很容易差一个字符就整块不匹配，
    #      而失败信息只会说"找不到表" —— 那正是上面那个坑的翻版。
    #   ⇒ 表头字符串 + 逐行推进，行为一眼可读。
    RUNS_HEADER = "| 遍 | 直打 | agent 自身 | 工具侧 | 仅自身倍率 | **含工具倍率** |"

    def _is_table_line(s: str) -> bool:
        """表格行判定：**必须先剥引用前缀**。

        ★ 这就是本次真正踩到的坑：`lines[j].lstrip().startswith("|")` 对
          `> | 1 | … |` 恒为 False（`lstrip()` 只去空白、不去 `> `），
          于是"表格块"被算成只有表头一行：替换范围错、诊断还说"只找到一张表"。
          ⇒ 方向整个指偏。本仓为同一个坑记过一次
          （`check_token_report.py::parse_runs_table` 的 docstring 写着同一句话）。
        """
        return s.lstrip().lstrip(">").lstrip().startswith("|")

    lines = text.split("\n")
    hits = [k for k, ln in enumerate(lines) if RUNS_HEADER in ln]

    def _block_end(start: int) -> int:
        j = start + 1
        while j < len(lines) and _is_table_line(lines[j]):
            j += 1
        return j

    if not hits:
        errs.append(f"✗ 找不到三遍表表头：{RUNS_HEADER!r}")
    elif runs:
        # ★ 文件里有两张同表头的三遍表：第一张是**当前账**，后面那张在更正块里、
        #   记的是改动前的账。`check_token_report.py` 按 6 列行抓**所有**行 ⇒
        #   两张会被读成"报告写了 6 遍"，当场报红。
        #   ⇒ 第一张刷新成当前账；**其余降级成行内文本**（一个 `|` 都不留），
        #     旧数字全保留 —— 历史凭据不能删，删了没人能回答"那 6,337 哪来的"。
        #   从后往前改，避免先改前面把后面的下标顶偏。
        for i in reversed(hits):
            j = _block_end(i)
            prefix = lines[i][:lines[i].index("|")]
            if i == hits[0]:
                lines[i:j] = [prefix + ln for ln in runs_table(runs).split("\n")]
                continue
            old = []
            for ln in lines[i:j]:
                cells = [c.strip() for c in
                         ln.lstrip().lstrip(">").strip().strip("|").split("|")]
                if len(cells) == 6 and cells[0].isdigit():
                    old.append(cells)
            if old:
                txt = "；".join(
                    f"第 {c[0]} 遍 直打 {c[1]} / agent 自身 {c[2]} / 工具侧 {c[3]}"
                    f"（仅自身 {c[4]}、含工具 {c[5]}）" for c in old)
                lines[i:j] = [f"{prefix}⚠️ 改动前那一版的实测凭据"
                              f"（已降级为正文，免得被当成第二张遍数表）：{txt}。"]
            else:
                lines[i:j] = [f"{prefix}{ln.lstrip().lstrip('> ')}" for ln in lines[i:j]]
        text = "\n".join(lines)

    if runs:
        lo = min(r["ratio_own"] for r in runs)
        hi = max(r["ratio_own"] for r in runs)
        lo2 = min(r["ratio_all"] for r in runs)
        hi2 = max(r["ratio_all"] for r in runs)
        text = sub(text, r"- \*\*仅自身：[\d.]+ ~ [\d.]+\*\*",
                   f"- **仅自身：{lo:.2f} ~ {hi:.2f}**", "仅自身范围", errs)
        text = sub(text, r"- \*\*含工具侧：[\d.]+ ~ [\d.]+\*\*",
                   f"- **含工具侧：{lo2:.2f} ~ {hi2:.2f}**", "含工具范围", errs)
        tm = sum(r.get("tool_usage_missing", 0) or 0 for r in runs)
        # ★ 这一句两个方向都被检查器盯着：有缺口必须写数、没缺口不许留旧数。
        #   两种写法各配一条模式，并在下面**断言只命中一种** ——
        #   幂等性靠这条保证（重跑一次不该报"匹配 0 处"）。
        # ★ 这段话在**引用块**里，每行前面有 `> `；而两行之间的换行之后**不带** `> `。
        #   所以模式必须从"破折号"开始匹配（不含行首前缀），替换文本也自动被前缀带走。
        pat_old = (r"第 \d+ 遍出现过「工具侧有 \d+ 次没报上账」[^\n]*\n"
                   r"[^\n]*（\*\*没有把那 \d+ 次当 0\*\*）")
        pat_new = r"三遍\*\*都没有\*\*「没报上账」的调用[^\n]*\n[^\n]*折扣。"
        if tm == 0:
            new_sent = ("三遍**都没有**「没报上账」的调用（`tool_usage_missing` 合计 0）——\n"
                        "  即工具侧的账这次是**完整的**，不存在「低估」那层折扣。")
            mine, other = pat_new, pat_old
        else:
            new_sent = (f"本批有 {tm} 次「工具侧没报上账」，由 `tool_usage_missing` 如实标出\n"
                        f"  （**没有把那 {tm} 次当 0**）")
            mine, other = pat_old, pat_new
        n_new, n_old = len(re.findall(pat_new, text)), len(re.findall(pat_old, text))
        if (n_new, n_old) == (1, 0):
            text = re.sub(pat_new, lambda _m: new_sent, text, count=1)
        elif (n_new, n_old) == (0, 1):
            text = re.sub(pat_old, lambda _m: new_sent, text, count=1)
        else:
            errs.append(f"✗ tool_usage_missing 那句：期望恰好命中一处，"
                        f"实际 新写法={n_new} 旧写法={n_old}")

    # ---- 5. 数据质量自检 ----
    text = sub(text, r"- `missing_calls = 0` —— 直打侧 \d+ 次 LLM 调用（\d+×3 \+ \d+×2）全部返回 usage",
               f"- `missing_calls = {s['missing_calls']}` —— 直打侧 {s['d_calls_total']} 次 LLM 调用"
               f"（{s['n_answer']}×3 + {s['n_refuse']}×2）全部返回 usage",
               "missing_calls", errs)
    text = sub(text, r"- `missing_usage = 0` —— agent 侧 \d+ 次调用同样全部拿到。",
               f"- `missing_usage = {s['missing_usage']}` —— agent 侧 "
               f"{s['a_calls_total']} 次调用同样全部拿到。", "missing_usage", errs)
    text = sub(text, r"- `truncated = 0` —— 20 题都没触到 `MAX_ROUNDS`。",
               f"- `truncated = {s['truncated']}` —— {s['n']} 题都没触到 `MAX_ROUNDS`。",
               "truncated", errs)
    text = sub(text, r"- \*\*output 占比 [\d.]+%\*\*（\d+ / \d+）",
               f"- **output 占比 {s['out_ratio']:.1f}%**（{s['a_out_sum']:.0f} / "
               f"{s['a_tot_sum']:.0f}）", "output 占比", errs)

    # ---- 6.〈最值得说的四点〉里的算式（检查器逐条抠）----
    text = sub(text, r"\*\*① 接了 agent 之后每题贵 [\d.]+ 倍",
               f"**① 接了 agent 之后每题贵 {s['a_tot'] / s['d_tot']:.2f} 倍", "①倍率", errs)
    text = sub(text, r"agent 侧 input 均值 \d+、直打侧 \d+，\*\*input 多 \d+%（[\d.]+ 倍）\*\*",
               f"agent 侧 input 均值 {s['a_in']:.0f}、直打侧 {s['d_in']:.0f}，"
               f"**input 多 {s['a_in'] / s['d_in'] - 1:.0%}（{s['a_in'] / s['d_in']:.2f} 倍）**",
               "input 倍率", errs)
    text = sub(text, r"而 output 均值 \d+ 对 \d+，\*\*只多 \d+ token\*\*",
               f"而 output 均值 {s['a_out']:.0f} 对 {s['d_out']:.0f}，"
               f"**只多 {s['a_out'] - s['d_out']:.0f} token**", "output 差值", errs)
    text = sub(text, r"\*\*② agent 侧缓存命中率 [\d.]+%（cache_hit 合计 \d+ / input 合计 \d+）。\*\*",
               f"**② agent 侧缓存命中率 {s['a_hit_rate']:.1f}%（cache_hit 合计 "
               f"{s['a_hit']:.0f} / input 合计 {s['a_in_sum']:.0f}）。**", "缓存命中率", errs)
    text = sub(text, r"\*\*③ agent 侧 output 只占 [\d.]+%（\d+/\d+）。\*\*",
               f"**③ agent 侧 output 只占 {s['out_ratio']:.1f}%"
               f"（{s['a_out']:.0f}/{s['a_tot']:.0f}）。**", "③output 占比", errs)
    text = sub(text, r"\*\*④ 成本 [\d.]+ 元/题，20 题合计 [\d.]+ 元。\*\*",
               f"**④ 成本 {s['cost_avg']:.4f} 元/题，{s['n']} 题合计 "
               f"{s['cost_sum']:.4f} 元。**", "④成本", errs)

    # ---- 7. 报告第 1 行的口径说明：注明它已是生成物 ----
    marker = "> 生成方式：`agent/token_cost.py` 实测"
    note = ("> ★★ **本文件里承载数字的那几段是生成物**：由 `scripts/render_token_report.py`\n"
            "> 从 `agent/_token_cost.json` 现算后定点替换（叙述与更正块保留）。\n"
            "> ⇒ **手改那些数字会被 `scripts/check_token_report.py` 打红**；\n"
            ">   要改数字就改数据、然后重跑这个脚本。\n"
            ">\n")
    if note not in text:
        text = sub(text, re.escape(marker), note + marker, "顶部生成说明", errs)

    # ---- 8. 收尾自检：渲染出来的文件必须让下游解析器只数到 len(runs) 行 ----
    #   ★ 这条是本次真正抓到问题的那一条：我第一版只替换"第一张"三遍表，
    #     而文件里还有一张同表头的（在更正块里），解析器把两张一起数 ⇒ 报"报告 6 遍"。
    #     靠肉眼读输出没发现，靠这条机械自检当场响。
    if runs:
        got = count_runs_rows(text)
        if got != len(runs):
            errs.append(
                f"✗ 渲染后仍能解析出 {got} 行三遍表，而 JSON 里只有 {len(runs)} 遍 ——\n"
                f"   多半是文件里还有**第二张同表头**的表（更正块里的历史账）。\n"
                f"   ⇒ 历史数字要么降级成行内文本（不带 `|`），要么把表头改掉。")
    return text, errs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只报差异，不写文件")
    args = ap.parse_args()
    if not os.path.exists(DATA):
        print(f"✗ 缺 {os.path.relpath(DATA, ROOT)} —— 先跑 agent/token_cost.py")
        return 1
    d, direct, agent, runs = load()
    s = stats(direct, agent)
    original = open(REPORT, encoding="utf-8").read()
    new, errs = render(original, s, direct, agent, runs)
    if errs:
        print("\n".join(errs))
        print("✗ 有替换失败 —— 报告结构变了，先看它的写法再改这个脚本"
              "（★ 别直接删判据）")
        return 1
    if args.check:
        print("✓ 替换规则全部匹配上了" + ("（报告已是最新）" if new == original
                                          else "（但报告内容与数据不同，需要重跑本脚本）"))
        return 0
    open(REPORT, "w", encoding="utf-8", newline="").write(new)
    print(f"✓ 已按 {os.path.relpath(DATA, ROOT)} 刷新 {os.path.relpath(REPORT, ROOT)}"
          f"（{len(new)} 字节）")
    print("  接着跑：scripts/check_token_report.py（权威判据）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
