# -*- coding: utf-8 -*-
"""阶段 3：完整 Agent = 真 LLM（bind_tools） + 真 MCP client（stdio 跨进程）。

★★★ 本文件存在的全部意义，是兑现 probe_loop.py 第 12-15 行写下的那句承诺：

    "等循环本身验证过了，再把它换成走 stdio 的 MCP client ——
     那样出问题能立刻分辨是【循环逻辑】还是【协议收发】。"

⇒ 所以本文件的做法是：**import probe_loop 的 run_one，一个字都不重写**，
  只把"工具怎么被调用"这一件事换掉。
  如果业务逻辑需要改一个字，说明阶段 1/2 的分层是假的。

------------------------------------------------------------------
★★ 那到底改了哪一行？—— 一行：

    probe_loop.py:    fn = TOOL_IMPL.get(tc["name"])
                        out = fn(**tc["args"])

    本文件：          caller = McpCaller(client)          # ← 只换这个对象
                      （run_one 内部代码不动，见下方 run_one 的 import）

  McpCaller 故意做成 **和 dict 一样的形状**（.get(name) → 可调用对象），
  所以它能无痛顶替 TOOL_IMPL。
  ⇒ 这不是炫技，是【接口对齐】：MCP 换掉了工具的实现，但没换掉它的形状。
    面试时这句话比任何架构图都值钱。

------------------------------------------------------------------
★★ 一个刻意的取舍：整个跑批【只起一个 server 进程】。

  真实 agent 就是一个长生命周期进程，工具服务器不该每轮重起
  （起一次进程 ≈ 几百毫秒，10 道题重起 10 次纯属浪费）。
  代价：如果 server 在第 7 题崩了，后面 3 题全挂。
  ⇒ 所以异常路径必须【响】而不是静默：run_one 里任何 MCPError 都被
    转成给模型看的文本，让模型自己决定怎么办，而不是整批崩掉。
    （与 tools_impl 里"错误当数据回给模型"同一原则，一路传到最外层。）

------------------------------------------------------------------
跑法（必须先起 RAG 服务，否则工具全返回"连不上"）：
    python -m uvicorn service:app --port 8000
    <虚拟环境>/Scripts/python.exe agent/policy_agent.py
"""

from __future__ import annotations

import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.environ["LANGSMITH_TRACING"] = "false"

import env_compat                                  # noqa: E402
env_compat.ensure_mmh3()                          # noqa: E402
env_compat.ensure_uuid_utils()                    # noqa: E402
from dotenv import load_dotenv                     # noqa: E402
load_dotenv(os.path.join(_ROOT, ".env"))

from langchain_deepseek import ChatDeepSeek         # noqa: E402

# ★★ 业务层【整个 import 过来】，不重写。这就是本文件的核心论点。
from mcp.probe_loop import (                        # noqa: E402
    MAX_ROUNDS, QUESTIONS, SYSTEM_PROMPT,
    check_fabrication, check_language, run_one,
)
from mcp.client import MCPError, StdioMCPClient      # noqa: E402
from mcp.tool_schemas import MODEL_SCHEMAS           # noqa: E402
from mcp.tools_impl import NO_HIT_TEXT, health       # noqa: E402


class McpCaller:
    """把 StdioMCPClient 包装成【和 TOOL_IMPL 字典一样】的形状。

    ★★★ 为什么要做这层对齐（这是本文件唯一的技术设计）：

      probe_loop.run_one 里写的是
          fn = TOOL_IMPL.get(tc["name"])     # 从 dict 里取
          out = fn(**tc["args"])             # 取出来就能直接调
      而 MCP client 的用法是
          out = client.call_tool(name, args)  # 参数是一个【字典】，不是展开的 kwargs

      两者签名不同（**kwargs vs dict）。如果照直改 run_one，
      就得把业务代码改成 MCP 的形状 —— 那就等于"换传输方式，业务层要改"，
      和 probe_loop 里写的承诺相反。

      ⇒ 这里让 McpCaller.get() 返回一个【接受 kwargs 的小闭包】，
        把 kwargs 重新打包成 dict 再交给 client。
      ⇒ 净效果：run_one 一行没改，MCP 就接上了。
    """

    def __init__(self, client: StdioMCPClient):
        self.client = client

    # ---- 让 dict.get(tc["name"], None) 那套语义原样可用 ----
    def get(self, name: str):
        """返回可调用对象；名字不存在时返回 None（让调用点自己走负例分支）。"""
        # ★ 注意这里【故意不用 server 的工具清单做校验】：
        #   模型幻觉出一个不存在的工具名时，client.call_tool 会拿到
        #   isError 通道的返回（"没有名为 X 的工具"），
        #   而 run_one 里 TOOL_IMPL.get() 返回 None 走的是另一条路。
        #   两条路都能让模型看到"这个工具调不通"，但文案不同 ——
        #   实测会打印出来对比，这是阶段 5 要盯的差异之一。
        if name not in {t["name"] for t in self.client.tools}:
            return None

        def _call(**kwargs) -> str:
            # ★ 协议失败抛异常，业务失败返回文本 —— 这个边界不能糊：
            #   MCPError = stdio 断了/协议坏了（重试有意义）
            #   isError 文本 = server 活着但这个工具调不通（重试没意义）
            return self.client.call_tool(name, kwargs)

        return _call


