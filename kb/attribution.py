# -*- coding: utf-8 -*-
"""失败归因：把一次评测结果落进**一个**桶，否则"调优"就没有方向。

## 为什么需要它
评测只报"答对 8/12"没用——同样是答错，可能是检索没捞到、可能是捞到了被预算裁掉、
可能是排在第 4 名没进 top3、也可能是答了却没标引用。这四类失败要改的地方完全不同：
换切块 / 调预算 / 改排序 / 改模板。不归因就去"调优"，等于随机改参数。

## 桶的定义（只有 8 个失败桶：6 主桶 + 2 个拒答细分；其余是"正常结果"）
| 桶 | 判据 | 该改哪层 |
|---|---|---|
| `retrieval_miss` | gold 既不在 trace 的候选里、也不在 included/excluded 里 | 数据层（切块）或检索 |
| `rank_shift` | gold 在候选里，但没进 included（被挤出 top3 或精排分低） | 精排 / 权威序 |
| `budget_cut` | gold 进了 excluded，reason 是 `budget` / `oversize` | Runtime 预算或压缩 |
| `gen_uncited` | gold 进了 included，但答案一句引用都没有（**batch 2 才有 citation 字段**） | 模板 |
| `acl_leak` | 越权题却命中了块（红线） | ACL 双道 |
| `stale_version` | gold 因版本过滤被排除（**batch 4 才有 as-of**） | 数据层 as-of |

正常结果：`ok`（应答/越权按预期）、`refuse_ok`（拒答题正确拒答）、
`answered_unexpectedly`（拒答题却答了）。

★ 2026-11-11：本节原来写「只有 6 个失败桶」，而 `FAILURE_BUCKETS` 早已是 8 个
  （2026-10-27 加了两个拒答细分）。docstring 自己过期了整整两周 ——
  这正是本仓「数字/描述要能指回产物」这条纪律该抓的东西。

★ 缺失不伪装：没有 `context`（`docs/` 旧路径没有 trace）时，`context_available=False`
  会如实报出来，此时"答错但不知道原因"不会被硬塞进 `retrieval_miss`。
"""
from __future__ import annotations

from typing import Any, Mapping

#: 8 个失败桶（可行动的那些）：6 主桶 + 2 个拒答细分。
#: `ok` / `refuse_ok` / `answered_unexpectedly` 是结果，不算桶。
FAILURE_BUCKETS: tuple[str, ...] = (
    "retrieval_miss", "rank_shift", "budget_cut", "gen_uncited", "acl_leak", "stale_version",
    # ★ 2026-10-27 加：拒答题"答了"必须再细分，否则 boundary 题失败无法行动
    "refuse_fabricated", "refuse_context_overreach",
)

_BUDGET_REASONS = ("budget", "oversize")


def gold_pairs(case: Mapping[str, Any]) -> list[tuple[str, str]]:
    """从题面取**全部** gold 条款：`[[doc, clause], ...]`。

    ★ 兼容三种写法：
      · `gold: [doc, clause]`（单条，老题集）
      · `gold: [[doc, clause], ...]`（多跳题：命中任一即算对）
      · `expected_sources: [[doc, clause], ...]`（企业模板）
      少认一种，多跳题就会被静默算成 retrieval_miss。
    """
    raw = case.get("gold") or case.get("expected_sources")
    if not raw:
        return []
    # 单条：[str, str]
    if len(raw) == 2 and isinstance(raw[0], str) and isinstance(raw[1], str):
        return [(str(raw[0]), str(raw[1]))]
    out: list[tuple[str, str]] = []
    for item in raw:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            out.append((str(item[0]), str(item[1])))
    return out


def gold_of(case: Mapping[str, Any]) -> tuple[str, str] | None:
    """取**第一条** gold（保持旧调用方语义）。多跳题请用 `gold_pairs()`。"""
    pairs = gold_pairs(case)
    return pairs[0] if pairs else None


#: 文档名后缀：默认语料用文件名（带 .md），企业存储的 doc 可能不带。
_SOURCE_SUFFIXES = (".md", ".markdown", ".txt", ".html", ".htm", ".docx", ".pdf")


def _norm_source(name: Any) -> str:
    """只去掉**已知文档后缀**再比较；不做别的清洗（避免把 a.md.md 之类洗坏）。"""
    s = str(name or "").strip()
    low = s.lower()
    for suf in _SOURCE_SUFFIXES:
        if low.endswith(suf):
            return s[: -len(suf)]
    return s


def _key(doc: Any, clause: Any) -> tuple[str, str]:
    return _norm_source(doc), str(clause or "")


