# -*- coding: utf-8 -*-
"""阶段 4 前置实测：ToolNode 碰到【不在清单里的工具名】会怎样？

★★★ 为什么要先验这个，不能直接写 LangGraph 版：

  本项目的负例工具 `get_my_grades` 【故意不在 schema 里】，
  手写版（probe_loop.run_one）对它的处理是：
      fn = TOOL_IMPL.get(tc["name"])   # 返回 None
      → 走负例分支，回一句"没有名为 get_my_grades 的工具"
  ⇒ 模型看得到这句话，于是它知道"这能力没有"，可以如实告诉用户。

  而 LangGraph 的 ToolNode 有个 handle_tool_errors 参数，
  默认值是一个函数（不是 False）。如果它的默认行为是【抛异常】：
      · 异常会冒泡出整张图 → invoke() 直接失败
      · 模型【看不到任何错误文本】→ 它不知道工具不存在
      · ⇒ 负例题全部失效，而且失败方式很难看（整批挂掉，不是答错）

  而如果默认是【把错误转成 ToolMessage 回给模型】：
      · 行为和手写版一致，但要确认文案是不是"模型看得懂"的
      · 文案如果是 "Error: tool not found" 这种英文裸串，
        中文模型能不能理解并照做，得实测

  ⇒ 这不是"读文档能确认"的事，是必须跑一遍才知道的事。

跑法：
    <虚拟环境>/Scripts/python.exe mcp/probe_toolnode_errors.py
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.environ["LANGSMITH_TRACING"] = "false"

from langchain_core.messages import AIMessage, ToolCall  # noqa: E402
from langgraph.prebuilt import ToolNode                # noqa: E402


def make_ai_with(call: dict) -> AIMessage:
    """伪造一条"模型说要调某个工具"的消息。

    ★ 为什么伪造而不是真的调 LLM：
      要测的是【ToolNode 拿到这个调用之后干什么】，
      模型那一步已经由 test_server_protocol / probe_loop 验过了。
      把模型从链路里摘掉，变量只剩一个 —— 这是实验设计的常识，
      但它和项目里"一次只验证一个新东西"是同一条原则。
    """
    return AIMessage(content="", tool_calls=[ToolCall(name=call["name"],
                                                     args=call["args"],
                                                     id=call["id"])])


def probe(label: str, tools: list, call: dict, **node_kwargs):
    """造一个 ToolNode 跑一次，看它抛异常还是返回文本。

    ★★★ 第一版这个函数直接 node.invoke(state)，五种情况全报同一个错：
        ValueError: Missing required config key 'N/A' for 'tools'
      那个错和 ToolNode 的错误处理毫无关系 —— 是我的测法坏了
      （ToolNode 依赖图执行时注入的 config，单独调用拿不到）。
      ⇒ 而"五项全抛异常"这个汇总看起来非常像结论，
        顺着它就能写出"ToolNode 默认行为是抛异常"这种完全错误的判断。
      ⇒ 教训和项目里那个坑同源：**判据坏了，测出来的一切都是假的，
        而坏的判据往往产出极其像结论的输出。**

      修法：把 ToolNode 放进一张真图里，用 app.invoke() 跑。
      这样 config 是框架自己注入的，和真实运行路径一致。
    """
    from langgraph.graph import END, START, StateGraph
    from langchain_core.messages import HumanMessage
    from typing_extensions import TypedDict

    print(f"\n--- {label} ---")
    print(f"    模型要调：{call['name']}({call['args']})")

    class S(TypedDict):
        messages: list

    def fake_agent(state: S) -> S:
        # 伪造"模型这一步"：直接产出那条带 tool_calls 的消息。
        # ★ 不真的调 LLM：要测的是 ToolNode 拿到调用之后干什么。
        return {"messages": state["messages"] + [make_ai_with(call)]}

    g = StateGraph(S)
    g.add_node("agent", fake_agent)
    g.add_node("tools", ToolNode(tools, **node_kwargs))
    g.add_edge(START, "agent")
    g.add_edge("agent", "tools")      # 无条件走进 tools，让它面对这个调用
    g.add_edge("tools", END)
    app = g.compile()

    state = {"messages": [HumanMessage(content="占位问题")]}
    try:
        out = app.invoke(state, config={"recursion_limit": 10})
        msgs = [m for m in out["messages"] if type(m).__name__ == "ToolMessage"]
        print(f"    ✅ 图跑完了，得到 {len(msgs)} 条 ToolMessage")
        for m in msgs:
            print(f"        content={m.content!r}")
            print(f"        tool_call_id={getattr(m, 'tool_call_id', None)!r}  "
                  f"status={getattr(m, 'status', None)!r}")
        return ("ok", msgs)
    except Exception as e:  # noqa: BLE001
        print(f"    ❌ 抛异常了：{type(e).__name__}: {str(e)[:200]}")
        return ("raise", e)


def main():
    from langchain_core.tools import StructuredTool

    def _ok(query: str) -> str:
        """真实存在的工具。"""
        return f"假装查到了：{query}"

    real_tool = StructuredTool.from_function(
        func=_ok, name="search_policy",
        description="检索学校政策原文。用于需要条款出处的场景。",
    )

    print("=" * 66)
    print("问题 1：ToolNode 遇到【清单里没有的工具名】（= 负例 get_my_grades 的情形）")
    print("=" * 66)

    kind, payload = probe(
        "默认 handle_tool_errors（就是不传，用框架默认值）",
        [real_tool],
        {"name": "get_my_grades", "args": {"student_id": "2023"}, "id": "call_1"},
    )
    verdict_default = kind

    print("\n" + "=" * 66)
    print("问题 2：参数类型不对（框架把参数校验算不算 tool error）")
    print("=" * 66)
    probe(
        "参数缺了（args 里没有 query）",
        [real_tool],
        {"name": "search_policy", "args": {"wrong_key": "x"}, "id": "call_2"},
    )

    print("\n" + "=" * 66)
    print("问题 3：工具实现内部抛异常（模拟 RAG 服务挂了）")
    print("=" * 66)

    def _boom(query: str) -> str:
        """会炸的工具。"""
        raise RuntimeError("连不上 RAG 服务")

    boom = StructuredTool.from_function(
        func=_boom, name="ask_policy", description="会炸的工具")

    kind3, payload3 = probe(
        "工具内部 RuntimeError",
        [boom],
        {"name": "ask_policy", "args": {"question": "x"}, "id": "call_3"},
    )

    print("\n" + "=" * 66)
    print("问题 4：显式设 handle_tool_errors=True 能不能拿到文本")
    print("=" * 66)
    probe(
        "handle_tool_errors=True",
        [real_tool],
        {"name": "get_my_grades", "args": {"student_id": "2023"}, "id": "call_4"},
        handle_tool_errors=True,
    )

    print("\n" + "=" * 66)
    print("问题 5：自定义文案（能不能塞中文的 NO_HIT 风格提示）")
    print("=" * 66)
    kind5, payload5 = probe(
        "handle_tool_errors=自定义字符串",
        [real_tool],
        {"name": "get_my_grades", "args": {"student_id": "2023"}, "id": "call_5"},
        handle_tool_errors="【这个工具不存在】我没有这个能力，请如实告诉用户，不要编造数据。",
    )

    print("\n" + "=" * 66)
    print("阶段 4 的结论（这几条直接决定 LangGraph 版怎么写）")
    print("=" * 66)
    if verdict_default == "raise":
        print("★ 默认行为会【抛异常】⇒ 必须显式传 handle_tool_errors，")
        print("  否则负例题会让整张图失败（不是答错，是崩），负例测试直接失效。")
    else:
        print(f"★ 默认行为返回消息（不是抛异常），文案如下 —— 要判断模型能不能照做：")
        if isinstance(payload, list):
            for m in payload:
                print(f"    {m.content!r}")
    print()
    print(f"  参数错误 → {'返回文本' if kind == 'ok' else '抛异常'}")
    print(f"  工具内部异常 → {'返回文本' if kind3 == 'ok' else '抛异常'}")
    print(f"  自定义文案 → {'可用' if kind5 == 'ok' else '不可用'}")


if __name__ == "__main__":
    main()
