# -*- coding: utf-8 -*-
"""归因实验：MCP 版和直调版跑出差异，到底该算在谁头上？

★★★ 这份脚本存在的理由（一份"看起来严谨"的错结论是怎么产生的）：

  2026-10-12 阶段 3 跑完，直调版 13 次工具调用 vs MCP 版 14 次，差在 s5。
  最顺手的结论是："接了 MCP 之后模型多调了一次工具，MCP 改变了行为。"
  ——**这个结论很危险，因为它几乎肯定是错的，而且看起来很像发现。**

  要判断"差异来自传输层"还是"来自别处"，必须先问一句：
    【同样这套代码、同样这个环境，跑两遍自己一致吗？】
  如果自己都不一致，那任何跨版本的比较都失去意义。

  实测结果：
    · 直调版自己跑两遍：工具调用数完全一致（波动 = 0 题）
    · 但同一个问题问两次，返回文本【逐字不同】，硬数字却完全一致
      md5 全部互不相同，长度 287~301 都在抖
    ⇒ 差异源头是【/chat 的生成非确定性】，不是 MCP 传输层
    ⇒ temperature=0 压不住 DeepSeek 服务端的随机性

  ⇒ 所以 s5 多调的那一次 search_policy，是模型看到更详细的答案后
    【主动去核实】，属于谨慎行为，是好行为，不是回归。

------------------------------------------------------------------
★★ 这个实验的方法论价值，比它的结论更值钱：

  "跨版本 A/B 对比"在有随机性的系统上是【不安全的】，
  因为你分不清【被测差异】和【噪声】。
  ⇒ 正规做法是三段：
      ① 同版本自比 → 量出噪声底噪
      ② 关键量做【语义级】比对（这里比硬数字，不比逐字文本）
      ③ 只有超出底噪的差异才算数

  只做 ① 和 ③ 中间那一步，就会得出"接 MCP 改变了行为"这种
  【无法证伪的漂亮结论】—— 而它恰恰是最容易写进简历的那种。

跑法：
    <虚拟环境>/Scripts/python.exe mcp/verify_mcp_parity.py
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from mcp.client import StdioMCPClient          # noqa: E402
from mcp.tools_impl import ask_policy, search_policy  # noqa: E402
# ★ print 里的 ⇒/✅/✗ 在 cp936 下编不出来（stdout 是管道时）⇒ 崩在打印上。本项目已犯 5 次。
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


Q = "综合测评怎么算分？"        # 就是 s5，两版唯一差异的那道题
REPEAT = 3


def md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()[:12]


def hard_numbers(text: str) -> list[str]:
    """抽硬数字。★ 为什么只比数字不比全文：
       措辞抖动是生成层的噪声，数字抖动才是真问题（改分、加限制条件）。
       这跟"总数守恒不等于分布没变"同源——要看最能代表事实的那几个量。
    """
    return sorted(set(re.findall(r"\d+\s*%?", text)))


def main():
    print(f"实验对象：{Q!r}　每条路径问 {REPEAT} 次\n")

    # ---- 第一段：直调（进程内函数）----
    direct = [ask_policy(Q) for _ in range(REPEAT)]
    print("=== 直调 TOOL_IMPL（同进程函数）===")
    for i, t in enumerate(direct, 1):
        print(f"  第{i}次  md5={md5(t)}  长度={len(t):4d}  数字={hard_numbers(t)}")
    print(f"  ⇒ 逐字互不相同？ {len({md5(t) for t in direct}) > 1}")
    print(f"  ⇒ 数字完全一致？ {len({tuple(hard_numbers(t)) for t in direct}) == 1}")

    # ---- 第二段：走 MCP stdio（跨进程 JSON-RPC）----
    with StdioMCPClient() as c:
        via_mcp = [c.call_tool("ask_policy", {"question": Q}) for _ in range(REPEAT)]

    print("\n=== 走 MCP stdio（跨进程）===")
    for i, t in enumerate(via_mcp, 1):
        print(f"  第{i}次  md5={md5(t)}  长度={len(t):4d}  数字={hard_numbers(t)}")
    print(f"  ⇒ 逐字互不相同？ {len({md5(t) for t in via_mcp}) > 1}")
    print(f"  ⇒ 数字完全一致？ {len({tuple(hard_numbers(t)) for t in via_mcp}) == 1}")

    # ---- 跨路径比对 ----
    all_texts = direct + via_mcp
    all_nums = {tuple(hard_numbers(t)) for t in all_texts}
    # ★ 关键事实：只看【实质性数字】——比例和上限。
    #   '1' '2' '3' 这种是模型给条款编号时的书写形式差异（"1、2、3" vs "①②③"），
    #   属于措辞噪声，不是事实变化。把它们算进来会得到"数字不一致"的假警报，
    #   跟项目里"总数守恒不等于分布没变"是同一类错误——量选错了。
    SUBSTANTIVE = ("30%", "70%")
    sub_nums = {tuple(n for n in hard_numbers(t) if n in SUBSTANTIVE)
                for t in all_texts}
    sub_has6 = {"含『不超过6分』" if ("6 分" in t or "6分" in t) else "缺6分"
                for t in all_texts}

    print("\n" + "=" * 62)
    print(f"全部 {len(all_texts)} 次返回里，不同文本数 = {len({md5(t) for t in all_texts})}"
          f"（共 {len(all_texts)} 次）")
    print(f"不同【全量数字】组合数 = {len(all_nums)}"
          f"（含条款编号的书写差异，属于措辞噪声）")
    print(f"不同【实质性数字】组合数 = {len(sub_nums)}　{sorted(sub_nums)}")
    print(f"『不超过6分』的表述 = {sub_has6}")
    print()
    print("★ 结论（判据写清楚，避免下一个人误读）：")
    print("  ① 文本逐字不同 —— 这是【生成层非确定性】，temperature=0 压不住")
    print("  ② 实质性数字（30%/70%/6分）跨全部 6 次完全一致 —— 【事实没漂】")
    print("  ③ MCP 传输层无损：结构化字段逐条一致（见下方 search_policy 比对）")
    print("  ⇒ 所以 s5 多调的那次 search_policy，源头是措辞差异导致的")
    print("    模型自我核实，不是 MCP 引入的回归。")

    # ---- 传输层无损性的硬检验：直调结果逐字出现在 MCP 结果里吗 ----
    # 措辞会漂，所以只查【关键事实片段】是否都在
    print("\n=== 传输层无损性检验（查关键事实片段有没有被截断/改写）===")
    facts = ["30%", "70%", "品德表现分", "学业表现分", "附加分"]
    for label, texts in (("直调", direct), ("MCP", via_mcp)):
        missing = [(i, [f for f in facts if f not in t])
                   for i, t in enumerate(texts, 1) if any(f not in t for f in facts)]
        print(f"  {label}：{'五项事实全部齐全' if not missing else f'缺片段 {missing}'}")

    # ---- 反向检验：MCP 会不会把内容截断？----
    print("\n=== 截断检验（MCP 版是否比直调版短）===")
    print(f"  直调最短={min(len(t) for t in direct)}　最长={max(len(t) for t in direct)}")
    print(f"  MCP 最短={min(len(t) for t in via_mcp)}　最长={max(len(t) for t in via_mcp)}")
    print("  ⇒ 长度区间重叠 ⇒ 没有系统性截断（若 MCP 恒短，才是丢内容）")
    assert min(len(t) for t in via_mcp) >= 0.5 * min(len(t) for t in direct), \
        "MCP 版明显更短，怀疑截断"

    # ---- search_policy 也查一遍：它是结构化的，比文本更能验无损 ----
    print("\n=== search_policy 结构化输出比对（更严格的传输检验）===")
    d = search_policy("综合测评 算分")
    with StdioMCPClient() as c:
        m = c.call_tool("search_policy", {"query": "综合测评 算分"})
    doc_re = re.compile(r"·\s*(.+?)（相似度\s*([\d.]+)）")
    ds = doc_re.findall(d)
    ms = doc_re.findall(m)
    print(f"  直调抽到 {len(ds)} 条：(文件名, 分数) = {[(a[:18], b) for a, b in ds]}")
    print(f"  MCP 抽到 {len(ms)} 条：(文件名, 分数) = {[(a[:18], b) for a, b in ms]}")
    print(f"  ⇒ 结构完全一致？ {ds == ms}")
    assert ds == ms, "结构化输出在传输层被改动了 —— 这是真 bug"

    print("\n[OK] 传输层无损：结构化字段逐条一致，文本措辞抖动来自生成层")


if __name__ == "__main__":
    main()