def _trace(response: Mapping[str, Any]) -> dict[str, Any] | None:
    ctx = response.get("context")
    return ctx if isinstance(ctx, dict) else None


#: 拒答的文本标记。宁可多认几个：漏认会把"正确拒答"记成"编造答案"；
#: 多认最多把"我不知道，但第 3 条说…"记成拒答 —— 后者在报告里更容易被发现。
_REFUSAL_MARKERS = (
    "不知道", "无法回答", "无法据此", "无法确定", "资料不足", "资料中没有",
    "未提供", "没有找到", "未收录", "不编", "不足以", "没有列出", "无法说明",
)


def looks_like_refusal(answer: str | None) -> bool:
    """答案文本里有没有"我答不了"的明确标记。"""
    text = str(answer or "")
    return any(marker in text for marker in _REFUSAL_MARKERS)


def classify(case: Mapping[str, Any], response: Mapping[str, Any]) -> dict[str, Any]:
    """返回 `{bucket, reason, gold, context_available}` —— 可直接写进报告逐条展示。"""
    typ = str(case.get("type", "answer"))
    hit = bool(response.get("knowledge_hit", False))
    gold = gold_of(case)
    pairs = gold_pairs(case)
    ctx = _trace(response)

    out: dict[str, Any] = {"gold": list(gold) if gold else None,
                           "context_available": ctx is not None}

    if typ == "deny":
        out.update(bucket="acl_leak" if hit else "ok",
                   reason="越权题却命中了块" if hit else "越权题被正确拒答")
        return out
    if typ == "refuse":
        # ★ 判据必须是**答案文本**，不能用"检索有没有命中"代理：
        #   实测模型会引用"检索到了但不充分"的资料，然后说"资料中没有…我不知道"；
        #   这时 knowledge_hit=True 却是**正确拒答**，旧代理把两道判成了"意外作答"。
        refused = looks_like_refusal(response.get("answer"))
        if refused or not hit:
            out.update(bucket="refuse_ok",
                       reason=("库外题被正确拒答（答案里明说不知道）" if refused else
                               "库外题被正确拒答"))
            return out
        # 答了：再分两类 —— 有没有真的用检索到的块
        ctx_ref = _trace(response)
        citation = (ctx_ref or {}).get("citation") or {}
        used = citation.get("used_ids") or []
        if used:
            out.update(bucket="refuse_context_overreach",
                       reason="拒答题却作答，且引用了检索到的块（相关但不充分 ⇒ 越界）")
        else:
            out.update(bucket="refuse_fabricated",
                       reason="拒答题却作答，且没有任何引用（疑似编造）")
        return out

    # ---- 以下都是 answer 题 ----
    if ctx is None:
        # 没有 trace 就**不猜**原因（docs/ 旧路径）：命中与否是唯一能说的事实。
        out.update(bucket="ok" if hit else "retrieval_miss",
                   reason="没有 context（旧路径）⇒ 只能按是否命中判；原因未知")
        return out

    included = {_key(b.get("source"), b.get("clause")) for b in (ctx.get("blocks") or [])}
    excluded = {_key(b.get("source"), b.get("clause")): str(b.get("reason"))
                for b in (ctx.get("excluded") or [])}
    cands = {_key(c.get("source"), c.get("clause")) for c in (ctx.get("candidates") or [])}
    gold_keys = [_key(doc, clause) for doc, clause in pairs]

    if any(k in included for k in gold_keys):
        citation = ctx.get("citation") or {}
        if citation and not (citation.get("used_ids") or []):
            out.update(bucket="gen_uncited", reason="答案没有任何引用标记")
            return out
        out.update(bucket="ok", reason="gold 在喂给模型的块里")
        return out

    hit_excluded = next((k for k in gold_keys if k in excluded), None)
    if hit_excluded:
        why = excluded[hit_excluded]
        if why in _BUDGET_REASONS:
            out.update(bucket="budget_cut", reason=f"gold 被预算裁掉（{why}）")
        elif why in ("stale", "superseded", "not_yet_effective"):
            out.update(bucket="stale_version", reason="gold 因版本过滤被排除")
        elif why == "low_score":
            out.update(bucket="rank_shift", reason="gold 在候选里但精排分低，整体拒答")
        else:
            out.update(bucket="retrieval_miss", reason=f"gold 被排除（{why}）")
        return out

    if any(k in cands for k in gold_keys):
        out.update(bucket="rank_shift",
                   reason="gold 在候选里但没进 included（被 top3 或精排分挡住）")
        return out

    out.update(bucket="retrieval_miss", reason="gold 不在候选里（切块/检索问题）")
    return out
