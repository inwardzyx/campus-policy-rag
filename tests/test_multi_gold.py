# -*- coding: utf-8 -*-
"""多条款 gold（多跳题）的判据。

## 背景（第一次真实闭环 accept 的前提）
导入的 52 题里 39 题引用 2 条以上条款（"多跳合成"）。如果 gold 只认第一条：
- 命中第二条会被判 `retrieval_miss`（假失败）；
- 召回/精度/nDCG 全部偏低。

所以 `gold_pairs()` / `normalize_case()` / `score_row()` / 归因必须**同口径**。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kb import attribution                          # noqa: E402
from kb.evalkit.cases import normalize_case         # noqa: E402
from kb.evalkit.metrics import score_row            # noqa: E402


def test_gold_pairs_accepts_three_shapes() -> None:
    assert attribution.gold_pairs({"gold": ["a.md", "第一条"]}) == [("a.md", "第一条")]
    assert attribution.gold_pairs({"gold": [["a.md", "第一条"], ["a.md", "第二条"]]}) == [
        ("a.md", "第一条"), ("a.md", "第二条")]
    assert attribution.gold_pairs({"expected_sources": [["a.md", "第一条"]]}) == [("a.md", "第一条")]
    assert attribution.gold_pairs({}) == []


def test_normalize_case_keeps_all_gold_pairs() -> None:
    case = normalize_case({"id": "x", "type": "answer",
                           "gold": [["a.md", "第一条"], ["a.md", "第二条"]]})
    assert len(case["gold"]) == 2, "多跳题的第二条 gold 不能在入口被压掉"
    single = normalize_case({"id": "y", "type": "answer", "gold": ["a.md", "第一条"]})
    assert single["gold"] == ["a.md", "第一条"]


def test_classify_ok_when_second_gold_hit() -> None:
    case = {"id": "m1", "type": "answer",
            "gold": [["a.md", "第一条"], ["a.md", "第二条"]]}
    resp = {"answer": "x[1]", "knowledge_hit": True,
            "context": {"blocks": [{"source": "a", "clause": "第二条"}],
                        "citation": {"used_ids": [1]}}}
    assert attribution.classify(case, resp)["bucket"] == "ok"


def test_recall_precision_over_all_gold_pairs() -> None:
    case = {"id": "m2", "type": "answer",
            "gold": [["a.md", "第一条"], ["a.md", "第二条"]]}
    resp = {"answer": "x[1]", "knowledge_hit": True,
            "sources": [{"doc": "a｜第二条", "clause": None}],
            "context": {"blocks": [{"source": "a", "clause": "第二条"}],
                        "citation": {"used_ids": [1]}}}
    row = score_row(case, resp)
    assert row["context_recall"] is True
    assert row["context_precision"] == 0.5, "2 条 gold 命中 1 条"
