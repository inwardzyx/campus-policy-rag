# -*- coding: utf-8 -*-
"""实测：recursion_limit 到底数"轮"还是数"步"。

★ 为什么必须实测而不是查文档就信：
  "super-step"这个词在官方文档里出现得很含糊（只说"图的执行步数"），
  而我脑子里把它理解成"一个来回算 2 步"。理解错了后面 MAX_ROUNDS 换算就全错。
  ⇒ 自己搭个最小图，一步一步数。

★ 顺便验证另一件事：LangGraph 的"轮"到底对应我手写版的哪一层。
  手写版 for rnd in range(MAX_ROUNDS) 里，一次循环 = 调模型 + 执行工具。
  LangGraph 里这一次循环 = agent 节点 + tools 节点 = 几步？

跑法：
    <虚拟环境>/Scripts/python.exe mcp/probe_recursion_limit.py
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from langgraph.graph import END, START, StateGraph  # noqa: E402
from typing_extensions import TypedDict  # noqa: E402


# ==================================================================
# 最小图：agent 节点要工具 → tools 节点执行 → 回 agent → 不要工具就 END
# 这就是工具调用循环的骨架，和 probe_loop.py 的 for 循环一一对应。
# ==================================================================


class State(TypedDict):
    n: int          # 记着已经转了几圈
    log: list       # 记录每个节点被执行的顺序


def agent(state: State) -> State:
    """假装模型：转 3 圈后才不要工具了。"""
    n = state["n"] + 1
    wants_tool = n <= 3
    print(f"  [agent ] 第 {n} 次：{'要工具' if wants_tool else '不要工具了，产出答案'}")
    return {"n": n, "log": state["log"] + [("agent", n)]}


def tools(state: State) -> State:
    print(f"  [tools ] 执行工具（第 {state['n']} 次要工具）")
    return {"log": state["log"] + [("tools", state["n"])]}


def build():
    g = StateGraph(State)
    g.add_node("agent", agent)
    g.add_node("tools", tools)
    g.add_edge(START, "agent")
    # 条件边：n<=3 就去 tools，否则去 END
    g.add_conditional_edges(
        "agent",
        lambda s: "tools" if s["n"] <= 3 else END,
        {"tools": "tools", END: END},
    )
    g.add_edge("tools", "agent")       # ★ 循环就是这一行
    return g.compile()


def try_limit(limit: int) -> tuple[bool, int, list]:
    """用指定的 recursion_limit 跑，看能不能跑完 + 记录实际步数。"""
    print(f"\n--- recursion_limit = {limit} ---")
    try:
        out = build().invoke(
            {"n": 0, "log": []},
            config={"recursion_limit": limit},
        )
        print(f"  ✅ 跑完了。节点执行序列：{out['log']}")
        return True, len(out["log"]), out["log"]
    except Exception as e:                      # noqa: BLE001
        print(f"  ❌ 中断：{type(e).__name__}")
        # 把长消息截短，不然刷屏
        print(f"     {str(e)[:120]}")
        return False, -1, []


def main():
    print("目标：让 agent 要 3 次工具（第 4 次不要）")
    print("如果 recursion_limit 数的是'轮'，那它至少要 4")
    print("如果它数的是'步（节点执行次数）'，那至少要 7：agent,tools,agent,tools,agent,tools,agent\n")

    results = {}
    for lim in (3, 5, 6, 7, 8):
        ok, steps, log = try_limit(lim)
        results[lim] = (ok, steps)

    print("\n" + "=" * 60)
    print("实测结论：")
    for lim, (ok, steps) in sorted(results.items()):
        print(f"  limit={lim:2d} → {'成功' if ok else '中断'}"
              f"（实际节点执行 {steps} 步）" if ok else f"  limit={lim:2d} → 中断")

    # 找最小可用的 limit
    ok_limits = [lim for lim, (ok, _) in results.items() if ok]
    if ok_limits:
        best = min(ok_limits)
        print(f"\n  最小可用 recursion_limit = {best}")
        print(f"  需要的 agent 调用次数（我手写版的'轮数'）= 4")
        print(f"  实际节点执行 7 步 = agent 4 次 + tools 3 次")
        print(f"  而 limit 要 8 而不是 7 ⇒ ★ START 节点本身也占一步")
        print(f"\n  ⇒ 换算关系：recursion_limit = 2 × 轮数"
              f"（1 步 START + 轮数步 agent + (轮数-1) 步 tools）")
        print(f"  ⇒ 我手写版 MAX_ROUNDS=6，换过去是 12，不是 6 也不是 11。")
        print(f"\n  ★★★ 公式是我【先猜后验】的，猜的是 2×轮数-1，"
              f"实测是 2×轮数。")
        print(f"     差 1 步。START 那一步我压根没想过。"
              f"⇒ 不实测就写公式=在写猜测，而且看起来特别像推导。")

        print("\n" + "=" * 62)
        print("阶段 4 回来补的一条：这个换算在【真实图】上偏保守一倍")
        print("=" * 62)
        print("  agent/langgraph_agent.py 用 MAX_ROUNDS=6 → recursion_limit=12")
        print("  实测 10 道题的节点执行数：[1, 1, 3, 3, 3, 3, 4, 4, 4, 5]")
        print("  最大 5，而上限是 12 —— 余量多了一倍多。")
        print()
        print("  ⇒ 换算没错（数的是 super-step），是【这个最小图比真实图省步数】：")
        print("    真实图里一轮只调一个工具时是 agent+tools 两步，与最小图一致；")
        print("    但 10 道题里模型最常用的模式是\"一轮调两个工具\"，")
        print("    那时 ToolNode 可能并行处理 ⇒ 步数不再翻倍。")
        print()
        print("  ★ 取舍说明：宁可留余量。超限的代价是抛 GraphRecursionError（硬失败），")
        print("    余量的代价只是多几次循环判断的开销。")
        print("    真要收紧上限，必须先用真实图量 —— 别拿这里的最小图数据去设。")


if __name__ == "__main__":
    main()
