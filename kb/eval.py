# -*- coding: utf-8 -*-
"""kb/eval.py —— 每语料“自动质检”报告

★ 这不是独立评测集，别把它当“105 题独立集”那种证据。
  它只回答三个部署前必须知道的问题：
    1) 库里每条（公开）内容，用自己的话还能不能被检索回来（self-hit@k）；
    2) 库里没有的问题，会不会被硬答（拒答探针）；
    3) 私有块会不会在未授权请求里被搜出来（ACL 泄漏探针）。

独立评测要另建“出题会话不接触代码与结论”的题集；自动质检只配叫 smoke。
"""

from __future__ import annotations

import time
from typing import Callable

# 与业务无关的“库里肯定没有”问题；用于拒答探针。
REFUSAL_PROBES = (
    "图书馆几点关门？",
    "今天天气怎么样？",
    "怎么申请护照？",
)

SearchFn = Callable[..., list[tuple[dict, float]]]


def _key(chunk: dict) -> tuple[str, str]:
    return str(chunk.get("doc_id") or chunk.get("doc")), str(chunk.get("clause"))


def _is_public(chunk: dict) -> bool:
    acl = {str(x).lower() for x in (chunk.get("acl") or ["public"])}
    return "public" in acl


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(len(s) - 1, max(0, int(round((len(s) - 1) * p))))
    return round(s[idx], 1)


def evaluate_namespace(
    chunks: list[dict],
    search: SearchFn,
    namespace: str,
    *,
    acl: list[str] | None = None,
    top_k: int = 5,
    max_items: int = 30,
    refusal_threshold: float = 0.55,
) -> dict:
    """对某个 namespace 出一份可复现的 smoke 报告。

    `search` 的契约与 `RAG.search` 一致：search(query, k=, namespace=, acl=)。
    """
    ns_chunks = [c for c in chunks if c.get("namespace") == namespace]
    public = [c for c in ns_chunks if _is_public(c)]
    sample = (public or ns_chunks)[:max_items]

    hits, lat = 0, []
    for c in sample:
        q = f"{c.get('clause', '')} {str(c.get('text', ''))[:40]}".strip()
        t0 = time.perf_counter()
        got = search(q, k=top_k, namespace=namespace, acl=acl)
        lat.append((time.perf_counter() - t0) * 1000)
        if _key(c) in {_key(g) for g, _ in got}:
            hits += 1

    refusal_details = []
    for q in REFUSAL_PROBES:
        got = search(q, k=top_k, namespace=namespace, acl=acl)
        top = max((s for _, s in got), default=0.0)
        refusal_details.append({"question": q, "refused": (not got) or top < refusal_threshold})

    private = [c for c in ns_chunks if not _is_public(c)]
    acl_leak = None
    if private:
        probe = private[0]
        got = search(str(probe.get("text", ""))[:60], k=top_k, namespace=namespace, acl=None)
        acl_leak = any(_key(c) == _key(probe) for c, _ in got)

    return {
        "namespace": namespace,
        "documents": len({str(c.get("doc_id") or c.get("doc")) for c in ns_chunks}),
        "chunks": len(ns_chunks),
        "sampled": len(sample),
        "self_hit": {
            "passed": hits,
            "total": len(sample),
            "recall_at_k": round(hits / len(sample), 3) if sample else None,
        },
        "refusal": {
            "passed": sum(1 for x in refusal_details if x["refused"]),
            "total": len(refusal_details),
            "details": refusal_details,
        },
        "acl_leak": acl_leak,
        "latency_ms": {"p50": _pct(lat, 0.50), "p95": _pct(lat, 0.95)},
        "note": "自动质检/smoke，不是独立评测集；独立评测需另建题集。",
    }
