# -*- coding: utf-8 -*-
"""onboarding 纯逻辑：探针生成、判分（含 `/search` 的单字段来源）、汇总。

## 盯的四条
  ① 探针确定性（同 seed 同结果）、(doc, clause) 不重复、跳过"第N段"这种分段标签；
  ② 判分要兼容两种来源形状：`/chat` 的 `{doc, clause}` 与 `/search` 的 `doc｜clause`；
  ③ 不命中就是 False（不许因为"有来源"就算命中）；
  ④ 汇总的 p95 / 命中率按定义算，空输入是 None（缺失 ≠ 零）。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kb import onboard as ob                    # noqa: E402


def _chunks():
    return [
        {"doc": "A.md", "clause": "第一条", "text": "学校秉承中国立场世界眼光理念，坚持立德树人。"},
        {"doc": "A.md", "clause": "第二条", "text": "学生应当遵守课堂纪律，按时完成作业与考试。"},
        {"doc": "A.md", "clause": "第3段", "text": "这是没有条款结构的分段块，不应该被选中。"},
        {"doc": "B.md", "clause": "第五条", "text": "教职工享有依法参加培训与进修的权利，学校应保障经费。"},
        {"doc": "B.md", "clause": "第五条", "text": "重复的条款号只应出现一次，这里给它足够长度以便走到去重分支。"},
    ]


def test_probes_are_deterministic_unique_and_skip_segments() -> None:
    # n 大于唯一条款数 ⇒ 返回全部，去重与否一眼可见（否则 sample 可能只抽到一条重复项）
    a = ob.build_probes(_chunks(), n=50, seed=7)
    b = ob.build_probes(_chunks(), n=50, seed=7)
    assert [x["question"] for x in a] == [x["question"] for x in b], "同 seed 必须同结果"

    keys = [tuple(x["gold"]) for x in a]
    assert len(keys) == len(set(keys)), "同一 (doc, clause) 只能出一条"
    assert all(not (k[1].startswith("第") and k[1].endswith("段")) for k in keys)
    assert len(a) == 3, f"应只从 3 个真条款里抽：{keys}"


def test_probe_question_carries_content_snippet() -> None:
    """条款号不在索引里，问句必须带正文片段才检索得到（2026-10-26 实测）。"""
    for p in ob.build_probes(_chunks(), n=3, seed=7):
        assert "「" in p["question"] and "…" in p["question"]


def test_score_probe_accepts_both_source_shapes() -> None:
    probe = {"question": "q", "gold": ["A.md", "第一条"]}
    chat_like = {"sources": [{"doc": "A.md", "clause": "第一条"}], "knowledge_hit": True,
                 "context": {"citation": {"validity": 1.0}}, "took_ms": 100}
    search_like = {"sources": [{"doc": "A.md｜第一条"}], "knowledge_hit": True,
                   "context": {"citation": {"validity": 1.0}}, "took_ms": 100}
    assert ob.score_probe(probe, chat_like)["hit"] is True
    assert ob.score_probe(probe, search_like)["hit"] is True, "`doc｜clause` 必须拆开再比"


def test_score_probe_miss_is_false() -> None:
    probe = {"question": "q", "gold": ["A.md", "第一条"]}
    resp = {"sources": [{"doc": "A.md", "clause": "第二条"}], "knowledge_hit": True,
            "context": {"citation": {"validity": 1.0}}, "took_ms": 100}
    assert ob.score_probe(probe, resp)["hit"] is False


def test_summarize_rates_and_p95() -> None:
    rows = [{"hit": True, "validity": 1.0, "took_ms": t} for t in (100, 200, 300, 400)]
    rows.append({"hit": False, "validity": None, "took_ms": 500})
    s = ob.summarize(rows)
    assert s["n"] == 5 and s["hit"] == 4 and s["hit_rate"] == 0.8
    assert s["p95_ms"] == 500 and len(s["misses"]) == 1


def test_summarize_empty_is_missing_not_zero() -> None:
    s = ob.summarize([])
    assert s["n"] == 0 and s["hit_rate"] is None and s["p95_ms"] is None
