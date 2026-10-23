# -*- coding: utf-8 -*-
"""kb/eval_runner.py —— 独立评测集的打分器（不生成题，只负责跑与算）

★ 与 kb/eval.py 的分工：
  · kb/eval.py  = 自动 smoke（self-hit / 拒答 / ACL 泄漏），**不是独立评测**；
  · 本文件      = 跑人工出的独立题集，按题面预期判分。
  两者不能互相冒充：smoke 是部署前体检，独立集是“出题人没看过代码与结果”的证据。

题集格式见 evalset/enterprise_eval_template.md。
"""

from __future__ import annotations

import math
from typing import Callable


def wilson_interval(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """二项比例的 Wilson 95% 区间；n=0 时返回 (0,0) 并调用方自己标缺失。"""
    if n <= 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (round(max(0.0, center - margin), 4), round(min(1.0, center + margin), 4))


def score_case(case: dict, response: dict) -> bool:
    """按题型判分。

    answer：expected_sources 里的每个子串，都要能在某条 source 的 doc 里找到；
            找不到 = 没答到点上（不是“答案措辞不一样”的容错，独立集就该严）。
    refuse：库外问题，必须 knowledge_hit=False 且没有 sources。
    deny  ：越权问题，表现与 refuse 相同（不能返回任何 source）。
    """
    typ = case.get("type", "answer")
    sources = response.get("sources") or []
    if typ == "answer":
        expected = case.get("expected_sources") or []
        if not expected:
            return False
        return any(all(term in str(s.get("doc", "")) for term in expected) for s in sources)
    if typ in ("refuse", "deny"):
        return (not response.get("knowledge_hit", True)) and not sources
    raise ValueError(f"未知题型：{typ!r}（只支持 answer/refuse/deny）")


def run_cases(cases: list[dict], search: Callable[[dict], dict]) -> list[dict]:
    """逐题调用 search(case)，返回带判分的结果；单题异常不拖垮整批。"""
    results = []
    for case in cases:
        try:
            resp = search(case)
            passed = score_case(case, resp)
            results.append({"id": case.get("id"), "type": case.get("type", "answer"),
                            "passed": passed, "response": resp})
        except Exception as exc:
            results.append({"id": case.get("id"), "type": case.get("type", "answer"),
                            "passed": False, "error": str(exc)})
    return results


def summarize(results: list[dict]) -> dict:
    total = len(results)
    passed = sum(1 for r in results if r.get("passed"))
    by_type: dict[str, dict] = {}
    for r in results:
        t = r.get("type", "answer")
        d = by_type.setdefault(t, {"total": 0, "passed": 0})
        d["total"] += 1
        d["passed"] += 1 if r.get("passed") else 0
    for d in by_type.values():
        d["rate"] = round(d["passed"] / d["total"], 4) if d["total"] else None
        d["wilson_95"] = wilson_interval(d["passed"], d["total"])
    lo, hi = wilson_interval(passed, total)
    return {
        "total": total,
        "passed": passed,
        "rate": round(passed / total, 4) if total else None,
        "wilson_95": [lo, hi],
        "by_type": by_type,
        "failures": [r for r in results if not r.get("passed")],
    }
