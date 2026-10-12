# -*- coding: utf-8 -*-
"""核对 report-token-cost.md 里的每个数字是否与 _token_cost.json 一致。

★★ 为什么要有这个脚本：

  这个项目的教训是"手写的数字一定会掉队"（已经犯过 4 次）。
  但反过来也有一个陷阱：**核对脚本自己出错时会假装报告错了**。

  本脚本自己踩过的两个坑（都曾让报告"看起来有问题"，实际报告是对的）：
    ① 用字符串比较 `0.0041` vs `0.004100` —— 数值相等、字符串不等
    ② 用列下标（cells[5]）定位字段 —— 报告改个表头就全盘错位

  ⇒ 所以这里的规矩是：
    · 按【列名】定位，不按下标
    · 按【数值】比较，不按字符串
    · 报告里没有的列要报出来，而不是静默跳过

跑法：
    D:/Python-project/.venv/Scripts/python.exe agent/check_token_report.py
"""

from __future__ import annotations

import json
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(_ROOT, "agent", "_token_cost.json")
MD = os.path.join(_ROOT, "evalset", "report-token-cost.md")

# 表头文字（必须与 report-token-cost.md 里实际写的完全一致）
HEADERS = ("input", "output", "total", "缓存命中", "命中率", "LLM 调用", "成本(元)")


def load_rows(md: str) -> dict[str, list[str]]:
    """把 markdown 表格的行解析成 {题号: [单元格...]}。"""
    out = {}
    for line in md.split("\n"):
        m = re.match(r"^\|\s*(h\d+)\s*\|", line)
        if m:
            out[m.group(1)] = [c.strip() for c in line.strip("|").split("|")]
    return out


def main() -> int:
    if not os.path.exists(SRC) or not os.path.exists(MD):
        print("[!!] 缺 _token_cost.json 或 report-token-cost.md")
        print("    先跑：agent/token_cost.py --n 10")
        return 1

    data = json.load(open(SRC, encoding="utf-8"))
    rows_with_usage = [r for r in data["agent"] if r.get("usage")]
    md = open(MD, encoding="utf-8").read()
    rows = load_rows(md)

    # ① 表头还在吗？—— 列被改名/删除时，这里要【拦住】而不是只提示。
    #   之前只 print 一次、退出码照样是 0，等于"看见了但没当回事"。
    #
    # ★★ 用 `h in md` 查表头是错的：`"缓存命中" in "缓存命中X"` 为 True，
    #   所以把列名改成"缓存命中X"能骗过这个检查。
    #   ⇒ 只能查【整行表头】，或者要求列名后面紧跟分隔符。
    header_line = next((ln for ln in md.split("\n")
                        if ln.startswith("| 题号")), "")
    missing_hdr = [h for h in HEADERS if f"| {h} " not in header_line
                   and f"| {h} |" not in header_line]
    if missing_hdr:
        print(f"[!!] 报告表头缺列：{missing_hdr}")
        print(f"    实际表头：{header_line.strip()}")
        print("    核对脚本按列名定位，表头变了就没法核 —— 先确认报告是不是被改过")
        return 1

    problems = []
    for r in rows_with_usage:
        u = r["usage"]
        hit_rate = u["cache_hit"] / u["input"] if u["input"] else 0.0
        cells = rows.get(r["id"])
        if not cells:
            problems.append((r["id"], "*", "报告里没有这一行", ""))
            continue

        # ★ 按【列语义】配对，不是按下标硬取。
        #   百分比列的容差是"报告里写几位小数就留几位"——
        #   之前用 1e-3 的绝对误差，结果 66.4 vs 66.4356（差 0.036）都不报，
        #   那等于没核。报告里显示 66.4%，真实值就该在这个容差内。
        pct_tol = 0.5 * 10 ** -(len(cells[5].split(".")[-1]) - 1)
        for label, got, exp, tol in (
            ("input", cells[1], u["input"], 1e-6),
            ("output", cells[2], u["output"], 1e-6),
            ("total", cells[3], u["total"], 1e-6),
            ("缓存命中", cells[4], u["cache_hit"], 1e-6),
            ("命中率", cells[5].rstrip("%"), hit_rate * 100, pct_tol),
            ("调用", cells[6], u["n_calls"], 1e-6),
            ("成本", cells[7], u["cost"], 1e-6),
        ):
            try:
                g, e = float(got), float(exp)
            except ValueError:
                problems.append((r["id"], label, got, exp))
                continue
            if abs(g - e) > tol:
                problems.append((r["id"], label, got, exp))

    n = len(rows_with_usage)
    print(f"逐题核对：{n} 题 × 7 列 = {n * 7} 个数字（数值比较，非字符串）")
    if problems:
        print(f"[!!] {len(problems)} 处不符：")
        for q, col, got, exp in problems[:20]:
            print(f"    {q:5} {col:8} 报告={got!s:>12}  实测={exp}")
        return 1
    print("[OK] 全部对上 —— 报告里的数字没有一个是手填的")
    return 0


if __name__ == "__main__":
    sys.exit(main())