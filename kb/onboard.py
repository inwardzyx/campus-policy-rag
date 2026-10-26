# -*- coding: utf-8 -*-
"""onboarding 的纯逻辑：从条款生成冒烟探针、判分、汇总。

## 口径（必须和报告一起给出去）
这些探针是**机器从条款生成的**，只回答一个问题：
"**这个条款能被检索到、并被回答引用到吗？**"
它**不证明**语义理解、不证明答案正确性，也不替代人工题集（`evalset/scenarios/`）。
报告里必须写明"需人工抽检 20%"，否则又是一次自欺。

## 为什么用"条款探针"而不是自动生成自然问句
自然问句要模型出，出题质量不可控（模型会照着答案出题，等于作弊）。
用"《文档》第X条怎么规定的"这种**结构化问句**，判据是确定的：答案的 sources 里
必须出现**同一个 (doc, clause)**。要测语义，另有人工题集，不在这里混。
"""
from __future__ import annotations

import math
import random
import re
from typing import Any, Iterable, Mapping, Sequence

#: 库外问题：语料里不该有答案。用固定的几条，避免"随机到恰好能答"的抖动。
REFUSAL_PROBES: tuple[str, ...] = (
    "公司食堂的营业时间是几点到几点？",
    "今年年会安排在哪个酒店？",
    "员工班车的路线表在哪里查？",
    "公司附近哪家健身房有合作折扣？",
)


def build_probes(chunks: Sequence[Mapping[str, Any]], *, n: int = 10,
                 seed: int = 7) -> list[dict[str, Any]]:
    """从块里挑 n 条**不同文档、不同条款**的探针（确定性抽样）。"""
    pool = [(str(c.get("doc")), str(c.get("clause")), str(c.get("text") or ""))
            for c in chunks if c.get("doc") and c.get("clause")]
    # 每条 (doc, clause) 只留一条；条款名是"第N段"这类分段标签的不要（不是真条款）
    seen: set[tuple[str, str]] = set()
    uniq: list[tuple[str, str, str]] = []
    for doc, clause, text in pool:
        if (doc, clause) in seen:
            continue
        if clause.startswith("第") and clause.endswith("段"):
            continue
        if len(text.strip()) < 20:          # 太短的块本来就会被把关拦
            continue
        seen.add((doc, clause))
        uniq.append((doc, clause, text))
    if not uniq:
        return []
    rng = random.Random(seed)
    picked = rng.sample(uniq, k=min(n, len(uniq)))
    out = []
    for doc, clause, text in picked:
        # ★ 2026-10-26 实测：**条款号不在索引里**（索引只含正文），所以
        #   「《doc》第四十一条 是怎么规定的？」这种问法检索命中 0；
        #   而正文片段能稳定命中（实测 3/3 命中，第一条 score=0.81）。
        #   这里把正文片段放进问句 —— 这不是绕过问题，是**按当前索引能力提问**；
        #   "按条款号检索"是待做的检索改动（要配对评测），已记进 HANDOFF。
        snippet = re.sub(r"\s+", "", text)[:16]
        out.append({"question": f"《{doc}》{clause} 里「{snippet}…」是怎么规定的？",
                    "gold": [doc, clause]})
    return out


def _pair(source: Mapping[str, Any]) -> tuple[str, str]:
    """把来源统一成 (doc, clause)：兼容 `doc｜clause` 的单字段写法。"""
    doc = str(source.get("doc") or "")
    clause = source.get("clause")
    if clause is None and "｜" in doc:
        doc, _, clause = doc.partition("｜")
    return doc, str(clause or "")


def score_probe(probe: Mapping[str, Any], response: Mapping[str, Any] | None) -> dict[str, Any]:
    """命中 = 答案的 sources 里出现同一个 (doc, clause)。"""
    gold = tuple(probe.get("gold") or ())
    sources = (response or {}).get("sources") or []
    # ★ `/search` 把来源拼成 `doc｜clause` 一个字段（`/chat` 是分开的）；
    #   不拆开就会得到 ('doc｜clause', 'None')，探针永远判不中（实测 0/10）。
    got = {_pair(s) for s in sources}
    citation = ((response or {}).get("context") or {}).get("citation") or {}
    return {
        "question": probe.get("question"),
        "gold": list(gold),
        "hit": gold in got,
        "refused": not (response or {}).get("knowledge_hit", False),
        "validity": citation.get("validity"),
        "took_ms": (response or {}).get("took_ms"),
        "sources": sorted({f"{d}｜{c}" for d, c in got})[:5],
    }


def summarize(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    hits = sum(1 for r in rows if r.get("hit"))
    refused = sum(1 for r in rows if r.get("refused"))
    validity = [r["validity"] for r in rows if r.get("validity") is not None]
    took = sorted(int(r["took_ms"]) for r in rows if r.get("took_ms") is not None)
    # 最近秩法：ceil(0.95·n) 是第几个（1 起），减 1 才是下标
    p95 = (took[min(len(took) - 1, max(0, math.ceil(len(took) * 0.95) - 1))]
           if took else None)
    return {
        "n": n,
        "hit": hits,
        "hit_rate": round(hits / n, 4) if n else None,
        "refused": refused,
        "citation_validity": (round(sum(validity) / len(validity), 4)
                              if validity else None),
        "p95_ms": p95,
        "misses": [r for r in rows if not r.get("hit")],
    }
