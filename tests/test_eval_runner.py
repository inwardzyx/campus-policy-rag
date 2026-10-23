# -*- coding: utf-8 -*-
"""独立评测打分器测试：只测判分与统计，不碰 HTTP/模型。"""

from __future__ import annotations

from kb.eval_runner import run_cases, score_case, summarize, wilson_interval


def test_answer_requires_all_expected_source_terms():
    case = {"type": "answer", "expected_sources": ["报销制度", "第三条"]}
    assert score_case(case, {"sources": [{"doc": "报销制度｜第三条"}]}) is True
    assert score_case(case, {"sources": [{"doc": "报销制度｜第四条"}]}) is False
    assert score_case(case, {"sources": []}) is False
    assert score_case({"type": "answer"}, {"sources": [{"doc": "x"}]}) is False


def test_refuse_and_deny_require_no_hit_and_no_sources():
    for typ in ("refuse", "deny"):
        case = {"type": typ}
        assert score_case(case, {"knowledge_hit": False, "sources": []}) is True
        assert score_case(case, {"knowledge_hit": True, "sources": [{"doc": "x"}]}) is False
        assert score_case(case, {"knowledge_hit": False, "sources": [{"doc": "x"}]}) is False


def test_wilson_interval_is_bounded_and_wide_for_small_n():
    lo, hi = wilson_interval(8, 10)
    assert 0 <= lo <= 0.8 <= hi <= 1
    assert hi - lo > 0.2          # n=10 的区间本来就很宽，不能报“80%”就完事
    assert wilson_interval(0, 0) == (0.0, 0.0)


def test_summarize_counts_by_type_and_lists_failures():
    results = [
        {"id": "A1", "type": "answer", "passed": True},
        {"id": "A2", "type": "answer", "passed": False},
        {"id": "R1", "type": "refuse", "passed": True},
    ]
    s = summarize(results)
    assert s["total"] == 3 and s["passed"] == 2
    assert s["by_type"]["answer"] == {"total": 2, "passed": 1, "rate": 0.5,
                                      "wilson_95": s["by_type"]["answer"]["wilson_95"]}
    assert [f["id"] for f in s["failures"]] == ["A2"]


def test_run_cases_catches_single_case_error():
    def search(case):
        if case["id"] == "bad":
            raise RuntimeError("boom")
        return {"sources": [{"doc": "报销制度｜第三条"}]}

    cases = [
        {"id": "ok", "type": "answer", "expected_sources": ["报销制度", "第三条"]},
        {"id": "bad", "type": "answer", "expected_sources": ["x"]},
    ]
    out = run_cases(cases, search)
    assert out[0]["passed"] is True
    assert out[1]["passed"] is False and "boom" in out[1]["error"]
