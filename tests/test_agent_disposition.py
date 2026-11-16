# -*- coding: utf-8 -*-
"""`agent/disposition.py` 的判据：**每种收尾都要有一个可解释的处置**。

★ 为什么这条要单独测：`kind` 决定了"这次算不算有效工作"。
  分错的症状不是报错，是**统计口径悄悄变了**（比如把 degraded 算成 answered，
  成功率就会虚高）—— 这正是 super-agent 那种"每条决策带 reason code"要防的事。
"""
import os
import sys

os.environ["LANGSMITH_TRACING"] = "false"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import pytest                                                    # noqa: E402

from agent.disposition import classify, to_line                   # noqa: E402


def _rec(**kw):
    base = {"stopped_reason": "done", "refused": False, "sources": [{"doc": "a.md"}],
            "steps": [], "tokens": 0}
    base.update(kw)
    return base


def test_answered_needs_sources():
    d = classify(_rec())
    assert d["kind"] == "answered" and d["code"] == "done"


def test_done_without_sources_is_degraded_not_answered():
    """跑完但零来源 ⇒ degraded。★ 这条最容易分错：状态是 done，但答案没有证据。"""
    d = classify(_rec(sources=[]))
    assert d["kind"] == "degraded"
    assert d["code"] == "done_no_source"


def test_gate_refused_is_refused():
    d = classify(_rec(stopped_reason="gate_refused", refused=True, sources=[]))
    assert d["kind"] == "refused"
    assert d["code"] == "gate_refused"


@pytest.mark.parametrize("code", ["tool_error", "rewrite_limit", "budget", "max_rounds"])
def test_degraded_codes(code):
    d = classify(_rec(stopped_reason=code, sources=[]))
    assert d["kind"] == "degraded"
    assert d["code"] == code
    assert d["detail"]


def test_unknown_state_is_error_not_success():
    """未知收尾 ⇒ error。★ 未知当成功 = 假绿，宁可变红。"""
    d = classify(_rec(stopped_reason="something_new"))
    assert d["kind"] == "error"


def test_line_is_readable():
    d = classify(_rec(stopped_reason="gate_refused", refused=True, sources=[]))
    assert "refused(gate_refused)" in to_line(d)
