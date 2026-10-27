# -*- coding: utf-8 -*-
"""P0 判据：答案值校验（`kb/values.py`）+ 拒答细分桶（`kb/attribution.py`）。

## 盯的四条
  ① 抽值要认单位同义（小时/时、天/日）；没有单位的数字保留但由调用方过滤；
  ② 值命中是**弱判据**：答案出现任一期望值即算命中；没配 expect 时必须是 None（缺失≠0）；
  ③ 拒答题"答了"要分两类：**引用过块**（越界）vs **没引用**（疑似编造）；
  ④ 报告里的 failure_buckets 必须包含新桶（否则归因面板看不见）。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kb import attribution                       # noqa: E402
from kb.evalkit.metrics import _m_answer_value   # noqa: E402
from kb.values import extract, match             # noqa: E402


def test_extract_units_and_aliases() -> None:
    got = extract("每周不超过8小时，每月不超过40小时。")
    assert (8.0, "小时") in got and (40.0, "小时") in got
    assert (3.0, "天") in extract("请假三天由班主任审批")
    assert (3.0, "天") in extract("请假三日")


def test_match_any_expected_value() -> None:
    exp = [(8.0, "小时")]
    assert match("每周最多 8 小时", exp) is True
    assert match("每周 8 天", exp) is False
    assert match("随便答", []) is False, "没有判据时不能算命中"


def test_metric_missing_is_none_not_zero() -> None:
    rows = [{"type": "answer", "answer_value": None},
            {"type": "answer", "answer_value": True},
            {"type": "answer", "answer_value": False}]
    value, note = _m_answer_value(rows, [], {})
    assert value == 0.5
    only_none = [{"type": "answer", "answer_value": None}]
    value2, note2 = _m_answer_value(only_none, [], {})
    assert value2 is None and "缺失" in note2


def _resp(answer: str, used_ids: list[int] | None = None) -> dict:
    return {"answer": answer, "knowledge_hit": True,
            "context": {"citation": {"used_ids": used_ids or []}, "blocks": []}}


def test_refuse_answered_with_citations_is_overreach() -> None:
    case = {"id": "r1", "type": "refuse", "question": "讲师课时费多少？"}
    out = attribution.classify(case, _resp("根据规定，讲师每课时 200 元[1]。", [1]))
    assert out["bucket"] == "refuse_context_overreach"


def test_refuse_answered_without_citations_is_fabricated() -> None:
    case = {"id": "r2", "type": "refuse", "question": "讲师课时费多少？"}
    out = attribution.classify(case, _resp("讲师每课时 200 元。", []))
    assert out["bucket"] == "refuse_fabricated"


def test_refuse_with_refusal_text_still_ok() -> None:
    case = {"id": "r3", "type": "refuse", "question": "食堂几点开门？"}
    out = attribution.classify(case, {"answer": "资料中没有相关内容，我不知道。", "knowledge_hit": True})
    assert out["bucket"] == "refuse_ok"


def test_failure_buckets_include_refusal_splits() -> None:
    for b in ("refuse_fabricated", "refuse_context_overreach"):
        assert b in attribution.FAILURE_BUCKETS

def test_score_row_flags_wrong_value() -> None:
    """反向验证：值写错时必须判 False（否则这个指标只会在"恰好对"时亮）。"""
    from kb.evalkit.metrics import score_row

    case = {"id": "x1", "type": "answer", "question": "每年多少学时？",
            "gold": ["员工培训管理办法.md", "第四条"],
            "expect": ["12 学时"]}
    ctx = {"citation": {"used_ids": [1], "validity": 1.0, "uncited_count": 0},
           "blocks": [{"source": "员工培训管理办法.md", "clause": "第四条"}]}
    good = score_row(case, {"answer": "每年不少于 12 学时。", "knowledge_hit": True, "context": ctx})
    bad = score_row(case, {"answer": "每年不少于 8 学时。", "knowledge_hit": True, "context": ctx})
    assert good["answer_value"] is True
    assert bad["answer_value"] is False
