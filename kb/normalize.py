# -*- coding: utf-8 -*-
"""把"没有 `##` 标题的正文"补成可切块的形状（真实 PDF 的第一道坎）。

## 为什么需要
2026-10-26 实测：`pypdf` 从一份 3.5 MB 的中文手册里抽出 **339,901 字符**，但
`##` 标题 **0 个**、表格行 **0 行** —— 而 `kb/loader.py` 是按 `## 条款` 切块的。
⇒ 真实 PDF 直接上传会切出 **0 块**，或者退化成定长块（条款号丢失、引用无法指回条款）。

这一步只做一件事：把形如 `第五条` 的行提成 `## 第五条`。
- 已经有 `#`/`##` 标题的文本**原样返回**（`added=0`）；
- 认不出条款的文本也原样返回（交给定长切块，并如实记为"没有条款结构"）。

★ 它不是 OCR，也不猜标题。真实世界里"第X条"是最稳定的结构性信号，
  比"用字号猜标题"可靠得多 —— 猜错会把正文切成碎块，那种错误很难被发现。
"""
from __future__ import annotations

import re

#: 条款行：允许前置空白、全角空格；冒号/顿号后可能有标题文本
CLAUSE_LINE = re.compile(
    r"^[\s　]*(第[一二三四五六七八九十百千零〇\d]{1,8}条(?:\s*[（(][^）)]{1,24}[）)])?)"
    r"[\s　]*(.*)$"
)
HAS_HEADING = re.compile(r"(?m)^\s*#{1,3}\s+")


def ensure_headings(text: str) -> tuple[str, int]:
    """返回 (新文本, 补出来的条款标题数)。幂等：已有标题时不做任何事。"""
    if not text or HAS_HEADING.search(text):
        return text, 0
    out: list[str] = []
    added = 0
    for line in text.splitlines():
        m = CLAUSE_LINE.match(line)
        if not m:
            out.append(line)
            continue
        clause, rest = m.group(1).strip(), m.group(2).strip()
        # 太长的"条款标题"、或以"所述/所指/规定…"开头的，都是正文误命中
        #（比如"第三条所述情形包括员工严重违反……"），不提取
        if len(rest) > 24 or rest.startswith(("所述", "所指", "规定", "明确")):
            out.append(line)
            continue
        if out and out[-1].strip():
            out.append("")
        out.append(f"## {clause}")
        if rest:
            out.append("")
            out.append(rest)
        added += 1
    return "\n".join(out), added
