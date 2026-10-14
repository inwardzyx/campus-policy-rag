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

    ★ 正确做法（实测 sources 里就带着原文）：
      `sources` 里每条都有 {doc（含条款号）, text（原文）, score}。
    ⇒ 要原文根本不用碰检索那一层，直接读 sources 就行，
      而且这样【两个工具都走 HTTP】，MCP server 保持纯跨进程。

    ★★ 2026-10-14：改走 `/search`（只检索、不生成），不再借道 `/chat`。
      原来这里写着"代价：/chat 内部会跑一遍生成，对一个工具调用来说可以接受"
      —— **不接受了，而且是先把代价量出来才改的**：
      同一批 20 题实测，借道 `/chat` 时本函数 13 次调用共 **6,337 token**，
      而其中每一个 token 都是**没人读的答案文本**（这个函数只读 sources），
      占工具侧总账（26,277）的 **24%**。换成 `/search` 之后这部分是 **0**，
      而返回给模型的文本**逐字节不变**（服务侧两条路共用同一份检索 + 拒答实现，
      `tests/test_guard.py` 的「只检索端点」组另做真 A/B 对照）。
      ⚠️ 于是本函数**不烧 token**：标记行里 `llm_calls=0`，
      这是【可证明的 0】（服务端这条路上一个 LLM 调用点都没有），不是 `?`。
      ★ 2026-10-14 二次改动：账目数字（`tokens_total` 等五个字段）**已从标记行删掉** ——
      它们每次调用都重新进模型上下文，是纯噪声；而"恒 0 次调用"这条断言改用
      `llm_calls=0` 表达。工具侧的账改由 `agent/token_cost.py` 重放取，理由见 `_usage_fields`。

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
    data = _get_json("/search", {"question": query, "top_k": k})
    # ★ 把服务内部的账带上去（见 _usage_fields）：不然这部分 token 全在 agent 账外
    u = data.get("usage")
    if not data.get("knowledge_hit", True):
        return NO_HIT_TEXT + _signal_line("no_hit", "search_policy", usage=u)
    srcs = data.get("sources", [])
    if not srcs:
        return NO_HIT_TEXT + _signal_line("no_hit", "search_policy", usage=u)
    return _format_sources(srcs) + _signal_line("hit", "search_policy", len(srcs), usage=u)


def _format_sources(srcs: list[dict]) -> str:
    """把 `sources` 渲染成喂给模型的文本 —— **唯一的**一处渲染实现。

    ★ 2026-10-14 抽出来的理由跟"改端点"是同一件事的两面：
      这次改动的对外承诺是"模型看到的东西**一个字都没变**"。
      `sources` 逐字段相同 + 走同一个渲染函数 ⇒ 文本逐字节相同，
      两条都是可验证的（`scripts/ab_search_vs_chat.py` 直接比字符串）。
      ⚠️ 如果渲染散在 if/else 里，这个承诺就退化成"我读了一遍，看着一样"。
    """
    lines = []
    for s in srcs:
        lines.append(f"· {s['doc']}（相似度 {s['score']:.4f}）\n  {s['text'][:200]}")
    return "\n".join(lines)


def ask_policy(question: str) -> str:
    """ask_policy 的实现：走 /chat 拿【带出处的成答】。跨进程。

    ★ 同 search_policy：末尾带结构化命中标记（SIGNAL 协议，见上）。
    """
    data = _get_json("/chat", {"question": question, "use_rerank": True})
    u = data.get("usage")        # ★ 同 search_policy：把服务内部的账带上去
    # ★ knowledge_hit 是本项目自己加的字段，它把「库里没有」和「服务异常」分开。
    #   不看它，模型收到的就只是一句冷冰冰的"我不知道"，
    #   而模型分不清来源时，最可能的行为是硬答。
    if not data.get("knowledge_hit", True):
        return NO_HIT_TEXT + _signal_line("no_hit", "ask_policy", usage=u)
    src = data.get("sources", [])
    if src:
        doc_names = sorted({s.get("doc", "?") for s in src})
        return (f"{data['answer']}\n\n依据文件：{'、'.join(doc_names)}"
                + _signal_line("hit", "ask_policy", len(src), usage=u))
    return data.get("answer", "") + _signal_line("no_hit", "ask_policy", usage=u)


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


