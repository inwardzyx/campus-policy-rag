# -*- coding: utf-8 -*-
"""agent/tools_local.py —— **进程内**工具实现（给 `/agent` 用，不走 MCP 跨进程）。

## 为什么需要这一份（以及它为什么不是"第二套工具"）

`mcp/tools_impl.py` 是给 **MCP server 那个独立进程**用的：它只能 HTTP 打回
`127.0.0.1:8000`。而 `/agent` 就活在 service 进程里，再去 HTTP 绕一圈有两个坏处：

1. **多一跳，且会连错**：2026-11-12 实测，服务起在 8031，工具却去打 8000（MCP 的默认端口），
   于是两次工具调用**全部失败**，而 agent 仍然回了 200 + `stopped_reason=done` ——
   "优雅失败"长得跟成功一样。**这就是这一步最大的收获：判据 1.1（sources 不许空）该早点跑。**
2. **必然有一件事不同步**：跨进程路径的 `sources` 是 JSON 反序列化回来的，进程内是对象；
   两份渲染若各写一遍，文本迟早不一致。

## 所以这里只做两件事

- **复用同一份渲染**：`mcp.tools_impl._format_sources` / `_signal_line` 直接 import，
  保证"模型看到的文本"与 MCP 路径**逐字节一致**（有 `tests/test_agent_tools_local.py` 对账）。
- **失败要抛**：进程内没有"HTTP 500 转成一段错误文本"那层，所以这里按 `ToolError` 抛，
  让 `agent/loop.py` 把它记成 `error`（前端标红），而不是喂给模型一段它可能照抄的失败描述。
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mcp.tools_impl import NO_HIT_TEXT, _format_sources, _signal_line   # noqa: E402  ★ 同一份渲染/同一句拒答


class ToolError(RuntimeError):
    """工具失败。与 `mcp.tools_impl.ToolError` 同名同义，但**这个会抛出去**（见模块说明）。"""


def search_policy(query: str, k: int = 5) -> str:
    """拿原文条款。★ 走 `handle_chat` 的**完整拒答门禁**（精排 < 5 分 = 库里没有）。

    ★★ 两份实现为什么都退回 `handle_chat`（2026-11-12 实测，两次踩坑）：
      第一版：裸 `rag.search`（top-5 直接喂）⇒ 拒答 4/8（/chat 是 8/8）；
      第二版：`handle_search`（向量闸 `_vec_reject`）⇒ 拒答 6/8。
      剩下的 2 题是"向量觉得有点像、精排给 0 分"的题 —— 而 /chat 的拒答闸是**精排分 < 5**
      （service.py:2716-2720）。要在这一步拿到同一道闸，只能走同一条管道。
    ⚠️ 代价：为了拿 sources，多花一次生成（约 700 token）。这是**先正确、再省 token** 的取舍；
       Step 3 可以给 `handle_chat` 加个 `no_generate=True` 参数把它省掉 —— 那要改 /chat，另开一次改动。
    """
    import service as svc

    if not svc.rag.ready:
        raise ToolError("模型还在加载（rag 未就绪）")
    try:
        resp = svc.handle_chat(svc.ChatRequest(question=query))
    except Exception as e:                                  # noqa: BLE001
        raise ToolError(f"检索失败：{type(e).__name__}: {e}") from e
    srcs = [{"doc": s.doc, "text": s.text, "score": float(s.score or 0.0)}
            for s in (resp.sources or [])]
    if not getattr(resp, "knowledge_hit", True) or not srcs:
        return NO_HIT_TEXT + _signal_line("no_hit", "search_policy")
    return _format_sources(srcs) + _signal_line("hit", "search_policy", len(srcs))


def ask_policy(question: str) -> str:
    """走完整管道拿成答：直接调 `handle_chat`（与 `/chat` 同一个入口，含拒答与引用）。"""
    import service as svc

    if not svc.rag.ready:
        raise ToolError("模型还在加载（rag 未就绪）")
    try:
        resp = svc.handle_chat(svc.ChatRequest(question=question))
    except Exception as e:                                  # noqa: BLE001
        raise ToolError(f"问答失败：{type(e).__name__}: {e}") from e
    srcs = getattr(resp, "sources", []) or []
    if not getattr(resp, "knowledge_hit", True):
        return NO_HIT_TEXT + _signal_line("no_hit", "ask_policy")
    text = getattr(resp, "answer", "") or ""
    return text + _signal_line("hit", "ask_policy", len(srcs))


#: 给 `run_loop(tools=...)` 用的清单。名字必须与 `mcp/tool_schemas.py` 的 schema 一致，
#: 否则模型调的名字在这里取不到（会走 loop 里那条 unknown_tool 分支）。
TOOL_IMPL = {"search_policy": search_policy, "ask_policy": ask_policy}
