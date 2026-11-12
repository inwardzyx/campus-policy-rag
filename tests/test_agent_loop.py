# -*- coding: utf-8 -*-
"""agent/loop.py 的判据 —— 三条"不许静默" + 一条"没重写"的证据。

★ 为什么这些必须是测试而不是手跑：
  Step 1 的产品承诺有三条（不丢来源、撞上限不静默、不超预算），
  每一条在坏情况下都**看起来正常**（有答案、有 sources、跑完了），只有用例能把它挑出来。

★ `test_matches_probe_loop` 是"没重写循环"的证据：
  同一组 fake 响应，分别喂给 `probe_loop.run_one` 与 `agent.loop.run_loop`，
  逐步比对轮数 / 每轮调用的工具名 / 最终答案。任何一处漂移都会红。
"""
import os
import sys

os.environ["LANGSMITH_TRACING"] = "false"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from types import SimpleNamespace                     # noqa: E402

import pytest                                          # noqa: E402

import mcp.probe_loop as probe_loop                    # noqa: E402
from agent.loop import run_loop                        # noqa: E402
from mcp.tools_impl import NO_HIT_TEXT, _format_sources, _signal_line   # noqa: E402


# ---------------------------------------------------------------- 假模型 / 假工具
def _tool_call(name: str, args: dict, cid: str = "c1", tokens: int = 10):
    return SimpleNamespace(content="", tool_calls=[{"name": name, "args": args, "id": cid}],
                           usage_metadata={"total_tokens": tokens})


def _final(text: str, tokens: int = 10):
    return SimpleNamespace(content=text, tool_calls=[], usage_metadata={"total_tokens": tokens})


class _FakeBound:
    """按脚本依次返回；脚本用光就抛错（说明用例没设计好，而不是模型行为）。"""

    def __init__(self, script):
        self.script = list(script)
        self.invoked = 0

    def invoke(self, msgs):
        self.invoked += 1
        assert self.script, "脚本用光了：用例设计有问题"
        return self.script.pop(0)


def _hit_tool(**kwargs) -> str:
    return _format_sources([{"doc": "学生纪律处分管理规定.md", "text": "第三条 …", "score": 0.8123}]) \
        + _signal_line("hit", "search_policy", 1)


def _miss_tool(**kwargs) -> str:
    return NO_HIT_TEXT + _signal_line("no_hit", "search_policy")


# ---------------------------------------------------------------- 判据
def test_done_with_tool_keeps_sources():
    """正常收尾：一步工具 + 最终答案 ⇒ done、有 sources、status=hit。"""
    rec = run_loop("请假谁批", llm=_FakeBound([_tool_call("search_policy", {"query": "请假"}),
                                              _final("由辅导员批。")]),
                   tools={"search_policy": _hit_tool})
    assert rec["stopped_reason"] == "done"
    assert rec["steps"] and rec["steps"][0]["status"] == "hit"
    assert rec["steps"][0]["n_src"] == 1
    assert len(rec["sources"]) == 1 and rec["sources"][0]["doc"].endswith(".md")
    assert rec["refused"] is False


def test_no_hit_is_refused():
    """工具说"库里没有"且没有一次命中 ⇒ refused=True（这条是拒答不退化的一半）。"""
    rec = run_loop("图书馆几点关门", llm=_FakeBound([_tool_call("search_policy", {"query": "图书馆"}),
                                                   _final("查不到。")]),
                   tools={"search_policy": _miss_tool})
    assert rec["no_hit_seen"] is True
    assert rec["refused"] is True


def test_max_rounds_is_not_silent():
    """撞轮数上限必须留下痕迹：stopped_reason + 兜底文案（不许给一段"看起来完整"的答案）。"""
    script = [_tool_call("search_policy", {"query": "q"}, cid=f"c{i}") for i in range(3)]
    rec = run_loop("绕圈子的问题", llm=_FakeBound(script), tools={"search_policy": _hit_tool},
                   max_rounds=2)
    assert rec["stopped_reason"] == "max_rounds"
    assert rec["rounds"] == 2
    assert "上限" in rec["answer"], rec["answer"]


def test_budget_stops_before_next_round():
    """预算在**下一轮开始前**判：第一轮就超预算 ⇒ 不再进第二轮。"""
    fake = _FakeBound([_tool_call("search_policy", {"query": "q"}, tokens=50),
                       _final("不该走到这里")])
    rec = run_loop("问题", llm=fake, tools={"search_policy": _hit_tool}, budget_tokens=10)
    assert rec["stopped_reason"] == "budget"
    assert fake.invoked == 1, "超预算后还多调了一次模型"
    assert "预算" in rec["answer"]


def test_unknown_tool_does_not_crash():
    """模型幻觉出不存在的工具名 ⇒ 回一句错误继续，不许 KeyError 崩（probe_loop 同款）。"""
    rec = run_loop("问题", llm=_FakeBound([_tool_call("delete_everything", {}), _final("好")]),
                   tools={"search_policy": _hit_tool})
    assert rec["steps"][0]["error"] == "unknown_tool"
    # ★ 一次都没成功 + 有过错误 ⇒ tool_error，不是 done。
    #   2026-11-12 实测踩到：工具连错端口全部失败，答案是一段"服务连接失败"的优雅话术，
    #   状态却是 done —— 前端会把它画成一条绿色的成功路径。
    assert rec["stopped_reason"] == "tool_error"


def test_matches_probe_loop(monkeypatch):
    """★ 没重写循环的证据：同一脚本，run_loop 与 probe_loop.run_one 逐步一致。"""
    q = {"id": "t1", "type": "answer", "question": "请假谁批"}
    script = [_tool_call("search_policy", {"query": "请假"}),
              _tool_call("ask_policy", {"question": "请假谁批"}, cid="c2"),
              _final("由辅导员批。")]
    tools = {"search_policy": _hit_tool, "ask_policy": _hit_tool}

    monkeypatch.setattr(probe_loop, "TOOL_IMPL", tools)
    old = probe_loop.run_one(q, _FakeBound(script))
    new = run_loop(q["question"], llm=_FakeBound(script), tools=tools)

    assert new["rounds"] == old["rounds"], (new["rounds"], old["rounds"])
    assert new["answer"] == old["answer"]
    old_seq = [c["name"] for row in old["trace"] for c in row.get("called", [])]
    assert [st["tool"] for st in new["steps"]] == old_seq

def test_no_tool_call_is_not_done():
    """模型一次工具都没调就作答 ⇒ no_tool + refused（无证据 = 视为拒答）。

    ★ 2026-11-12 冒烟实测：8 道超范围的题里有 2 道模型直接凭常识答了，
      /chat 那边是拒答的 —— 不拦这一条，agent 的拒答率会凭空掉一半。
    """
    rec = run_loop("图书馆几点关门", llm=_FakeBound([_final("应该是晚上十点。")]),
                   tools={"search_policy": _hit_tool})
    assert rec["stopped_reason"] == "no_tool"
    assert rec["refused"] is True
    assert rec["steps"] == []
