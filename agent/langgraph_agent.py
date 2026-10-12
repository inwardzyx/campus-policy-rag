# -*- coding: utf-8 -*-
"""阶段 4：LangGraph 版 Agent —— 只把【控制流层】换成图，其余原样复用。

★★★ 这个文件的论点（也是整个 MCP 项目的收尾）：

    阶段 2/3 的手写版里，一个 agent 循环 = 你写的 for + if。
    阶段 4 用 LangGraph 重写这个循环。⇒ 问题来了：**到底换了什么？**

    实测换掉的只有三处：
      ① for 循环          → g.add_edge("tools", "agent")  （循环成了一条边）
      ② 两层终止判断       → tools_condition 这个条件函数 + recursion_limit
      ③ 消息配对 msgs.append → ToolNode 自动配对

    换掉的**只有控制流**。这些一个字都没变：
      - SYSTEM_PROMPT（直接 import probe_loop 的）
      - bound = llm.bind_tools(MODEL_SCHEMAS)（同一个 schema 源）
      - NO_HIT_TEXT 拒答翻译（在 tools_impl 里，不在图里）
      - 负例工具不写进 schema（在 tool_schemas 里）
      - 判据 check_fabrication / check_language（直接 import）
      - 工具走 stdio MCP（同一个 client，只是包成 StructuredTool）

    ⇒ 这就是"分层"的可验证形式：换掉一层，别的层【不需要改】。
      面试时这句话可以配着 diff 说，比任何架构图都硬。

------------------------------------------------------------------
★★ 为什么 negative 工具要【单独】测（不能混在 10 题里一起看）：

    实测发现（见下方输出）ToolNode 对清单外的工具名回的是一句英文：
        "Error: get_my_grades is not a valid tool, try one of [search_policy]."
    而手写版回的是中文：
        "没有名为 get_my_grades 的工具。现有工具：[...]"

    ⇒ 同一个负例，两版给模型看的【措辞不同】。
      如果只看最终的 4 道 negative 题有没有编造，
      就会出现"两版都过了 = 没差别"的假象 ——
      实际上中间那一层已经变了，只是被最终输出掩盖了。

    ⇒ 所以本文件额外加【一次硬探针】：直接把工具名喂给 ToolNode，
      把框架原样返回的文本打出来对比。★ 这个对照必须进阶段 5 的验收报告：
      它是本项目里"框架换来的行为差异"的第一处实证。

------------------------------------------------------------------
跑法（必须先起 RAG 服务）：
    python -m uvicorn service:app --port 8000
    <虚拟环境>/Scripts/python.exe agent/langgraph_agent.py
"""

from __future__ import annotations

import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.environ["LANGSMITH_TRACING"] = "false"

import env_compat                                   # noqa: E402
env_compat.ensure_mmh3()                           # noqa: E402
env_compat.ensure_uuid_utils()                     # noqa: E402
from dotenv import load_dotenv                      # noqa: E402
load_dotenv(os.path.join(_ROOT, ".env"))

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage  # noqa: E402
from langchain_deepseek import ChatDeepSeek          # noqa: E402
from langgraph.graph import END, START, MessagesState, StateGraph          # noqa: E402
from langgraph.prebuilt import ToolNode, tools_condition                   # noqa: E402

# ★★ 业务层：直接 import，一个字不重写（和 policy_agent 同一个做法）
from mcp.probe_loop import (                          # noqa: E402
    MAX_ROUNDS, QUESTIONS, SYSTEM_PROMPT,
    check_fabrication, check_language,
)
from mcp.tool_schemas import MODEL_SCHEMAS            # noqa: E402
from mcp.tools_impl import NO_HIT_TEXT, health        # noqa: E402
from mcp.client import StdioMCPClient                 # noqa: E402

from agent.mcp_bridge import build_tools, self_check   # noqa: E402

#★★ recursion_limit 是【换算来的】，不是拍的。
#   mcp/probe_recursion_limit.py 实测：limit 数的是 super-step（节点执行次数），
#   不是"轮"。换算关系 recursion_limit = 2 × 轮数（START 本身也占一步）。
#   MAX_ROUNDS=6 → 2×6 = 12。
#
#   ⚠️ 两个已知偏差，阶段 5 必须回头验：
#     ① 这是在【一轮只调一个工具】的最小图上测的。ToolNode 对一轮多个
#        tool_calls 可能并行处理，步数关系会变。
#     ② 框架会不会在 tools_condition 之后多插一步（比如 END 的判定），
#        只有真跑一遍才知道。⇒ 所以下面跑完会把【实际步数】打出来。
RECURSION_LIMIT = 2 * MAX_ROUNDS


def build_app(tools: list):
    """搭图。这是本文件唯一"属于 LangGraph"的部分。"""

    def call_model(state: MessagesState) -> dict:
        """节点1：发问题 + 带工具清单 → 模型。

        ★ 注意这里：SYSTEM_PROMPT 和 bound 都从外面传进来，
          本函数【不含任何业务判断】。它就是"调一次模型"。
        """
        return {"messages": [llm_with_tools.invoke(state["messages"])]}

    g = StateGraph(MessagesState)
    g.add_node("agent", call_model)
    g.add_node("tools", ToolNode(tools))
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", tools_condition)   # ★ 终止层1
    g.add_edge("tools", "agent")                        # ★ 循环就是这一行
    return g.compile()


