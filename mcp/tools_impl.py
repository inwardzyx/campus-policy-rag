# -*- coding: utf-8 -*-
"""工具执行层：把 MCP 的 tools/call 落到你已有的 RAG 服务上。

★★ 这一层的三个设计决定，每一个都对应一类真实故障：

① **必须走 HTTP 打127.0.0.1:8000，而不是 import RAG 类**
   ⇒ 因为这是【跨进程】。能这么答"stdio 还是 HTTP""服务没起来怎么办"，
     而同进程 import 只能答"我包了一层"。
   ⇒ 代价是要处理"服务没起来"，也就是下面的 ②。

② **RAG 服务没起来 / 挂了 ⇒ 返回 isError，不抛异常**
   ⇒ MCP 的设计是把错误当【数据】回给模型，让模型看到"这个工具调不通"
     然后换个思路或如实告诉用户。raise 会把整个 stdio 连接搞崩，
     模型什么都学不到，客户端还会一直等 —— 这就是 ③ 要解决的。

③ **每个 HTTP 调用都有硬超时**
   ⇒ /chat 内部要跑 rerank + 生成，量级是秒级；客户端没有超时就等于
     把整个 agent 的可用性交给下游服务的稳定性。
     ★ 这和 service.py 里 _invoke_llm 用线程池 + future.result(timeout=)
       是同一个思路：一个思路用在两个地方，面试时讲得出"为什么"。

④ ★★拒答信号必须传上去（本项目最容易漏的一环）
   /chat 返回里有 knowledge_hit 字段，它区分「库里没有」和「服务挂了」。
   如果只把 answer 文本回给模型，模型就分不清这两者 ——
   而它分不清的时候，最可能的行为是【换个工具再试，或者干脆自己编】。
   ⇒ 所以这里把 knowledge_hit=False 翻译成一句【模型看得懂的话】再返回。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

# ★ 仓库根要进 sys.path：本文件在 mcp/ 子目录下，
#   直接跑脚本时 sys.path[0] 是 mcp/，import service 会ModuleNotFoundError。
#   （和 tests/conftest.py、experiments/ 下那些脚本同一个坑。）
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

RAG_BASE = os.environ.get("RAG_BASE", "http://127.0.0.1:8000")
HTTP_TIMEOUT = float(os.environ.get("MCP_HTTP_TIMEOUT", "30"))

# 反幻觉用：库里没有的时候，工具回给模型的话必须【明确、且可被模型照做】
NO_HIT_TEXT = ("【知识库里没有这条】不要据此作答，"
               "也不要从别处拼凑数字或流程。告诉我库里查不到即可。")


class ToolError(Exception):
    """工具执行失败。★ 注意这个异常【不往外抛】，只在 server 里被接住转成 isError。"""


def _get_json(path: str, payload: dict | None = None, timeout: float = HTTP_TIMEOUT) -> dict:
    """打RAG 服务并解析 JSON。任何失败都抛 ToolError（★ 统一收口，别让裸异常漏出去）。"""
    url = RAG_BASE + path
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"},
        method="POST" if data else "GET",
    )
    try:
        # ★ timeout 是必须的：没有它，下游挂了就等于整个 agent 挂��
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise ToolError(f"HTTP {e.code}：{e.reason}") from e
    except urllib.error.URLError as e:
        # 连接被拒 = 服务没起来，这是最常见的失败，必须单独说清
        raise ToolError(f"连不上 RAG 服务（{RAG_BASE}）：{e.reason}。"
                        f"请确认它已经启动：python -m uvicorn service:app --port 8000") from e
    except TimeoutError as e:
        raise ToolError(f"RAG 服务超时（>{timeout}s）") from e
    except json.JSONDecodeError as e:
        raise ToolError(f"RAG 返回的不是合法 JSON：{e}") from e


def search_policy(query: str, k: int = 5) -> str:
    """search_policy 的实现：拿【原文条款】。

    ★★ 踩过的坑，值得记下来（2026-10-12）：
    第一版这里写的是 `import service` 拿进程内的 rag.search()，理由是
    "要原文就只能取检索那一层的结果"。**结果 MCP server 是另一个进程，
    那边的 service.rag 永远是 not ready** —— 报"模型还在加载"，
    而真服务明明在好好跑着。
    ⇒ **这是"没实测就写代码"的典型**：架构图上看着顺，跑起来才发现自相矛盾。

    ★ 正确做法（实测 /chat 的 sources 里就带着原文）：
      /chat 返回的 sources 里每条都有 {doc（含条款号）, text（原文）, score}。
      ⇒ 要原文根本不用碰检索那一层，直接读 sources 就行，
        而且这样【两个工具都走 HTTP】，MCP server 保持纯跨进程，
        "stdio 还是 HTTP""服务没起来怎么办"这些问题才答得出来。
    ⇒ 代价：/chat 内部会跑一遍生成，所以比纯检索慢 ——
      对一个工具调用来说可以接受，而且这一点必须写在文档里，别假装它更轻。
    """
    data = _get_json("/chat", {"question": query, "use_rerank": False, "top_k": k})
    if not data.get("knowledge_hit", True):
        return NO_HIT_TEXT
    srcs = data.get("sources", [])
    if not srcs:
        return NO_HIT_TEXT
    lines = []
    for s in srcs:
        lines.append(f"· {s['doc']}（相似度 {s['score']:.4f}）\n  {s['text'][:200]}")
    return "\n".join(lines)


def ask_policy(question: str) -> str:
    """ask_policy 的实现：走 /chat 拿【带出处的成答】。跨进程。"""
    data = _get_json("/chat", {"question": question, "use_rerank": True})
    # ★ knowledge_hit 是本项目自己加的字段，它把「库里没有」和「服务异常」分开。
    #   不看它，模型收到的就只是一句冷冰冰的"我不知道"，
    #   而模型分不清来源时，最可能的行为是硬答。
    if not data.get("knowledge_hit", True):
        return NO_HIT_TEXT
    src = data.get("sources", [])
    if src:
        doc_names = sorted({s.get("doc", "?") for s in src})
        return f"{data['answer']}\n\n依据文件：{'、'.join(doc_names)}"
    return data.get("answer", "")


def health() -> dict:
    """健康检查：MCP server 启动时先探一下，别等到 agent 调用才发现服务没起。"""
    return _get_json("/health")


# ★★ 这就是【唯一源】：MCP 侧的 tools/list 和 bind_tools 侧的描述都从这里派生。
#   注意 get_my_grades 故意不在这里 —— 模型只能调"被告知存在的工具"。
TOOL_IMPL = {
    "search_policy": search_policy,
    "ask_policy": ask_policy,
}


if __name__ == "__main__":
    import sys
    try:
        print("健康检查：", health())
    except ToolError as e:
        print("[!!] " + str(e))
        sys.exit(1)
    print("工具清单：", list(TOOL_IMPL))
    print("\n--- search_policy 试跑---")
    print(search_policy("请假 审批")[:300])
    print("\n--- ask_policy 试跑（这题库里没有，期望走 NO_HIT）---")
    print(ask_policy("下个月的学杂费是多少？")[:300])
