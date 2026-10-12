# -*- coding: utf-8 -*-
"""手写 MCP Client（stdio 传输）—— 不用任何 MCP 库，纯 subprocess + JSON-RPC。

★ 为什么先手写这个，而不是直接用 mcp 官方 SDK 的 client：
  ① 面试直接问"为什么用/不用框架"—— 手写过才有真答案
  ② 避免第一天就卡在 mcp + langchain-mcp-adapters 的版本兼容上（本机一个包都没装）
  ③ 协议逻辑和 service.py 里 _invoke_llm 的超时降级是同一个思路，可对照着讲
  ⇒ 这份代码是**为了理解而写的**，不是"最终就该这么写"。
     真上生产还是用官方 SDK —— 这一点必须写在文档里，别假装手写更好。

★★ 这个 client 的职责边界必须说清楚（最容易搞混的地方）：

    它做的是【协议收发】，不是【业务决策】。
    ──────────────────────────────────────────
    agent/policy_agent.py（业务层）
        ↓ 要调哪个工具（模型决定）
    本文件（协议层）
        ↓ 怎么把"调这个工具"变成一行 JSON 发给 server、怎么读回结果
    mcp/server.py（另一个进程，干活的地方）

    ⇒ 换一套传输方式（比如改成 HTTP），只动本文件，业务层【一个字都不用改】。
      这就是"stdio 还是 HTTP"那道面试题的答案来源 —— 不是背的，
      是【把协议层和业务层分开】这个结构本身给的。

★★★ 实测踩出来的协议三个坑（每一条都对应一类真实故障）：

① **一行一条 JSON，靠 `\n` 分帧**
   服务端用 readline() 读，所以每条请求必须以 `\n` 结尾。
   漏了 ⇒ 服务端永远等不到你的消息，客户端静静卡死、不报错、像死了一样。
   ⇒ 这是 MCP stdio 新手的第一个 bug，症状是"什么也不发生"。

② **`readline()` 读到空字符串 = 流结束了**
   `readline()` 在流关闭时返回 `""`（不是 `None`、不报错）。
   ⇒ 空行 = server 死了，**必须当成致命错误**——否则你会在无意义的空行上
     无限循环，还以为是"在等数据"。

③ **notification 不能回，request 必须回**
   没有 id 的就是 notification，写了 stdout 一个字都是协议错误。
   ⇒ 服务端已经在 test_server_protocol.py 里验过了。
     客户端这边只需要知道：**发出去的每条带 id 的请求，
     都必须对应一条带同 id 的响应**，顺序允许不严格一致（并发请求时）。

跑法：
    <虚拟环境>/Scripts/python.exe mcp/client.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(_ROOT, "mcp", "server.py")


class MCPError(Exception):
    """协议层的错误，和 service.py 里 LLMCallError 是同一类 —— 用异常而不是返回值，
    是为了和【业务错误】严格分开（协议坏了 ≠ 业务说查不到）。"""


class StdioMCPClient:
    """最小 MCP client：拉起 server 子进程，通过 stdio 收发 JSON-RPC。

    ★ 为什么用类而不是散函数：
      状态（进程、请求 id 计数器）必须**活着**，不然每次调用都重新起进程。
      而类是"把状态和方法装在一起"的最自然方式 —— 这就是 OO 存在的意义。
    """

    def __init__(self, server_cmd: list[str] | None = None):
        self.server_cmd = server_cmd or [sys.executable, SERVER]
        self.proc: subprocess.Popen | None = None
        self._id = 0                      # 我们自己的请求编号器

    def __enter__(self):
        """进入 with 块时：起进程 + 握手 + 拉工具清单。

        ★ 为什么这里做三件事而不是只做"起进程"：
          MCP 的协议是**有状态的** —— initialize 之后才能谈 tools/list，
          tools/list 之后才能谈 tools/call。
          ⇒ 这些必须按顺序、在同一个进程里完成，否则每一步都是错的。
          ⇒ 所以 __enter__ 一次做完，失败的任何一种都能当场响，而不是
            等你真去调工具的时候才发现"server 根本没响应"。
        """
        self.proc = subprocess.Popen(
            self.server_cmd,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8",
            bufsize=1,                    # ★ 行缓冲：按行收发，不然等不到第一条
        )

        # 1. 握手：告诉 server 我是谁、能支持哪个协议版本
        init = self._request("initialize", {
            "protocolVersion": "2026-07-28",
            "capabilities": {},
            "clientInfo": {"name": "policy-agent", "version": "0.1.0"},
        })
        self.server_info = init["serverInfo"]
        self.protocol_version = init["protocolVersion"]

        # 2. 通知 server 我准备好了（★ 这是 notification，不能指望它回）
        self._notify("notifications/initialized", {})

        # 3. 拉工具清单（这才是业务层要用的 schema）
        tools = self._request("tools/list", {})
        self.tools = tools["tools"]
        return self

    def __exit__(self, exc_type, exc, tb):
        """退出时【必须】收尾，不然子进程泄漏 —— 和 service.py 里 finally 恢复 rag.rejected 同原则。"""
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()          # ★ 还等着就强杀，不跟它耗
        return False

    # ------------------------------------------------------------------
    # 协议收发（这是本文件的【全部】，不做任何业务判断）
    # ------------------------------------------------------------------

    def _send(self, msg: dict):
        """把一条 JSON-RPC 写进 server 的 stdin。"""
        if self.proc is None or self.proc.stdin is None:
            raise MCPError("server 进程没有起来，或 stdin 不可用")
        # ★★ 换行符 = 协议的分帧符。JSON 内部可能有换行，
        #   server 没法靠内容判断"这条消息结束了"。漏了 \n ⇒ 客户端静静卡死。
        self.proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()           # ★ flush 必须有：数据卡在缓冲区 = 对面永远收不到

    def _recv(self) -> dict:
        """从 server 的 stdout 读一行，解析成 dict。"""
        if self.proc is None or self.proc.stdout is None:
            raise MCPError("server 进程没有起来，或 stdout 不可用")
        line = self.proc.stdout.readline()
        if not line:
            # ★★★ 这是流结束的信号，不是"还在等数据"。
            #   空行 = server 死了，必须响。不响应的话你会在无意义的空行上
            #   无限循环，还以为是"在等数据"。
            raise MCPError("server 进程没有输出，可能已退出")
        try:
            return json.loads(line)
        except json.JSONDecodeError as e:
            raise MCPError(f"server 返回的不是合法 JSON：{e}；原始行：{line[:200]!r}") from e

    def _request(self, method: str, params: dict) -> dict:
        """发一个带 id 的请求，等它对应的响应回来。"""
        self._id += 1
        rid = self._id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})

        # ★★★ 不是"读一行就完了"。server 可能在你请求之间推 notification，
        #   或者你手抖发出过带 id 的请求。
        #   所以必须**读到 id 匹配**的那一条为止，其余丢弃 ——
        #   否则你会把别人的响应当成你的，id 一错，后面全错。
        #   这和 tests/test_guard.py 里 test_near_dup_after_earlier_rejection
        #   踩的坑同源：用位置当身份。
        for _ in range(20):                  # ★ 有限次数：防止 server 一直推无关消息把这里堵死
            resp = self._recv()
            if resp.get("id") == rid:
                break
        else:
            raise MCPError(f"读了 20 条消息都没等到 id={rid} 的响应")

        if "error" in resp:
            err = resp["error"]
            raise MCPError(f"JSON-RPC 错误 {err['code']}：{err['message']}")
        return resp["result"]

    def _notify(self, method: str, params: dict):
        """发一个 notification（★ 没有 id ⇒ 不能指望有响应）。"""
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    # ------------------------------------------------------------------
    # 给业务层用的接口 —— 这三行的实现就是整个文件存在的理由
    # ------------------------------------------------------------------

    def call_tool(self, name: str, arguments: dict) -> str:
        """调一个工具，返回它的文本结果。"""
        result = self._request("tools/call", {"name": name, "arguments": arguments})

        # ★ 工具失败也是【数据】：server 返回 isError=True，这里必须照实返回，
        #   不能 raise —— raise 会把工具失败伪装成协议失败，
        #   模型就看到【协议坏了】而不是【这个工具调不通】，两种处理方式完全不同。
        #   （与 service.py 里"拒答和故障必须严格分开"同原则。）
        parts = [c["text"] for c in result.get("content", []) if c.get("type") == "text"]
        return "\n".join(parts)


# ==================================================================
# 自测：真起一个 server 进程，走一遍完整握手
# ==================================================================
if __name__ == "__main__":
    print(f"拉起 server：{SERVER}\n")
    with StdioMCPClient() as c:
        print(f"server info    : {c.server_info}")
        print(f"protocol       : {c.protocol_version}")
        print(f"工具清单       : {[t['name'] for t in c.tools]}")
        print(f"负例工具混入？ : {'get_my_grades' in [t['name'] for t in c.tools]}")

        print("\n--- 调 search_policy ---")
        out = c.call_tool("search_policy", {"query": "请假 审批"})
        print(out[:300])

        print("\n--- 调 ask_policy（库里没有，期望走 NO_HIT）---")
        out = c.call_tool("ask_policy", {"question": "下个月的学杂费是多少？"})
        print(out)

        print("\n--- 负例路径：调不存在的工具 ---")
        # ★ 不存在的工具走的是【工具失败】不是【协议失败】：
        #   server 回 isError=True，call_tool 按设计把错误文本照实返回（不 raise）。
        out = c.call_tool("get_my_grades", {"student_id": "2023"})
        print(out)
        assert "没有名为" in out or "unknown" in out.lower(), \
            "负例工具居然返回了正常结果？"
        print("（负例被 isError 通道挡下，没有伪装成成功）")