def run_one_over_mcp(q: dict, bound, caller: McpCaller) -> dict:
    """跑一道题，但工具走 MCP。

    ★ 为什么这里只做一件小事（临时替换模块级 TOOL_IMPL），
      而不是把 run_one 复制一份改：
      复制一份意味着两份业务逻辑从此各自演化，
      而"两版跑出来的差别到底是框架造成的还是业务分叉造成的"
      就永远说不清了 —— 这是所有 A/B 对比实验的通用死法。
      ⇒ 用同一个 run_one，差别只可能来自传输层。
    """
    import mcp.probe_loop as PL

    original = PL.TOOL_IMPL
    PL.TOOL_IMPL = _DictLike(caller)
    try:
        return run_one(q, bound)
    finally:
        PL.TOOL_IMPL = original          # ★ 必须恢复，否则污染同进程后续调用


class _DictLike:
    """极薄的字典外观：把 McpCaller 包成 dict，让 run_one 的 .get() 原样工作。"""

    def __init__(self, caller: McpCaller):
        self._caller = caller

    def get(self, name, default=None):
        fn = self._caller.get(name)
        return default if fn is None else fn

    def __iter__(self):
        return iter(t["name"] for t in self._caller.client.tools)

    def keys(self):
        return [t["name"] for t in self._caller.client.tools]


def main():
    # ---- 0. 先探活：工具全依赖 RAG 服务，服务没起就别浪费 token ----
    try:
        print(f"[前置] RAG 服务健康检查：{health()}")
    except Exception as e:                                  # noqa: BLE001
        print(f"[!!] RAG 服务不可用：{e}")
        print("    先起服务：python -m uvicorn service:app --port 8000")
        return None

    llm = ChatDeepSeek(model="deepseek-chat", temperature=0)
    bound = llm.bind_tools(MODEL_SCHEMAS)

    qs = json.load(open(QUESTIONS, encoding="utf-8"))["questions"]

    rows = []
    # ★ 一个 server 进程跑完整批（见文件头"刻意的取舍"）
    with StdioMCPClient() as client:
        print(f"[握手] server={client.server_info} 协议={client.protocol_version}")
        print(f"[工具] server 报的是 {[t['name'] for t in client.tools]}")
        # ★★ 交叉核对：模型被告知的清单，必须和 server 报的清单一致。
        #   不一致就是 schema 漂了（改了 SOURCE_TOOLS 但 server 拿的是另一份）。
        mdl_names = [s["function"]["name"] for s in MODEL_SCHEMAS]
        srv_names = [t["name"] for t in client.tools]
        print(f"[交叉核对] 模型侧={mdl_names}　server 侧={srv_names}　"
              f"{'一致 OK' if mdl_names == srv_names else '!! 不一致，schema 漂了'}")
        print(f"[预算] 轮数上限={MAX_ROUNDS}　题数={len(qs)}\n")

        caller = McpCaller(client)
        for q in qs:
            try:
                rec = run_one_over_mcp(q, bound, caller)
            except MCPError as e:
                # 协议层炸了（server 崩了/流断了）—— 必须响，不能静默跳过，
                # 否则这一批的结果会被当成"模型表现不好"，把基建故障误判成模型问题。
                print(f"--- {q['id']} [协议层故障] {type(e).__name__}: {e}")
                rec = {"id": q["id"], "type": q["type"], "question": q["question"],
                       "answer": None, "rounds": 0, "trace": [],
                       "tool_output_seen_no_hit": False, "mcp_error": str(e)}
            rows.append(rec)

            names = [c["name"] for r in rec["trace"] for c in r.get("called", [])]
            print(f"--- {rec['id']} [{rec['type']}] {rec['question']}")
            print(f"    调用了 {names or '（无）'}　共 {rec.get('rounds')} 轮"
                  f"　见到『查不到』={rec.get('tool_output_seen_no_hit')}")
            zh, ratio = check_language(rec)
            print(f"    汉字占比 {ratio:.0%}　中文={'OK' if zh else '!! 不达标'}")
            print(f"    回答：{(rec.get('answer') or '')[:200]}")
            p, hit = check_fabrication(rec, q.get("forbidden_patterns", []))
            if p:
                print(f"    [!!] 命中禁答模式 {p!r} -> {hit!r}")
            if not zh:
                print("    [!!] 不是中文回答 —— 禁答正则对它失效，判定不可信")
            print()

    return rows


if __name__ == "__main__":
    rows = main()
    if rows is None:
        sys.exit(1)
    out = os.path.join(_ROOT, "agent", "_agent_out.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    print(f"完整记录已写入 {os.path.relpath(out, _ROOT)}")

    # ---- 阶段 3 自身的自检（不是评测，是"接线对不对"）----
    print("\n=== 接线自检 ===")
    assert rows, "没有任何记录，说明 run_one 根本没跑起来"
    mcp_errors = [r for r in rows if r.get("mcp_error")]
    print(f"协议层故障：{len(mcp_errors)} 题" +
          (f"（{[r['id'] for r in mcp_errors]}）" if mcp_errors else " → 基建正常"))
    used = {c["name"] for r in rows for c in
            (x for t in r["trace"] for x in t.get("called", []))}
    print(f"实际被调用的工具：{sorted(used)}")
    assert used <= {"search_policy", "ask_policy"}, f"调到了不该存在的工具：{used}"
    print("[OK] 所有工具调用都经由 MCP server，没有绕过协议层直接调函数")
