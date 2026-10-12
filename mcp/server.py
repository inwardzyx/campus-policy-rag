# -*- coding: utf-8 -*-
"""手写 MCP Server（stdio 传输）—— 不用任何 MCP 库，纯 JSON-RPC。

★ 为什么先手写、而不是直接用 mcp 官方 SDK：
  ① 手写一遍才答得出"为什么用/不用框架"（面试直接问这个）
  ② 避免第一天就卡在 mcp + langchain-mcp-adapters 的版本兼容上（本机一个包都没装）
  ③ 协议逻辑和 service.py 里 _invoke_llm 的超时降级是同一个思路，可对照着讲
  ⇒ 所以这份代码是**为了理解而写的**，不是"最终就该这么写"。
     真上生产还是用官方 SDK —— 这一点必须写在文档里，别假装手写更好。

★★★ 协议三个容易踩的点（每一条都是实测踩出来的，不是背规范）：

① 一行一条 JSON，靠 `\n` 分帧
   JSON 内部可能有换行，server 没法靠内容判断"这条消息结束了"。
   ⇒ 漏了 `\n`，server 永远等不到你的消息，客户端【静静卡死、不报错】。
     这是新手第一个 bug，症状是"什么也不发生"。

② 没有 id 的消息是 notification，【不能回】
   回它就是协议错误。⇒ `resp.get("id") is None` 时不要写回 anything。

③ 错误要当【数据】回，不能 raise
   工具调不到 / 参数错 ⇒ 返回 `{"content":[...], "isError": true}`。
   raise 会把整个 stdio 连接搞崩，客户端永远等不到回应。
   ★ 同源原则：service.py 里"缺键时用 .get() 拦下并说明，而不是抛 KeyError"。
     两个地方一个道理，值得一起讲。

跑法（由 MCP client 拉起，不是手动跑）：
    <虚拟环境>/Scripts/python.exe mcp/server.py
手工试一把（另开一个终端往 stdin 喂一行）：
    echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python mcp/server.py
"""

from __future__ import annotations

import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mcp.tool_schemas import MCP_SCHEMAS                    # noqa: E402
from mcp.tools_impl import TOOL_IMPL, ToolError           # noqa: E402

PROTOCOL_VERSION = "2026-07-28"
SERVER_INFO = {"name": "policy-mcp", "version": "0.1.0"}


def handle(req: dict) -> dict | None:
    """处理一条请求。★ 返回 None 表示【不回复】（notification）。"""
    rid = req.get("id")                 # ★ 没有 id ⇒ notification ⇒ 不能回
    method = req.get("method", "")
    params = req.get("params", {})

    #★★★ 挡在最前面：没有 id 就是 notification，一律【不回】——
    #   实测踩出来的 bug（2026-10-12）：
    #   喂 {"jsonrpc":"2.0","method":"notifications/initialized"} 进来，
    #   它落进下面的"未知方法"分支，代码 return 了 {"id": null, "error":...}，
    #   而真实客户端会把它当成"对 id=null 的回复"，而它压根没发过这个请求。
    #   ⇒ 症状：协议被违反，客户端行为未定义。
    #   ⇒ 根因：我只在"未知方法"那一个分支里检查了 rid，
    #     而【每一】个分支都必须检查 —— 正确的位置是入口。
    if rid is None:
        return None

    # ---------------- initialize：协议握手 ----------------
    if method == "initialize":
        result = {
            "protocolVersion": PROTOCOL_VERSION,
            "serverInfo": SERVER_INFO,
            # 声明我们提供 tools 这类能力，且【不主动推任何东西】
            #⇒ capabilities 里没有 notifications，就不该往客户端推消息
            "capabilities": {"tools": {"listChanged": False}},
        }

    # ---------------- tools/list：给工具清单 ----------------
    elif method == "tools/list":
        result = {"tools": MCP_SCHEMAS}

    # ---------------- tools/call：真正干活 ----------------
    elif method == "tools/call":
        name = params.get("name", "")
        args = params.get("arguments", {}) or {}
        # ★★ 用 .get() 不用 []：负例工具（get_my_grades）不在清单里，
        #    模型若幻觉出工具名，必须【回一句错误】而不是 KeyError 把进程搞崩。
        fn = TOOL_IMPL.get(name)
        if fn is None:
            result = {
                "content": [{"type": "text",
                             "text": f"没有名为 {name} 的工具。现有工具：{list(TOOL_IMPL)}。"}],
                "isError": True,
            }
        else:
            try:
                out = fn(**args)
                result = {"content": [{"type": "text", "text": str(out)}]}
            except ToolError as e:
                # ★ 工具失败也是【数据】：isError=True，让模型看到"调不通"再换思路。
                #   直接 raise 会崩掉连接，模型什么都学不到。
                result = {"content": [{"type": "text", "text": f"工具调用失败：{e}"}],
                          "isError": True}
            except TypeError as e:
                # 参数不匹配（模型填错了字段名/数量）—— 同样当数据回，别崩
                result = {"content": [{"type": "text",
                                       "text": f"参数不合法：{e}。期望：{args}"}],
                          "isError": True}

    # ---------------- 未知方法 ----------------
    else:
        # JSON-RPC 标准错误码：-32601 = Method not found
        return {"jsonrpc": "2.0", "id": rid,
                "error": {"code": -32601, "message": f"未知方法：{method}"}}
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def main():
    # stderr 是给你自己看的（启动日志、报错），
    # ★ 绝不能往 stdout 写任何非协议内容 —— 那会污染协议流，客户端解析直接崩。
    print("policy-mcp 起来了（stdio 传输）", file=sys.stderr)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            # 收到坏 JSON 也要回一条错误（如果有 id），否则客户端一直等
            print(f"[!!] 不是合法 JSON：{e}", file=sys.stderr)
            continue

        resp = handle(req)
        if resp is None:
            continue                      # ★ notification：不回
        # ★ flush 必须有：不flush 的话数据卡在缓冲区，客户端永远收不到
        sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
