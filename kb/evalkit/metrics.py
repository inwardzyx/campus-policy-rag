# -*- coding: utf-8 -*-
"""指标：**注册表** + 逐题判分。新增指标 = 加一条 `register_metric()`，runner 不用改。

## 每个指标必须自带三样说明
`desc`（算什么）、`unit`（rate/ms/chars/count）、`missing`（**拿不到时是什么、为什么**）。
最后一列是这套设计的关键：报告里出现 `null` 时，消费方不用去翻代码猜"是没算还是真的是 0"。

## 缺失 ≠ 零（贯穿始终）
`context_recall` 没有 trace 就是 `None`；`nDCG` 没装 ir_measures 就是 `None` 并附 note；
`citation_validity` 没有引用报告就是 `None`。任何"补 0"都会让退化看起来像正常。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from kb import attribution
from kb.eval_runner import wilson_interval

# ---------------------------------------------------------------- 基础工具

def _ctx(response: Mapping[str, Any]) -> dict[str, Any] | None:
    ctx = response.get("context")
    return ctx if isinstance(ctx, dict) else None


def _included(ctx: Mapping[str, Any] | None) -> set[tuple[str, str]]:
    if not ctx:
        return set()
    return {(str(b.get("source")), str(b.get("clause"))) for b in (ctx.get("blocks") or [])}


def _citation_field(ctx: Mapping[str, Any] | None, key: str):
    report = (ctx or {}).get("citation") or {}
    return report.get(key)


def _mean(values: Sequence[float]) -> float | None:
    vals = [float(v) for v in values if v is not None]
    return round(sum(vals) / len(vals), 4) if vals else None


def _rate(rows: Sequence[Mapping[str, Any]], typ: str,
          ok_buckets: Sequence[str]) -> dict[str, Any]:
    subset = [r for r in rows if r["type"] == typ]
    n = len(subset)
    k = sum(1 for r in subset if r["bucket"] in ok_buckets)
    lo, hi = wilson_interval(k, n) if n else (None, None)
    return {"passed": k, "total": n,
            "rate": round(k / n, 4) if n else None,
            "wilson_95": [lo, hi] if n else None}


def _ndcg(rows: Sequence[Mapping[str, Any]], cases: Sequence[Mapping[str, Any]],
          k: int = 3) -> tuple[float | None, str]:
    """按 sources 顺序算 nDCG@k（二值相关）。**用 ir-measures，不自研**。"""
    try:
        import ir_measures
        from ir_measures import nDCG
    except Exception as exc:                      # noqa: BLE001
        return None, f"nDCG@{k} 未算：ir_measures 不可用（{type(exc).__name__}）：{exc}"

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
        run[qid] = {str(doc): float(len(docs) - i) for i, doc in enumerate(docs)}
    if not qrels:
        return None, f"nDCG@{k} 未算：没有可算的 answer 题（gold 缺失）"
    got = ir_measures.calc_aggregate([nDCG @ k], qrels, run)
    value = got.get(nDCG @ k)
    return (round(float(value), 4) if value is not None else None), ""


# ---------------------------------------------------------------- 逐题判分

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
        "scenario": case.get("scenario"),
        "question": case.get("question"),
        "bucket": attr["bucket"],
        "reason": attr["reason"],
        "context_available": attr["context_available"],
        "knowledge_hit": bool(response.get("knowledge_hit", False)),
        "took_ms": response.get("took_ms"),
        "context_recall": recall,
        "context_precision": precision,
        "citation_validity": _citation_field(ctx, "validity"),
        "uncited_count": _citation_field(ctx, "uncited_count"),
        "included": len(included),
        "sources": [s.get("doc") for s in (response.get("sources") or [])],
        "compression": (ctx or {}).get("compression"),
        # ★ 会话级评测要用：改写后的检索词（只来自 trace）、gold（对齐用）、会话键。
        "rewritten_query": (ctx or {}).get("rewritten_query"),
        "gold": list(gold) if gold else None,
        "session_id": (ctx or {}).get("session_id"),
        "turn_index": (ctx or {}).get("turn_index"),
        # 留一段答案（截断）：报告要能被人逐条抽验，离线复算也要有输入
        "answer": (response.get("answer") or "")[:500],
    }


# ---------------------------------------------------------------- 注册表

@dataclass(frozen=True)
class MetricSpec:
    name: str                       # 可用 `{k}` 之类占位符（由 aggregate 的 opts 填）
    desc: str
    unit: str                       # rate | ms | chars | count | dict
    missing: str                    # 拿不到时的含义（写给人看）
    compute: Callable[[Sequence[Mapping[str, Any]], Sequence[Mapping[str, Any]],
                       Mapping[str, Any]], tuple[Any, str | None]]


METRICS: list[MetricSpec] = []


def register_metric(spec: MetricSpec, *, replace: bool = True) -> MetricSpec:
    """注册一个指标；同名默认替换（幂等，重复 import 不会越积越多）。"""
    global METRICS
    if replace:
        METRICS = [m for m in METRICS if m.name != spec.name]
    METRICS.append(spec)
    return spec


def metric_specs(opts: Mapping[str, Any] | None = None) -> dict[str, dict[str, str]]:
    """报告用的"指标说明书"：每个指标的名字/口径/缺失含义。"""
    o = dict(opts or {})
    return {m.name.format(**o) if o else m.name:
            {"desc": m.desc, "unit": m.unit, "missing": m.missing}
            for m in METRICS}


def aggregate(rows: Sequence[Mapping[str, Any]], cases: Sequence[Mapping[str, Any]],
              *, k: int = 3) -> dict[str, Any]:
    """跑一遍注册表 + 归因分布。任何单个指标炸了都只让它自己变 None，不带崩整份报告。"""
    opts = {"k": k}
    out: dict[str, Any] = {"n_cases": len(rows)}
    notes: list[str] = []

    for spec in METRICS:
        try:
            value, note = spec.compute(rows, cases, opts)
        except Exception as exc:                  # noqa: BLE001
            value, note = None, f"{spec.name} 计算失败（{type(exc).__name__}）：{exc}"
        out[spec.name.format(**opts)] = value
        if note:
            notes.append(note)
    if not any(x.startswith("nDCG") for x in notes):
        notes.append(f"nDCG@{k} 由 ir-measures 现算（二值相关，键=doc｜clause）")

    buckets: dict[str, int] = {}
    for r in rows:
        buckets[r["bucket"]] = buckets.get(r["bucket"], 0) + 1
    out["buckets"] = dict(sorted(buckets.items(), key=lambda kv: -kv[1]))
    out["failure_buckets"] = {b: buckets.get(b, 0) for b in attribution.FAILURE_BUCKETS}
    out["notes"] = notes
    return out


# ---------------------------------------------------------------- 默认指标集

def _m_answer(rows, cases, o):
    return _rate(rows, "answer", ["ok"]), None


def _m_refuse(rows, cases, o):
    return _rate(rows, "refuse", ["refuse_ok"]), None


def _m_deny(rows, cases, o):
    return _rate(rows, "deny", ["ok"]), None


def _m_recall(rows, cases, o):
    return _mean([r["context_recall"] for r in rows
                  if r.get("context_recall") is not None]), None


def _m_precision(rows, cases, o):
    return _mean([r["context_precision"] for r in rows
                  if r.get("context_precision") is not None]), None


def _m_ndcg(rows, cases, o):
    return _ndcg(rows, cases, k=int(o.get("k", 3)))


def _m_budget_cut(rows, cases, o):
    ans = [r for r in rows if r["type"] == "answer"]
    if not ans:
        return None, None
    return round(sum(1 for r in ans if r["bucket"] == "budget_cut") / len(ans), 4), None


def _m_citation(rows, cases, o):
    return _mean([r["citation_validity"] for r in rows
                  if r.get("citation_validity") is not None]), None


def _m_uncited(rows, cases, o):
    return _mean([r["uncited_count"] for r in rows
                  if r.get("uncited_count") is not None]), None


def _m_p95(rows, cases, o):
    times = sorted(int(r["took_ms"]) for r in rows if r.get("took_ms") is not None)
    if not times:
        return None, None
    return times[max(0, int(round(0.95 * (len(times) - 1))))], None


def _m_compress_active(rows, cases, o):
    return sum(1 for r in rows if r.get("compression")), None


def _m_compress_dedup(rows, cases, o):
    return sum(int((r.get("compression") or {}).get("dedup_dropped") or 0) for r in rows), None


for _spec in (
    MetricSpec("answer", "应答类题目的正确率（bucket=ok）", "rate",
               "该套件没有 answer 题时为 None", _m_answer),
    MetricSpec("refuse", "库外题的正确拒答率（bucket=refuse_ok）", "rate",
               "该套件没有 refuse 题时为 None", _m_refuse),
    MetricSpec("deny", "越权题的正确拒绝率（bucket=ok）", "rate",
               "该套件没有 deny 题时为 None", _m_deny),
    MetricSpec("context_recall", "gold 条款是否进了真正喂给模型的块", "rate",
               "无 trace（docs/ 旧路径）或没有 gold 时为 None", _m_recall),
    MetricSpec("context_precision", "喂进去的块里 gold 的占比（单 gold 粗口径）", "rate",
               "无 trace 或没有 gold 时为 None", _m_precision),
    MetricSpec("ndcg@{k}", "按 sources 顺序算的 nDCG（二值相关）", "rate",
               "未装 ir_measures（评测依赖）时为 None，并在 notes 说明", _m_ndcg),
    MetricSpec("budget_cut_rate", "answer 题里 gold 被预算裁掉的比例", "rate",
               "没有 answer 题时为 None", _m_budget_cut),
    MetricSpec("citation_validity", "合法引用编号 / 全部引用编号", "rate",
               "没有引用报告（未启用引用或旧路径）时为 None", _m_citation),
    MetricSpec("uncited_paragraphs_per_answer", "每答案平均「未标注段落」数", "count",
               "没有引用报告时为 None", _m_uncited),
    MetricSpec("p95_ms", "端到端耗时 p95", "ms", "没有 took_ms 时为 None", _m_p95),
    MetricSpec("compression_rows_active", "压缩启用的题数（0 = 这批没触发压缩）", "count",
               "压缩默认关，恒为 0 属于正常", _m_compress_active),
    MetricSpec("compression_dedup_total", "压缩去重丢弃的块数", "count",
               "压缩默认关，恒为 0 属于正常", _m_compress_dedup),
):
    register_metric(_spec)
