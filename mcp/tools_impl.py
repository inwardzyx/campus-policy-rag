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
      而且这样【两个工具都走 HTTP】，MCP server 保持纯跨进程。
    ⇒ 代价：/chat 内部会跑一遍生成，所以比纯检索慢 ——
      对一个工具调用来说可以接受，而且这一点必须写在文档里，别假装它更轻。

    ★★★ 2026-10-12 加：命中标记（SIGNAL 协议）
      为什么加：拒答判据原来只能靠【读模型的措辞】猜它有没有依据，
      而关键词判据的实测假阳率高到会把"硬答"洗成"正确拒答"。
      ⇒ 改成让工具【自己说】有没有命中 —— 一个固定格式的标记块：
        模型看得懂（它读的就是这段文本），脚本也能精确解析（不靠猜）。
      ★ 这个块必须放在**答案的最末尾**：模型读到最后的注意力权重最高，
        而中间会被业务内容稀释。
      ⚠️ 它只是给模型的提示，不改变模型的行为保证 ——
        模型仍可能无视它编答案。所以它解决的是"判据能不能客观测量"，
        不是"模型会不会硬答"。后者要靠 hard_answer_rate 单独测。
    """
    data = _get_json("/chat", {"question": query, "use_rerank": False, "top_k": k})
    if not data.get("knowledge_hit", True):
        return NO_HIT_TEXT + _signal_line("no_hit", "search_policy")
    srcs = data.get("sources", [])
    if not srcs:
        return NO_HIT_TEXT + _signal_line("no_hit", "search_policy")
    lines = []
    for s in srcs:
        lines.append(f"· {s['doc']}（相似度 {s['score']:.4f}）\n  {s['text'][:200]}")
    return "\n".join(lines) + _signal_line("hit", "search_policy", len(srcs))


def ask_policy(question: str) -> str:
    """ask_policy 的实现：走 /chat 拿【带出处的成答】。跨进程。

    ★ 同 search_policy：末尾带结构化命中标记（SIGNAL 协议，见上）。
    """
    data = _get_json("/chat", {"question": question, "use_rerank": True})
    # ★ knowledge_hit 是本项目自己加的字段，它把「库里没有」和「服务异常」分开。
    #   不看它，模型收到的就只是一句冷冰冰的"我不知道"，
    #   而模型分不清来源时，最可能的行为是硬答。
    if not data.get("knowledge_hit", True):
        return NO_HIT_TEXT + _signal_line("no_hit", "ask_policy")
    src = data.get("sources", [])
    if src:
        doc_names = sorted({s.get("doc", "?") for s in src})
        return (f"{data['answer']}\n\n依据文件：{'、'.join(doc_names)}"
                + _signal_line("hit", "ask_policy", len(src)))
    return data.get("answer", "") + _signal_line("no_hit", "ask_policy")


def health() -> dict:
    """健康检查：MCP server 启动时先探一下，别等到 agent 调用才发现服务没起。"""
    return _get_json("/health")


# ==================================================================
# ★★★ SIGNAL 协议：让工具【自己说】命中没命中（2026-10-12）
#
# 为什么要有这个：
#   原来判断"模型有没有依据"只能【读模型的措辞】，
#   而关键词判据的实测假阳率高到会把硬答洗成"正确拒答"
#   （DSH 独立审计实测：5 条对抗样例全错，最致命的一条是
#     "我查不到报销比例，但按往年惯例可以报销 80%" 被判成正确拒答）。
#   ⇒ 改为主动标记：这个格式是固定的，脚本能精确解析，不靠猜措辞。
#
# 为什么不用 JSON：
#   工具返回是给【模型】读的。JSON 对模型是额外噪音，
#   而这行标记要同时满足两件事：
#     · 模型看得懂（不然它会当成无关内容忽略）
#     · 脚本能精确 parse（不然还得正则，又回到猜措辞）
#   ⇒ 一行人话 + 一个稳定的 key=value 尾巴，两边都够用。
#
# ⚠️ 它【不是】行为保证：模型完全可能无视这行标记继续编。
#   它解决的是"能不能客观测量"，不是"会不会硬答"。
#   后者必须靠 hard_answer_rate（模型无视标记的题数）单独测。
# ==================================================================

SIGNAL_PREFIX = "[检索信号]"
SIGNAL_KEY = "hit_status"       # ★ 脚本只认这个 key，别改名字


def _signal_line(status: str, tool: str, n_src: int = 0) -> str:
    """生成末尾的命中标记行。

    status ∈ {"hit", "no_hit"}；no_hit 表示库里确实没有这条。
    """
    return (f"\n\n{SIGNAL_PREFIX} {SIGNAL_KEY}={status} "
            f"tool={tool} n_sources={n_src}")


def parse_signal(tool_text: str) -> str | None:
    """从工具输出里解析命中标记。**解析不了就返回 None，不猜。**

    ★ 返回 None 是合法的结果，不是异常 ——
      它意味着"这次工具没带标记"（旧版本 / 走了别的分支），
      此时计分必须把该题排除出分母，而不是当成 no_hit。
      把 None 当成 no_hit 会凭空造出一批"硬答"。
    """
    if not tool_text or SIGNAL_PREFIX not in tool_text:
        return None
    for line in tool_text.splitlines():
        line = line.strip()
        if not line.startswith(SIGNAL_PREFIX):
            continue
        for part in line[len(SIGNAL_PREFIX):].split():
            if part.startswith(f"{SIGNAL_KEY}="):
                v = part.split("=", 1)[1].strip()
                return v if v in ("hit", "no_hit") else None
    return None


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