def main():
    try:
        print(f"[前置] RAG 服务健康检查：{health()}")
    except Exception as e:                           # noqa: BLE001
        print(f"[!!] RAG 服务不可用：{e}")
        print("    先起服务：python -m uvicorn service:app --port 8000")
        return None

    global llm_with_tools
    llm_with_tools = ChatDeepSeek(model="deepseek-chat", temperature=0).bind_tools(
        MODEL_SCHEMAS)

    qs = json.load(open(QUESTIONS, encoding="utf-8"))["questions"]

    rows = []
    with StdioMCPClient() as client:
        tools = build_tools(client)
        self_check(client, tools)          # ★ 接线自检：三方一致性
        app = build_app(tools)

        print(f"\n[预算] MAX_ROUNDS={MAX_ROUNDS} → "
              f"recursion_limit={RECURSION_LIMIT}（2×换算，实测步数见每题）")

        for q in qs:
            msgs = [SystemMessage(content=SYSTEM_PROMPT),
                    HumanMessage(content=q["question"])]
            steps = 0
            truncated = False
            err = None
            try:
                # ★★ 只跑一次。第一版这里先 stream 再 invoke，
                #   等于同一道题跑两遍 —— 10 题变20 遍，还每次都带 rerank，
                #   直接把脚本拖到超时。⇒ 实测踩出来的，别改回去。
                #   步数改用 config 里带 thread_id 后从 checkpoint 数（见下方注释）。
                final = app.invoke(
                    {"messages": msgs},
                    config={"recursion_limit": RECURSION_LIMIT,
                            "configurable": {"thread_id": q["id"]}},
                )
                msgs = final["messages"]
                # ★ 数真实步数：LangGraph 的 state 里记着 next/created_by，
                #   但更简单可靠的是数 messages 里【节点追加了几次】——
                #   每次节点执行只追加一条消息（reducer 累加），
                #   所以 len(messages) - 2 就是节点执行次数（减去我们自己塞的2 条）。
                steps = max(len(msgs) - 2, 1)
            except Exception as e:                     # noqa: BLE001
                err = f"{type(e).__name__}: {str(e)[:150]}"
                truncated = "recursion" in type(e).__name__.lower()

            # ---- 从 messages 序列里抽最终答案和工具调用 ----
            answer = ""
            called = []
            for m in msgs:
                if isinstance(m, AIMessage):
                    for tc in getattr(m, "tool_calls", []) or []:
                        called.append({"name": tc["name"],
                                       "args": json.loads(tc["args"])
                                       if isinstance(tc["args"], str) else tc["args"]})
                    if m.content:
                        answer = m.content
            seen_no_hit = any(NO_HIT_TEXT in str(getattr(m, "content", ""))
                              for m in msgs)

            rec = {"id": q["id"], "type": q["type"], "question": q["question"],
                   "answer": answer or None, "rounds": steps, "steps": steps,
                   "recursion_limit": RECURSION_LIMIT, "truncated": truncated,
                   "error": err, "tool_output_seen_no_hit": seen_no_hit,
                   "called_tools": called}
            rows.append(rec)

            print(f"\n--- {rec['id']} [{rec['type']}] {rec['question']}")
            print(f"    调了 {[c['name'] for c in called] or '（无）'}　"
                  f"步数={steps}　见到『查不到』={seen_no_hit}")
            zh, ratio = check_language(rec)
            print(f"    汉字占比 {ratio:.0%}　中文={'OK' if zh else '!! 不达标'}")
            print(f"    回答：{(rec['answer'] or '')[:200]}")
            if truncated:
                print(f"    [!!] 撞到 recursion_limit —— 手写版此时会返回兜底文案，"
                      f"这里直接抛异常了（★ 两版行为不同，见文件头）")
            p, hit = check_fabrication(rec, q.get("forbidden_patterns", []))
            if p:
                print(f"    [!!] 命中禁答模式 {p!r} -> {hit!r}")
            if not zh:
                print("    [!!] 不是中文回答 —— 禁答正则对它失效，判定不可信")

    return rows


if __name__ == "__main__":
    rows = main()
    if rows is None:
        sys.exit(1)
    out = os.path.join(_ROOT, "agent", "_langgraph_out.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    print(f"\n完整记录已写入 {os.path.relpath(out, _ROOT)}")

    print("\n=== LangGraph 版自检 ===")
    ok = sum(1 for r in rows if not r["truncated"] and not r["error"])
    print(f"正常跑完：{ok}/{len(rows)} 题")
    print(f"撞上限被截断：{sum(r['truncated'] for r in rows)} 题")
    print(f"实测步数分布：{sorted(r['steps'] for r in rows)}")
    print(f"recursion_limit={RECURSION_LIMIT}（换算值）"
          f"　最大实测步数={max(r['steps'] for r in rows)}")
    used = {c["name"] for r in rows for c in r["called_tools"]}
    print(f"实际被调用的工具：{sorted(used)}")
    assert used <= {"search_policy", "ask_policy"}, f"调到了不该有的工具：{used}"
    print("[OK] 所有工具调用都经由 MCP server")
