# -*- coding: utf-8 -*-
"""阶段 4 的关键差异探针：同一个负例工具名，两版给模型看的文本逐字对比。

★★★ 这份脚本测的是【框架带来的行为差异】，而不是"两版谁更好"。

  背景：负例工具 get_my_grades 故意不在 schema 里。
  手写版 run_one 对它回中文：
      没有名为 get_my_grades 的工具。现有工具：['search_policy', 'ask_policy']。
  LangGraph ToolNode 对它回英文：
      Error: get_my_grades is not a valid tool, try one of [search_policy, ask_policy].

  ⇒ 两版在【最终答案】上可能完全一样（10 道题都过、都不编造），
    但【中间那层喂给模型的话】已经变了。
    这就是"只看端到端指标会漏掉什么"的一个具体样本：
      端到端指标一样 ⇒ 你会写"换框架没有影响" ⇒ 而实际上有一处确凿的差异。

  ⇒ 所以阶段 5 的验收报告必须包含这一项，它比"两版都 10/10"更有信息量。

★ 顺带验的第二件事：ToolNode 默认【不抛异常】，而是回一条 status='error' 的
  ToolMessage。所以负例测试在 LangGraph 版里天然能跑通。
  ⚠️ 但注意：这是 1.2.11 版本的实测行为，不是协议保证。换版本要重跑这个脚本。

跑法（必须先起 RAG 服务）：
    python -m uvicorn service:app --port 8000
    <虚拟环境>/Scripts/python.exe agent/probe_negative_text.py
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.environ["LANGSMITH_TRACING"] = "false"

from langchain_core.messages import (                # noqa: E402
    AIMessage, HumanMessage, ToolCall,
)
from langgraph.graph import END, START, StateGraph     # noqa: E402
from langgraph.prebuilt import ToolNode                # noqa: E402
from typing_extensions import TypedDict                # noqa: E402

from agent.mcp_bridge import build_tools                # noqa: E402
from mcp.client import StdioMCPClient                   # noqa: E402
from mcp.tool_schemas import SOURCE_TOOLS               # noqa: E402

NEGATIVE_TOOL = "get_my_grades"        # ★ 故意不在 SOURCE_TOOLS 里
# 手写版 probe_loop.run_one 里那一行的原文（保持逐字一致，别改成"差不多"的意思）
HANDWRITTEN_TEXT = (
    f"没有名为 {NEGATIVE_TOOL} 的工具。现有工具："
    f"{[t['name'] for t in SOURCE_TOOLS]}。"
)


class S(TypedDict):
    messages: list


def han_count(s: str) -> int:
    """汉字个数。★ 不算占比算个数：这里要回答的是"有没有中文"，
    不是"中文占多少" —— 0 和非 0 的差别是质变，占比只是程度。"""
    return sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff")


def probe_toolnode(tools: list) -> tuple[str, str | None]:
    """伪造"模型要调一个不存在的工具"，看 ToolNode 到底给回什么。

    ★ 为什么伪造模型那一步：变量只剩 ToolNode 一个。
      模型行为已经由 probe_loop / policy_agent / langgraph_agent 验过三轮了。
    """
    def fake_agent(state: S):
        return {"messages": state["messages"] + [
            AIMessage(content="", tool_calls=[ToolCall(
                name=NEGATIVE_TOOL, args={"student_id": "2023"}, id="c1")])]}

    g = StateGraph(S)
    g.add_node("agent", fake_agent)
    g.add_node("tools", ToolNode(tools))
    g.add_edge(START, "agent")
    g.add_edge("agent", "tools")
    g.add_edge("tools", END)

    try:
        out = g.compile().invoke(
            {"messages": [HumanMessage(content="查我的绩点")]},
            config={"recursion_limit": 10},
        )
    except Exception as e:                                  # noqa: BLE001
        return ("", f"{type(e).__name__}: {str(e)[:150]}")

    tm = [m for m in out["messages"] if type(m).__name__ == "ToolMessage"]
    if not tm:
        return ("", "ToolNode 一条 ToolMessage 都没返回")
    return (tm[0].content, getattr(tm[0], "status", None))


def main():
    with StdioMCPClient() as client:
        tools = build_tools(client)
        text, status = probe_toolnode(tools)

    print("=" * 68)
    print(f"同一个负例：模型幻觉出清单外的 {NEGATIVE_TOOL}")
    print("=" * 68)

    print("\n【手写版 probe_loop.run_one】")
    print(f"  {HANDWRITTEN_TEXT}")
    print(f"  汉字数={han_count(HANDWRITTEN_TEXT)}")

    print("\n【LangGraph ToolNode】")
    if text:
        print(f"  {text}")
        print(f"  status={status!r}   汉字数={han_count(text)}")
    else:
        print(f"  ❌ 没拿到文本：{status}")

    print("\n" + "=" * 68)
    print("逐项对照")
    print("=" * 68)
    print(f"  ToolNode 默认抛异常？        {'否 ✅（回的是文本）' if text else '是 ❌'}")
    same = (text == HANDWRITTEN_TEXT)
    print(f"  两版文本逐字相同？            {same}")
    print(f"  ToolNode 侧含中文？           {han_count(text) > 0}"
          f"（汉字数={han_count(text)}）")

    print("\n★ 结论（阶段 5 验收报告要写的三条）：")
    print("  ① ToolNode 默认【不抛异常】，回一条 status='error' 的 ToolMessage")
    print("     ⇒ 负例测试在 LangGraph 版里天然能跑通，不需要额外配"
          "handle_tool_errors")
    print("  ② 但它的文案是【全英文裸串】，手写版是中文且列出还剩哪些工具")
    print("     ⇒ 判据是中文正则（forbidden_patterns），口径不一致的风险在此")
    print("  ③ 两版【端到端指标一样】（10/10 都不编造）却已存在这处差异")
    print("     ⇒ 只看端到端会漏掉中间层的改变 —— 这是本项目最值得写的一条")

    assert text, "ToolNode 什么都没返回 —— 探针本身失效，先修探针再谈结论"
    assert han_count(HANDWRITTEN_TEXT) > 0, "手写版文案不该是纯英文（改错了？）"


if __name__ == "__main__":
    main()
