# -*- coding: utf-8 -*-
"""评测指标：把"测什么"从"怎么跑"里拆出来。

## 为什么单独一个模块
`kb/eval_runner.py` 负责"按题型判对错"（answer/refuse/deny），本模块负责
**上下文层与检索层的指标**：context_recall / context_precision / budget_cut_rate /
ndcg@k / p95。两者都不碰 HTTP，也都不调 LLM —— 这样才能离线、确定性地跑。

## 口径借用（不重复造轮子）
- **nDCG@k / R@k / RR**：用 `ir-measures`（TREC 口径），**不自己写**。
  它不在 runtime 依赖里，只在 `requirements-eval.txt`；没装时指标返回 `None`
  并在 `notes` 里写明原因（**缺失 ≠ 0**，不许用 0 假装算过）。
- **Wilson 区间**：复用 `kb/eval_runner.wilson_interval`，不抄第二份。
- **citation_validity / uncited_count**：从 `context.citation` 来（batch 2 起）；
  ALCE 口径的"是否被所引段落支持"要 NLI，规则判不了，所以**不叫 precision**。

## 为什么 context_recall 自己算
它不是标准 IR 指标，而是"gold 条款有没有进这一轮真正喂给模型的块"——
只有我们的 `ContextTrace` 知道答案，没有现成库可复用。
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from kb import attribution
from kb.eval_runner import wilson_interval


def _ctx(response: Mapping[str, Any]) -> dict[str, Any] | None:
    ctx = response.get("context")
    return ctx if isinstance(ctx, dict) else None


def _included(ctx: Mapping[str, Any] | None) -> set[tuple[str, str]]:
    if not ctx:
        return set()
    return {(str(b.get("source")), str(b.get("clause"))) for b in (ctx.get("blocks") or [])}


def _citation_field(ctx: Mapping[str, Any] | None, key: str):
    """从 `context.citation` 里取一个字段；没有 citation（旧路径/未生成）就是 None。"""
    report = (ctx or {}).get("citation") or {}
    return report.get(key)


def _mean(values: Sequence[float]) -> float | None:
    """均值；空列表返回 None —— **缺失不补 0**，否则"没算"会看起来像"算出来是 0"。"""
    vals = [float(v) for v in values if v is not None]
    return round(sum(vals) / len(vals), 4) if vals else None


def score_row(case: Mapping[str, Any], response: Mapping[str, Any]) -> dict[str, Any]:
    """一题一行：判分 + 归因 + 上下文指标。所有字段都可能是 None（= 拿不到）。"""
    ctx = _ctx(response)
    included = _included(ctx)
    gold = attribution.gold_of(case)
    attr = attribution.classify(case, response)
    typ = str(case.get("type", "answer"))

    recall = None
    precision = None
    if typ == "answer" and gold:
        if ctx is None:
            recall = None          # 没有 trace 就没有"上下文召回"这回事，不补 0
        else:
            recall = gold in included
            precision = (1.0 if gold in included else 0.0) if included else None

    return {
        "id": case.get("id"),
        "type": typ,
        "question": case.get("question"),
        "bucket": attr["bucket"],
        "reason": attr["reason"],
        "context_available": attr["context_available"],
        "knowledge_hit": bool(response.get("knowledge_hit", False)),
        "took_ms": response.get("took_ms"),
        "context_recall": recall,
        "context_precision": precision,
        # 引用（batch 2 起）：validity = 合法编号 / 全部编号；uncited = 无出处的结论句数。
        # ★ 不叫 precision：ALCE 的 citation precision 要 NLI 判"这句话被所引段落支持"，
        #   规则判不了，不冒充这个口径（见 kb/citation.py 顶部）。
        "citation_validity": _citation_field(ctx, "validity"),
        "uncited_count": _citation_field(ctx, "uncited_count"),
        "included": len(included),
        "sources": [s.get("doc") for s in (response.get("sources") or [])],
        # ★ 留一段答案原文（截断）：报告要能被人逐条抽验，离线复算也要有输入。
        #   全文很长，截 500 字够看出"答了什么、引了哪句"。
        "answer": (response.get("answer") or "")[:500],
    }


def _rate(rows: Sequence[Mapping[str, Any]], typ: str, ok_buckets: Sequence[str]) -> dict[str, Any]:
    subset = [r for r in rows if r["type"] == typ]
    n = len(subset)
    k = sum(1 for r in subset if r["bucket"] in ok_buckets)
    lo, hi = wilson_interval(k, n) if n else (None, None)
    return {"passed": k, "total": n,
            "rate": round(k / n, 4) if n else None,
            "wilson_95": [lo, hi] if n else None}


def _ndcg(rows: Sequence[Mapping[str, Any]], cases: Sequence[Mapping[str, Any]],
          k: int = 3) -> tuple[float | None, str]:
    """按 sources 的顺序算 nDCG@k（二值相关：gold 那一条 = 1）。"""
    try:
        import ir_measures
        from ir_measures import nDCG
    except Exception as exc:                      # noqa: BLE001
        return None, f"ir_measures 不可用（{type(exc).__name__}）：{exc}"

    qrels: dict[str, dict[str, int]] = {}
    run: dict[str, dict[str, float]] = {}
    for case in cases:
        gold = attribution.gold_of(case)
        if not gold or str(case.get("type", "answer")) != "answer":
            continue
        qid = str(case.get("id"))
        qrels[qid] = {f"{gold[0]}｜{gold[1]}": 1}
        row = next((r for r in rows if r["id"] == case.get("id")), None)
        docs = list(row["sources"]) if row else []
        # sources[i] 的 doc 字段本身就是 "doc｜clause"，与 qrels 的键同构
        run[qid] = {str(doc): float(len(docs) - i) for i, doc in enumerate(docs)}
    if not qrels:
        return None, "没有可算的 answer 题（gold 缺失）"
    got = ir_measures.calc_aggregate([nDCG @ k], qrels, run)
    value = got.get(nDCG @ k)
    return (round(float(value), 4) if value is not None else None), ""


def aggregate(rows: Sequence[Mapping[str, Any]], cases: Sequence[Mapping[str, Any]],
              k: int = 3) -> dict[str, Any]:
    """汇总：三类题的正确率 + Wilson + 6 个上下文/检索指标 + 归因分布。"""
    notes: list[str] = []

    buckets: dict[str, int] = {}
    for r in rows:
        buckets[r["bucket"]] = buckets.get(r["bucket"], 0) + 1

    recalls = [r["context_recall"] for r in rows if r["context_recall"] is not None]
    precisions = [r["context_precision"] for r in rows if r["context_precision"] is not None]
    answer_rows = [r for r in rows if r["type"] == "answer"]

    ndcg, ndcg_note = _ndcg(rows, cases, k=k)
    if ndcg_note:
        notes.append(ndcg_note)
    else:
        notes.append(f"nDCG@{k} 由 ir-measures 现算（二值相关，键=doc｜clause）")

    times = sorted(int(r["took_ms"]) for r in rows if r.get("took_ms") is not None)
    p95 = times[max(0, int(round(0.95 * (len(times) - 1))))] if times else None

    return {
        "n_cases": len(rows),
        "answer": _rate(rows, "answer", ["ok"]),
        "refuse": _rate(rows, "refuse", ["refuse_ok"]),
        "deny": _rate(rows, "deny", ["ok"]),
        "buckets": dict(sorted(buckets.items(), key=lambda kv: -kv[1])),
        "failure_buckets": {b: buckets.get(b, 0) for b in attribution.FAILURE_BUCKETS},
        "context_recall": (round(sum(recalls) / len(recalls), 4) if recalls else None),
        "context_precision": (round(sum(precisions) / len(precisions), 4) if precisions else None),
        f"ndcg@{k}": ndcg,
        "budget_cut_rate": (round(sum(1 for r in answer_rows if r["bucket"] == "budget_cut")
                                  / len(answer_rows), 4) if answer_rows else None),
        "citation_validity": _mean([r["citation_validity"] for r in rows
                                    if r.get("citation_validity") is not None]),
        "uncited_paragraphs_per_answer": _mean([r["uncited_count"] for r in rows
                                                if r.get("uncited_count") is not None]),
        "p95_ms": p95,
        "notes": notes,
    }
