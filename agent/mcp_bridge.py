# -*- coding: utf-8 -*-
"""把 MCP client 包成 LangChain 工具，交给 LangGraph 的 ToolNode。

★★★ 这个文件是【整条链路里唯一的转接头】，所以它很短，但每一步都有讲究。

    LLM (deepseek)  ──bind_tools──▶  决定调哪个工具、参数是什么
          │
          ▼
    LangGraph ToolNode  ──调──▶  本文件：StructuredTool 对象
          │  （框架只认工具对象，不认我的 client）
          ▼
    _run_search_policy(**kwargs)  ──▶  McpToolBridge.call(名字, kwargs)
          │
          ▼
    StdioMCPClient.call_tool  ──JSON-RPC over stdin──▶  mcp/server.py
          │                                              （另一个进程）
          ▼
    tools_impl → HTTP 127.0.0.1:8000 → RAG 检索/生成

------------------------------------------------------------------
★★★ 实测出来的三件事（都不是读文档能知道的，见 mcp/probe_toolnode_errors.py）：

① ToolNode 遇到【不在清单里的工具名】默认【不抛异常】，而是回一条 ToolMessage：
     "Error: get_my_grades is not a valid tool, try one of [search_policy]."
   status='error'。⇒ 负例测试在 LangGraph 版里天然能跑通 —— 但
   ⇒ 这条文案是英文裸串，模型能不能照做、会不会因此改口说英文，**必须实测**。
     这就是本文件下方 run_once() 要回答的问题之一。

② StructuredTool.from_function 默认 infer_schema=True，
   它从【函数的类型标注】生成参数 schema —— 不是从我的 SOURCE_TOOLS。
   实测踩到：函数签名写 query，模型按 ask_policy 的 schema 传 question，
   于是报 "query: Field required" —— **报错内容跟真正原因对不上，很有迷惑性**。
   ⇒ 所以这里显式 infer_schema=False + 用 pydantic 模型从 SOURCE_TOOLS 生成，
     参数名的唯一真相是唯一源，不是我的函数签名。

③ args_schema 要的是 pydantic 模型，传 dict 会 AttributeError。
   且 Field(..., description=...) 的 description 必须用关键字，
   写成 Field("中文") 是把中文当成【默认值】，description 会变 None。

------------------------------------------------------------------
★★ 为什么 description 一律从 SOURCE_TOOLS 取，不写进函数 docstring：
   docstring 会随代码编辑器/格式化工具被改、被删、被挪位置；
   而 description 是模型选工具的唯一依据，它一变模型行为就变。
   ⇒ 放在唯一源里，tool_schemas.py 的自检（两种形状逐字一致）才能守住它。

跑法（必须先起 RAG 服务）：
    python -m uvicorn service:app --port 8000
    <虚拟环境>/Scripts/python.exe agent/langgraph_agent.py
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from langchain_core.tools import StructuredTool     # noqa: E402
from pydantic import Field, create_model            # noqa: E402

from mcp.client import MCPError, StdioMCPClient      # noqa: E402
from mcp.tool_schemas import SOURCE_TOOLS           # noqa: E402


# ------------------------------------------------------------------
# 参数 schema：从唯一源生成，不从函数签名生成
# ------------------------------------------------------------------


def build_args_model(src: dict):
    """把 SOURCE_TOOLS 里的一�� props 变成 pydantic 输入模型。

    ★★ 实测坑：Field 的 description 必须用关键字写。
      Field("检索词") 是把"检索词"当成【默认值】，description 会变 None。
      （字段默认 None → 参数说明静默丢失 → 模型少一条信息，且没有任何报错。）
    """
    fields: dict[str, Any] = {}
    for pname, pspec in src["props"].items():
        if pname in src["required"]:
            ann = (str, Field(..., description=pspec["desc"]))
        else:
            ann = (Optional[str], Field(default=None, description=pspec["desc"]))
        fields[pname] = ann
    return create_model(f"{src['name']}_Input", **fields)


class McpToolBridge:
    """把手写 MCP client 桥接成"给 ToolNode 用的函数"。

    ★ 为什么需要桥：ToolNode 会用关键字参数调工具
      （tool.invoke({"query": "..."}) → func(**kwargs)），
      而 client.call_tool 收的是一个 dict。签名不兼容，必须包一层。
      —— 与 policy_agent.McpCaller 是同一个问题的两种解法：
         那边是把 client 伪装成 dict，这边是把 dict 展开成 kwargs。
    """

    def __init__(self, client: StdioMCPClient):
        self.client = client

    def make(self, name: str):
        """造一个【接受 kwargs、返回文本】的函数。"""
        caller = self.client.call_tool

        def _run(**kwargs) -> str:
            # ★★★ 错误处理必须在这一层【和手写版保持一致】：
            #   tools/call 正常返回但 isError=True  → client.call_tool 照实返回文本，
            #     模型看到"这个工具调不通"，可以换思路或如实告诉用户。
            #   MCPError（协议坏了、server 崩了）→ 也转成文本，
            #     ★ 不让异常冒泡出 ToolNode，否则整张图失败，
            #       模型什么都学不到，而且失败方式很难看（整批挂，不是答错）。
            #     这与手写版 run_one 里 ToolError→文本 的处理是同一原则。
            try:
                return caller(name, kwargs)
            except MCPError as e:
                return f"工具暂时不可用（协议层出错）：{e}"

        _run.__name__ = name
        return _run


def build_tools(client: StdioMCPClient) -> list[StructuredTool]:
    """从 SOURCE_TOOLS 生成 LangChain 工具清单。"""
    bridge = McpToolBridge(client)
    tools = []
    for src in SOURCE_TOOLS:
        tools.append(
            StructuredTool.from_function(
                func=bridge.make(src["name"]),
                name=src["name"],
                description=src["desc"],           # ★ 唯一源
                args_schema=build_args_model(src),  # ★ 唯一源
                infer_schema=False,                 # ★ 别读函数签名
            )
        )
    return tools


def self_check(client: StdioMCPClient, tools: list[StructuredTool]):
    """接线自检：工具清单三方一致（唯一源 / server 报 / 建出来的）。

    ★ 为什么必须三方比：任何一处漂了，模型看到的和它能调的就对不上，
      而 ToolNode 对"清单外的工具名"只会回一句英文 error，
      不会告诉你"你的 schema 写错了" —— 表现是模型莫名开始调不存在的工具。
    """
    print("=== 接线自检（三方一致性）===")
    src_names = [t["name"] for t in SOURCE_TOOLS]
    srv_names = [t["name"] for t in client.tools]
    built = [t.name for t in tools]
    print(f"  唯一源 SOURCE_TOOLS : {src_names}")
    print(f"  server 报的 tools/list: {srv_names}")
    print(f"  建出来的 LangChain 工具: {built}")
    assert src_names == srv_names == built, f"三方不一致：{src_names} / {srv_names} / {built}"
    print("  → 三方一致 OK")

    for t in tools:
        src = next(s for s in SOURCE_TOOLS if s["name"] == t.name)
        assert t.description == src["desc"], f"{t.name}：description 不是来自唯一源"
        got_params = list(t.args_schema.model_fields)
        want_params = list(src["props"])
        assert got_params == want_params, \
            f"{t.name}：参数名 {got_params} 与唯一源 {want_params} 不一致"
        # 参数说明也必须在（第一版把 Field("中文") 写成了默认值，description 变 None）
        for p in want_params:
            d = t.args_schema.model_fields[p].description
            assert d == src["props"][p]["desc"], f"{t.name}.{p} 的参数说明丢了或被改了"
        print(f"  → {t.name}：参数 {got_params}、描述、参数说明全部来自唯一源")

    print("\n=== 实调一次两个工具（确认参数名真的对得上）===")
    tools_by_name = {t.name: t for t in tools}
    for src in SOURCE_TOOLS:
        args = {p: "请假 审批" for p in src["props"]}
        out = tools_by_name[src["name"]].invoke(args)
        print(f"  {src['name']}({args}) → {out[:110]}...")


if __name__ == "__main__":
    with StdioMCPClient() as c:
        ts = build_tools(c)
        self_check(c, ts)
