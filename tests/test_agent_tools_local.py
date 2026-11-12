# -*- coding: utf-8 -*-
"""`agent/tools_local.py` 的对账：**模型看到的文本**必须与 MCP 那条路逐字节一致。

## 为什么这条测试重要

两条工具路径（MCP 独立进程 / `/agent` 进程内）如果各写一份渲染，
"模型看到的东西"迟早会分叉 —— 而分叉的症状不是报错，是**分数变了**，
归因起来要几天。所以这里只认一个判据：同一组 sources，两份输出**字符串相等**。

★ 不测"调用模型"：那是冒烟脚本（`agent/smoke_agent.py`）的事。
  单元测试只钉住"渲染与信号行"这两个纯函数边界 —— 它们便宜、可重复、能进 CI。
"""
import os
import sys
from types import SimpleNamespace

os.environ["LANGSMITH_TRACING"] = "false"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import pytest                                          # noqa: E402

import service as svc                                  # noqa: E402
from agent import tools_local                          # noqa: E402
from mcp.tools_impl import NO_HIT_TEXT, _format_sources, _signal_line   # noqa: E402


def _ready_rag(monkeypatch):
    """把 `rag.ready` 置真（不加载模型）—— 工具只在未就绪时抛 ToolError。"""
    monkeypatch.setattr(svc, "rag", SimpleNamespace(ready=True), raising=False)


def test_search_policy_renders_like_mcp(monkeypatch):
    """命中时：`tools_local.search_policy` 的输出 == MCP 路径的同一份渲染。"""
    _ready_rag(monkeypatch)
    srcs = [SimpleNamespace(doc="学生纪律处分管理规定.md｜第十八条", text="违规电器清单……", score=0.8123),
            SimpleNamespace(doc="学生请假管理办法.md｜第五条", text="请假审批……", score=0.7411)]
    monkeypatch.setattr(svc, "handle_chat",
                        lambda req, roles=None: SimpleNamespace(sources=srcs, knowledge_hit=True))
    out = tools_local.search_policy("违规电器怎么处分？")
    expected = _format_sources([{"doc": s.doc, "text": s.text, "score": s.score} for s in srcs]) \
        + _signal_line("hit", "search_policy", len(srcs))
    assert out == expected


def test_search_policy_no_hit_uses_same_refusal_text(monkeypatch):
    """未命中时：拒答文案与信号行也必须来自同一处（不许各写一句"查不到"）。"""
    _ready_rag(monkeypatch)
    monkeypatch.setattr(svc, "handle_chat",
                        lambda req, roles=None: SimpleNamespace(sources=[], knowledge_hit=False))
    out = tools_local.search_policy("图书馆几点关门？")
    assert out == NO_HIT_TEXT + _signal_line("no_hit", "search_policy")


def test_tool_failure_raises_not_returns(monkeypatch):
    """工具失败必须**抛** ToolError（不是回一段"服务连接失败"的文字让模型照抄）。

    ★ 2026-11-12 踩过：第一版复用 `mcp/tools_impl`，它把 HTTP 失败转成文字返回，
      于是两次调用全失败、响应却是 200+done，答案是一段"后台服务未启动"的优雅话术。
    """
    _ready_rag(monkeypatch)

    def boom(req, roles=None):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(svc, "handle_chat", boom)
    with pytest.raises(tools_local.ToolError):
        tools_local.search_policy("任意问题")


def test_ask_policy_no_hit_when_knowledge_miss(monkeypatch):
    """`ask_policy` 的拒答口径：`knowledge_hit=False` ⇒ NO_HIT + no_hit 信号（不是把成答喂回去）。"""
    _ready_rag(monkeypatch)
    monkeypatch.setattr(svc, "handle_chat",
                        lambda req, roles=None: SimpleNamespace(answer="", sources=[], knowledge_hit=False))
    out = tools_local.ask_policy("图书馆几点关门？")
    assert out == NO_HIT_TEXT + _signal_line("no_hit", "ask_policy")
