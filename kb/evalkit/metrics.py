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

from kb import attribution, categories
from kb.eval_runner import wilson_interval

# ---------------------------------------------------------------- 基础工具

def _ctx(response: Mapping[str, Any]) -> dict[str, Any] | None:
    ctx = response.get("context")
    return ctx if isinstance(ctx, dict) else None


def _included(ctx: Mapping[str, Any] | None) -> set[tuple[str, str]]:
    """真正喂给模型的块（**归一文档后缀**：默认语料带 .md，企业存储可能不带）。"""
    if not ctx:
        return set()
    return {attribution._key(b.get("source"), b.get("clause"))
            for b in (ctx.get("blocks") or [])}

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
        pairs = attribution.gold_pairs(case)
        if not pairs or str(case.get("type", "answer")) != "answer":
            continue
        qid = str(case.get("id"))
        qrels[qid] = {f"{attribution._norm_source(d)}｜{c}": 1 for d, c in pairs}
        row = next((r for r in rows if r["id"] == case.get("id")), None)
        keys = list(row.get("source_keys") or []) if row else []
        run[qid] = {key: float(len(keys) - i) for i, key in enumerate(keys)}
    if not qrels:
        return None, f"nDCG@{k} 未算：没有可算的 answer 题（gold 缺失）"
    got = ir_measures.calc_aggregate([nDCG @ k], qrels, run)
    value = got.get(nDCG @ k)
    return (round(float(value), 4) if value is not None else None), ""


# ---------------------------------------------------------------- 逐题判分

def _answer_value(case: Mapping[str, Any], response: Mapping[str, Any]) -> bool | None:
    """答案值校验：命中=None 表示**没有判据**（该题没配 expect），不是失败也不是通过。"""
    expect = case.get("expect") or []
    if not expect:
        return None
    import kb.values as values_mod

    expected = []
    for item in expect:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            expected.append((float(item[0]), str(item[1])))
        else:
            expected.extend(values_mod.extract(str(item)))
    if not expected:
        return None
    return values_mod.match(response.get("answer") or "", expected)


def _source_key(src: Mapping[str, Any]) -> str:
    """把一条 source 归一成 `doc｜clause`。

    ★ 两种形态都要认：`{doc, clause}` 分开的；以及 doc 里已经拼成 `doc｜clause` 的
      （chat 的展示用 sources 就是后者）——不认后者，nDCG 会拿 "doc｜None" 去比 gold。
    """
    doc = str(src.get("doc") or "")
    clause = src.get("clause")
    if clause is None and "｜" in doc:
        d, _, c = doc.partition("｜")
        return f"{attribution._norm_source(d)}｜{c}"
    return f"{attribution._norm_source(doc)}｜{clause}"


def score_row(case: Mapping[str, Any], response: Mapping[str, Any]) -> dict[str, Any]:
    """一题一行：判分 + 归因 + 上下文指标。所有字段都可能是 None（= 拿不到）。"""
    ctx = _ctx(response)
    included = _included(ctx)
    gold = attribution.gold_of(case)
    pairs = attribution.gold_pairs(case)
    gold_keys = [attribution._key(d, c) for d, c in pairs]
    attr = attribution.classify(case, response)
    typ = str(case.get("type", "answer"))

    recall = None
    precision = None
    if typ == "answer" and pairs:
        if ctx is None:
            recall = None          # 没有 trace 就没有"上下文召回"这回事，不补 0
        else:
            hits = sum(1 for k in gold_keys if k in included)
            # ★ 语义修正（盲审 P1-2）：
            #   recall    = 命中 gold 条数 / gold 总条数（多 gold 时是覆盖率，不是布尔）
            #   precision = 命中 gold 条数 / included 块数（"喂进去的块里 gold 占多少"）
            #   旧实现把 hits/len(gold) 当 precision，和描述不符、且聚合值系统性偏低。
            recall = (hits / len(gold_keys)) if gold_keys else None
            precision = (hits / len(included)) if included else None

    return {
        "id": case.get("id"),
        "type": typ,
        "category": categories.category_of(case),
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
        # ★ nDCG 要用 (doc, clause) 对齐；doc 归一后缀（企业存储可能不带 .md）
        "source_keys": [_source_key(s) for s in (response.get("sources") or [])],
        "compression": (ctx or {}).get("compression"),
        # ★ 会话级评测要用：改写后的检索词（只来自 trace）、gold（对齐用）、会话键。
        "rewritten_query": (ctx or {}).get("rewritten_query"),
        "gold": list(gold) if gold else None,
        "answer_value": _answer_value(case, response),
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

def _m_by_category(rows, cases, o):
    """分组指标：按题型分别给 Wilson —— 小样本时区间自动变宽。"""
    groups: dict[str, list] = {}
    for r in rows:
        groups.setdefault(str(r.get("category") or "other"), []).append(r)
    return {
        cat: {
            "n": len(sub),
            "answer": _rate(sub, "answer", ["ok"]),
            "refuse": _rate(sub, "refuse", ["refuse_ok"]),
            "deny": _rate(sub, "deny", ["ok"]),
            "answer_value": _m_answer_value(sub, [], {})[0],
        }
        for cat, sub in sorted(groups.items())
    }, None


def _m_answer(rows, cases, o):
    return _rate(rows, "answer", ["ok"]), None


def _m_answer_value(rows, cases, o):
    """值命中率（只统计**配了 expect** 的 answer 题；没配的不进分母）。"""
    vals = [r.get("answer_value") for r in rows
            if r.get("type") == "answer" and r.get("answer_value") is not None]
    if not vals:
        return None, "没有配 expect 的 answer 题 ⇒ 不计算值命中率（缺失≠0）"
    return round(sum(1 for v in vals if v) / len(vals), 4), None


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
    MetricSpec("by_category", "按题型分组的指标（每类各自给 Wilson）", "dict",
               "没有行时为空 dict", _m_by_category),
    MetricSpec("answer_value", "答案值命中率（只统计配了 expect 的题；缺失≠0）", "rate",
               "没有配 expect 的 answer 题 ⇒ 不计算（缺失≠0）", _m_answer_value),
    MetricSpec("answer", "应答类题目的**检索命中率**（bucket=ok；不是答案正确性）", "rate",
               "该套件没有 answer 题时为 None", _m_answer),
    MetricSpec("refuse", "库外题的正确拒答率（bucket=refuse_ok）", "rate",
               "该套件没有 refuse 题时为 None", _m_refuse),
    MetricSpec("deny", "越权题的正确拒绝率（bucket=ok）", "rate",
               "该套件没有 deny 题时为 None", _m_deny),
    MetricSpec("context_recall", "gold 覆盖率（命中 gold 条数 / gold 总条数）", "rate",
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
