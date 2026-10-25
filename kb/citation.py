# -*- coding: utf-8 -*-
"""引用校验：答案里的 `[n]` 到底指没指对块。

## 为什么必须校验，而不是"相信模型"
模型最典型的两种引用错误：
① **编编号**——答案写了 `[7]`，但这次只喂了 3 块（凭空引用，最危险）；
② **不标**——答案有结论却一句引用都没有（等于没出处）。
这两种都能用规则判，而且必须是**规则**：合规场景不能靠"再问一次模型你引的对不对"。

## 为什么按"段落"数未标注，而不是按句子
实测（2026-10-25，campus 20 题）模型的引用风格是**段末标注**：
「……给予留校察看处分。[1]」——一条 `[1]` 覆盖前面整段。按句子切会把同一段里
那些"其实被 [1] 覆盖"的句子全算成没标注，`uncited_count` 直接假高 3~5 倍。
所以单位改成**段落**（空行分隔），并保留 `n_paragraphs` 让人能换算。

## 为什么叫 validity 不叫 precision
ALCE（Gao et al., EMNLP 2023）的 citation precision 要判断"这句话是否**被所引段落支持**"，
那需要 NLI/人判。本模块只做**可机械判定**的两件事：
- 编号是否合法（`1 ≤ n ≤ 本次喂入块数`）；
- 被引块的条款编号（如"第二十四条"）是否出现在答案里（很粗，只作参考信号）。
所以指标叫 `validity`（合法引用 / 全部引用），**不冒充** ALCE 的 precision。
语义级判定留给批 5 的抽样 judge（用现有 DeepSeek 通道，10% 抽样、离线）。
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

CITE_RE = re.compile(r"\[(\d{1,2})\]")
_CLAUSE_NO_RE = re.compile(r"(第[一二三四五六七八九十百零\d]+条)")

#: 这些段落是在"说不知道/说资料不够"，不该因为没标引用就被算成"结论没出处"。
#: ★ 白名单来自实测（campus 20 题）：模型会写"无法**完整**回答""并未系统列出"
#:   这类变体，只写"无法回答"会漏掉它们（2026-10-25 实测踩到，q2 因此假高了 1 段）。
_REFUSAL_HINTS = ("不知道", "未提及", "没有相关", "未规定", "资料中没有", "无法回答",
                  "无法完整", "无法给出", "不能完整", "并未系统列出", "没有关于",
                  "没有找到", "未提供")

_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")


def parse_ids(answer: str) -> list[int]:
    """按出现顺序取编号，去重（同一块引两次仍算一次引用）。"""
    seen: list[int] = []
    for m in CITE_RE.finditer(answer or ""):
        n = int(m.group(1))
        if n not in seen:
            seen.append(n)
    return seen


def split_paragraphs(answer: str) -> list[str]:
    """按空行切段；单行也算一段（模型偶尔不空行）。只用来数"哪段没标引用"。"""
    text = (answer or "").replace("\r\n", "\n").strip()
    if not text:
        return []
    parts = [p.strip() for p in _PARAGRAPH_SPLIT.split(text)]
    return [p for p in parts if p]


def _is_refusal_paragraph(paragraph: str) -> bool:
    return any(h in paragraph for h in _REFUSAL_HINTS)


def _clause_numbers(clause_label: str) -> list[str]:
    """从块的条款标签里抠出"第X条"（我们的标签形如 `第三章-第二十四条`）。"""
    return _CLAUSE_NO_RE.findall(clause_label or "")


def check(answer: str, blocks: Sequence[Mapping[str, Any]],
          *, min_paragraph_chars: int = 10) -> dict[str, Any]:
    """校验一次回答的引用。`blocks` 必须是**这次真正喂进 prompt 的块**（顺序即编号）。

    返回的字段全部可解释：
    - `used_ids` / `unknown_ids`：合法 / 越界编号；
    - `uncited_count` / `uncited_samples`：够长、不是"不知道"类、又没标引用的**段落**；
    - `clause_numbers_in_answer`：答案里出现的条款编号（参考信号，不作判据）；
    - `validity`：合法引用 / 全部引用；**一条引用都没有时为 None**（缺失 ≠ 0）。
    """
    ids = parse_ids(answer)
    n = len(blocks)
    used = [i for i in ids if 1 <= i <= n]
    unknown = [i for i in ids if not (1 <= i <= n)]

    paragraphs = split_paragraphs(answer)
    uncited = [p for p in paragraphs
               if len(p) >= min_paragraph_chars and not CITE_RE.search(p)
               and not _is_refusal_paragraph(p)]

    return {
        "used_ids": used,
        "unknown_ids": unknown,
        "validity": (round(len(used) / len(ids), 4) if ids else None),
        "uncited_count": len(uncited),
        "uncited_samples": [p[:120] for p in uncited[:3]],
        "n_paragraphs": len(paragraphs),
        "clause_numbers_in_answer": sorted(set(_CLAUSE_NO_RE.findall(answer or ""))),
    }


def summarize(report: Mapping[str, Any] | None) -> str:
    """一行摘要，给日志/报告用（不要在每个调用点自己拼）。"""
    if not report:
        return "无引用报告"
    used = report.get("used_ids") or []
    unknown = report.get("unknown_ids") or []
    if not used and not unknown:
        return f"零引用（{report.get('uncited_count', 0)} 句无出处）"
    return (f"引用 {len(used)} 条（合法），越界 {len(unknown)} 条"
            f"{'（' + ','.join(map(str, unknown)) + '）' if unknown else ''}，"
            f"未标注 {report.get('uncited_count', 0)} 句")
