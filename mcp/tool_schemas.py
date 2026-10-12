# -*- coding: utf-8 -*-
"""工具描述的唯一来源（single source of truth），以及它到两种形状的转换。

★ 为什么用两个转换函数，而不是把两种形状各写一份：
  写两份必然会漂 —— 改了一处忘了另一处，你手里的 schema 就自相矛盾了，
  而模型看到矛盾的描述会做出不可预测的选择。
  ⇒ 只维护这份 SOURCE，转出来的那两份永远一致。

★★ 为什么 `description` 里写「不做什么」比写「做什么」更重要（2026-10-12）：
  两个工具功能相近（都查校规），模型区分它们靠的不是名字，是描述里的**否定句**。
  如果只写"检索政策""回答政策问题"，模型挑哪个都是对的 ⇒ **选择测试就没有区分度**，
  测出来的"选对率"是假的（跟"总数守恒、分布变了"那类假象同源）。
  ⇒ 阶段 0 那10 道题里 s1-s3 期望 search_policy、s4-s5 期望 ask_policy，
    如果 schema 不划边界，这两组会互相串，而串了你也不知道是模型的锅还是描述的锅。
"""

from __future__ import annotations

# ==================================================================
# 一份描述，两种形状
# ==================================================================
SOURCE_TOOLS: list[dict] = [
    {
        "name": "search_policy",
        "desc": (
            "检索学校政策【原文条款】，返回条款文本、文件名、条款号和相似度分数。\n"
            "适用于：需要引用规定怎么写、需要条款出处、需要逐条比对多个规定。\n"
            "不适用于：用户想要一段可以直接引用的完整答案——那种情况用 ask_policy。\n"
            "【重要】库里只有学校公开制度，不含任何个人数据（成绩、绩点、消费记录等）。"
        ),
        "props": {
            "query": {
                "type": "string",
                "desc": (
                    "检索词，2-5 个关键词用空格分隔，不要把整句问话塞进来。\n"
                    "例：'请假 审批'、'综合测评 算分'、'奖学金 材料'\n"
                    "口语说法先转成制度用语（礼拜→周、坏了→保修）效果更好。"
                ),
            },
        },
        "required": ["query"],
    },
    {
        "name": "ask_policy",
        "desc": (
            "就一个完整问题给出【带出处的成答】，内部会做检索、精排和拒答判断。\n"
            "适用于：用户想要一段能直接读的答案（'我要怎么走流程''怎么算分'），"
            "或者问题需要综合多条规定。\n"
            "不适用于：需要逐条引用原文或指定条款号时——那种情况用 search_policy。\n"
            "【重要】如果库里没有依据，它会明确说没有。此时不要自己补，"
            "也不要拿 search_policy 的结果硬拼一个答案。"
        ),
        "props": {
            "question": {
                "type": "string",
                "desc": "用户的完整问题原话，不要改写、不要摘要。",
            },
        },
        "required": ["question"],
    },
]


def to_model_schema(t: dict) -> dict:
    """转成 llm.bind_tools() 要的形状（喂给模型，决定它选不选这个工具）。"""
    return {
        "type": "function",
        "function": {
            "name": t["name"],
            "description": t["desc"],
            "parameters": {
                "type": "object",
                "properties": t["props"],
                "required": t["required"],
            },
        },
    }


def to_mcp_schema(t: dict) -> dict:
    """转成 MCP tools/list 要的形状（喂给 MCP client / 别的 agent）。"""
    return {
        "name": t["name"],              # ★ 直接是 name，没有 "function" 嵌套
        "description": t["desc"],
        "inputSchema": {                 # ★ 叫 inputSchema，不是 parameters
            "type": "object",
            "properties": t["props"],
            "required": t["required"],
        },
    }


MODEL_SCHEMAS = [to_model_schema(t) for t in SOURCE_TOOLS]
MCP_SCHEMAS = [to_mcp_schema(t) for t in SOURCE_TOOLS]

#=================================================================
# ★★ 这里【故意】没有 get_my_grades
#
#   模型只能调用"被告知存在的工具"。不告诉它，它就不会正常去调——
#   于是我们测的变成：**当被问到能力边界时，它会怎么做**：
#     ① 老实说"我没有查成绩的能力"     ← 期望
#     ② 拿 search_policy 去查（查不到）  ← 选择错，但至少没编造
#     ③ 自己编一个成绩出来              ← 真正要测的失败
#   ⇒ 负例不写进 schema，要靠 evalset/agent_probe_questions.json
#     里的 negative 题去问它。
# ==================================================================


if __name__ == "__main__":
    import json
    import sys

    print("=== 模型看到的（bind_tools）===")
    print(json.dumps(MODEL_SCHEMAS[0], ensure_ascii=False, indent=2))
    print("\n=== MCP tools/list 返回的 ===")
    print(json.dumps(MCP_SCHEMAS[1], ensure_ascii=False, indent=2))

    # ---- 自检：两份描述必须逐字一致（漂了就响）----
    ok = True
    for src, mdl, mcp in zip(SOURCE_TOOLS, MODEL_SCHEMAS, MCP_SCHEMAS):
        assert src["name"] == mdl["function"]["name"] == mcp["name"], \
            f"{src['name']}：两种形状的名字对不上"
        assert src["desc"] == mdl["function"]["description"] == mcp["description"], \
            f"{src['name']}：描述在转换过程中被改动了（漂了）"
    print("\n[OK] 两种形状的名字与描述逐字一致" if ok else "\n[!!] 不一致")

    # ---- 自检：必须恰好两个工具，且没有负例工具混进来 ----
    names = [s["function"]["name"] for s in MODEL_SCHEMAS]
    assert names == ["search_policy", "ask_policy"], f"工具清单不对：{names}"
    print(f"[OK] 模型只被告知 {len(names)} 个工具：{names}")

    sys.exit(0)