def _usage_fields(usage: dict | None) -> str:
    """只留**一次 LLM 调用都没发生**这条事实，账目数字一律不进标记行。

    ## ★★★ 2026-10-14 改：账目字段从标记行里**删掉**（先量后改）

    原来这里会把 `tokens_total / tokens_calls / tokens_input / tokens_output /
    tokens_cache_hit` 五个字段一并塞进标记行。**现在删掉，理由是一笔量出来的账：**

    · 标记行每次工具调用都跟着返回，而它整段都会**重新进模型上下文**（每一轮重发）。
      13 次 `search_policy` 调用实测：标记行合计 1,040 字，其中**账目字段占 1,040 字里的
      绝大部分**（每次 ~80 字）—— 这是喂给模型的纯噪声：模型不需要知道自己花了多少钱。
    · 删掉之后**模型可见的其余文本一个字节都不变** ⇒ 这不是"我觉得它没用"，
      是**可机械证明的等价**（`scripts/ab_payload_trim.py` 逐字节比）。
    · 而那条"这条路上恒 0 次 LLM 调用"的断言（本仓最值钱的一条外部可验证断言）
      **必须留下** —— 所以保留 `llm_calls=`，它正是同一个事实的另一种写法。

    ## 账本去哪了

    工具侧的账（服务内部为这次工具调用烧掉的 token）**不再经由标记行传递**，
    改成 `agent/token_cost.py` **按记录下来的调用参数重放**取账（`ToolLedger`）：
    重放的输入与当时逐字节相同 ⇒ 服务端记账一致，而模型上下文里一个字节都不多。
    代价写在 `agent/token_cost.py` 的注释里：重放要求服务在测量后仍然可用。

    ## ★ `?` 与 0 的区别（这条没变，也不能变）

    `usage is None` 有两种可能：整次请求一次都没调模型（真的 0）、
    或调了但一次都没拿到 usage（没拿到）。**前者写 0、后者写 `?`** ——
    混了就是本仓反复剿的那类错（把"没拿到"说成"真的是 0"）。
    ★ 2026-10-14 补：`/search` 返回的 `n_calls=0` 属于**可证明的 0**，
      此时写 0 才对，写 `?` 反而是假话。
    """
    if not usage:
        return " llm_calls=?"
    return f" llm_calls={int(usage.get('n_calls', 0))}"


def _signal_line(status: str, tool: str, n_src: int = 0,
                 usage: dict | None = None) -> str:
    """生成末尾的命中标记行。

    status ∈ {"hit", "no_hit"}；no_hit 表示库里确实没有这条。
    usage = `/chat` 响应里的 usage —— ★ 现在**只取它的 `n_calls`**，
    账目数字不再进模型上下文（见 `_usage_fields` 的取舍说明）。
    """
    return (f"\n\n{SIGNAL_PREFIX} {SIGNAL_KEY}={status} "
            f"tool={tool} n_sources={n_src}" + _usage_fields(usage))


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


def parse_signal_usage(tool_text: str) -> dict | None:
    """从标记行里解析【工具内部花掉的 token】。解析不了就返回 None，不猜。

    返回：{"total": int, "n_calls": int, "input": int, "output": int, "cache_hit": int}
    返回 None 有三种情况，**都必须与"花了 0 token"区分开**：
      · 没有标记行（旧版本 / 走了别的分支）
      · 标记行里没有计量字段（更旧的版本）
      · 标记行里写的是 `?` —— 工具明确说"这份账我没拿到"
    ⇒ 调用方见到 None 要计入"工具侧账目缺失"，而不是计入 0。
      这和 `service.py::_take_usage()` 那条"分清没拿到与真的是 0"是同一个原则。
    """
    if not tool_text or SIGNAL_PREFIX not in tool_text:
        return None
    for line in tool_text.splitlines():
        line = line.strip()
        if not line.startswith(SIGNAL_PREFIX):
            continue
        kv: dict[str, str] = {}
        for part in line[len(SIGNAL_PREFIX):].split():
            if "=" in part:
                k, _, v = part.partition("=")
                kv[k.strip()] = v.strip()
        if "tokens_total" not in kv or kv["tokens_total"] == "?":
            return None                      # 没字段 / 明确"没拿到" —— 都不是 0
        try:
            return {"total": int(kv["tokens_total"]),
                    "n_calls": int(kv.get("tokens_calls", 0)),
                    "input": int(kv.get("tokens_input", 0)),
                    "output": int(kv.get("tokens_output", 0)),
                    "cache_hit": int(kv.get("tokens_cache_hit", 0))}
        except ValueError:
            return None                      # 字段坏了也不猜


def parse_signal_calls(tool_text: str) -> int | None:
    """从标记行里解析【这次工具调用在服务端触发了几次 LLM 调用】。

    ★ 为什么不复用 `parse_signal_usage`：那个函数认的是**旧格式**的
      `tokens_total` 一整套字段。新标记行里已经不放账目数字了（见 `_usage_fields`），
      所以需要这个只认 `llm_calls` 的解析器。

    ★★ 返回 None 与 0 **不是一回事**，这条不能省：
      · `None` — 没有标记行 / 没有 `llm_calls` 字段 / 写的是 `?`
        ⇒ 调用方要计成「这次没拿到」，**不许当成 0**
      · `0`    — 工具明确报"服务端一次模型都没调"
        ⇒ 这正是 `search_policy` 走 `/search` 时的**可证明的 0**
    把 None 当成 0，就是本仓反复剿的"把没拿到说成真的是 0"。
    """
    if not tool_text or SIGNAL_PREFIX not in tool_text:
        return None
    for line in tool_text.splitlines():
        line = line.strip()
        if not line.startswith(SIGNAL_PREFIX):
            continue
        for part in line[len(SIGNAL_PREFIX):].split():
            if part.startswith("llm_calls="):
                v = part.split("=", 1)[1].strip()
                if v == "?":
                    return None
                try:
                    return int(v)
                except ValueError:
                    return None              # 字段坏了也不猜
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
