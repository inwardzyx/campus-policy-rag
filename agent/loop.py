# -*- coding: utf-8 -*-
"""agent/loop.py —— 把 `mcp/probe_loop.py::run_one` 的循环抽成**产品可调用**的函数。

## 为什么不重写循环

`probe_loop.run_one` 是拿 10 道 agent 题量过的（轮数上限 6 就是量出来的，见那个文件第 41–57 行）。
在这里重写一遍 = 造第二套循环：探针证明过的东西，产品路径上又得重新证明一次。

⇒ `run_loop` 的内部与 `run_one` **逐行同构**，`tests/test_agent_loop.py::test_matches_probe_loop`
用同一组 fake 响应跑两遍、逐步比对 trace —— 这是"没重写"的证据，不是承诺。

## 抽出来只多了三件事（都是产品要、探针不要）

1. **事件回调 `on_event`**：前端时间线靠它。⚠️ 事后再从 trace 反推"当时发生了什么"是编故事，
   必须在**发生的那一刻**发出去（`plan` / `tool_call` / `tool_result` / `answer`）。
2. **`stopped_reason`**：`done` / `max_rounds` / `budget`。
   ★ 静默截断 = 硬失败：用户看到一段"看起来完整"的答案，其实是撞了上限。
   probe_loop 撞上限会回一句「【超出 N 轮调用上限，我停止检索】」，这里保留这个语义。
3. **token 预算**：每轮结束检查一次，超了就在**下一轮开始前**收口 —— 而不是跑满再报账。

## 返回契约（前端与评测共用，改它要同时改两边）

```python
{
  "answer": str, "refused": bool,
  "stopped_reason": "done" | "gate_refused" | "no_tool" | "rewrite_limit"
                     | "max_rounds" | "budget" | "tool_error",
  "steps": [{"i", "tool", "args", "tool_ms", "status", "n_src", "error"}],
  "rounds": int, "tokens": int | None, "no_hit_seen": bool,
}
```
"""
from __future__ import annotations

import os
import re
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage   # noqa: E402

from mcp.probe_loop import MAX_ROUNDS, SYSTEM_PROMPT                            # noqa: E402  ★ 不抄提示词/上限
from mcp.tool_schemas import MODEL_SCHEMAS                                     # noqa: E402
from mcp.tools_impl import NO_HIT_TEXT, TOOL_IMPL, ToolError, parse_signal      # noqa: E402

#: 默认 token 预算。★ 这个数**不是**拍的：一次 multi-hop 问题实测 4 轮、约 3k token
#: （`mcp/_loop_out.json`），所以 8k 留了一倍余量；它只防"无界烧钱"，不负责省钱。
DEFAULT_BUDGET_TOKENS = 8000

_RE_N_SRC = re.compile(r"n_sources=(\d+)")
#: `mcp/tools_impl._format_sources` 的渲染格式：`· 文档（相似度 0.8123）\n  正文前 200 字`
#: ★ 解析失败就返回空列表，**不猜**（与 parse_signal 的"解析不了返回 None"同一原则）——
#:   这里没有 None 这个档，所以用"空 + 调用方看 steps.status"来区分。
_RE_SRC = re.compile(r"^· (.+?)（相似度 ([\d.]+)）\n  (.*)$", re.M)


def agent_enabled() -> bool:
    """`AGENT_ENABLED` 开关（默认 off）。★ 每次现读环境变量 —— 测试要能开关，重启才生效的开关没法测。"""
    return (os.environ.get("AGENT_ENABLED") or "0").strip().lower() in ("1", "true", "yes", "on")


def _tokens(resp) -> int:
    """从模型响应里取 token；取不到就返回 0 —— 调用方用 `tokens` 汇总时再看是否全 0。"""
    md = getattr(resp, "usage_metadata", None) or {}
    return int(md.get("total_tokens") or 0)


def _n_src(out: str) -> int:
    m = _RE_N_SRC.search(out or "")
    return int(m.group(1)) if m else 0


def _emit(on_event, etype: str, **fields) -> None:
    """事件必须一个个发；没有回调时静默（探针/测试不关心 UI）。"""
    if on_event is None:
        return
    on_event({"type": etype, **fields})


#: 改写问法的重试上限。★ 为什么是 1：105 题实测里，`s10` 第一步走闸被拒，
#: 模型改写问法再查就命中了**无关文档** —— "换个说法重试直到命中"是绕过拒答的后门。
#: 允许一次，是给"表述差异"留余地；允许两次，就是在允许它把拒答磨掉。
MAX_REWRITES = 2


def _norm(q: str) -> str:
    """比较"是不是同一个问题"用的归一化：只去空白与标点，不做语义改写（不猜）。"""
    import re as _re
    return _re.sub(r"[\s，。？！、；：,.?!;:]+", "", q or "").lower()


def run_loop(question: str, *, llm=None, max_rounds: int | None = None,
             budget_tokens: int | None = None, on_event=None, tools=None,
             gate=None) -> dict:
    """跑一次完整的 agent 循环。

    `llm` 必须是**已经 bind_tools 过的**可调用对象（`service.rag.llm.bind_tools(MODEL_SCHEMAS)`），
    传 None 时自己建一个 —— 只给脚本用，服务端应传入共享实例，别每条请求新建客户端。
    """
    if llm is None:
        from langchain_deepseek import ChatDeepSeek
        llm = ChatDeepSeek(model="deepseek-chat", temperature=0).bind_tools(MODEL_SCHEMAS)
    tools = tools if tools is not None else TOOL_IMPL
    rounds_limit = int(max_rounds or MAX_ROUNDS)
    budget = int(budget_tokens or DEFAULT_BUDGET_TOKENS)

    # ★★ 拒答口径以【原问题】为准（2026-11-12）：`gate` 是"这个原问题能不能回答"的判定，
    #   由调用方注入（服务端传 `handle_chat(原问题).knowledge_hit`）。不注入时退回旧口径。
    #   为什么必须在循环**开始前**判一次：循环里模型会改写问法，改写后的命中不足以证明
    #   原问题可答 —— 105 题实测就是这么把拒答从 41/61 磨到 26/61 的。
    answerable = True if gate is None else bool(gate(question))

    msgs = [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=question)]
    steps: list[dict] = []
    rewrites = 0
    _seen_q: set[str] = set()
    tool_errors = 0
    steps_succeeded = 0
    sources: list[dict] = []
    seen_src: set[tuple[str, str]] = set()
    tokens = 0
    no_hit_seen = False
    hits_seen = False

    for rnd in range(rounds_limit):
        resp = llm.invoke(msgs)
        tokens += _tokens(resp)

        # 模型这一轮如果说了点什么（不是空），当作它自己的"计划"发出去。
        # ★ 不发空串：前端拿到空计划会渲染出一个空气泡，看起来像 bug。
        if (getattr(resp, "content", "") or "").strip():
            _emit(on_event, "plan", round=rnd + 1, text=resp.content)

        # ---- 终止层 1：模型不再要工具 ⇒ 这就是最终答案（与 probe_loop 同构）----
        if not resp.tool_calls:
            answer = resp.content or ""
            _emit(on_event, "answer", text=answer)
            # ★★ 一次工具都没调就答 ⇒ 无证据（2026-11-12 冒烟实测：8 道超范围题里有 2 道
            #   模型直接凭常识作答，/chat 是拒答的）。这种回答**不许算 done**。
            if not steps:
                return {"answer": answer, "refused": True, "stopped_reason": "no_tool",
                        "steps": steps, "rounds": rnd + 1, "tokens": tokens,
                        "no_hit_seen": no_hit_seen, "sources": sources}
            # ★★ 原问题不可答 ⇒ 这里也必须拦住（不能只在收尾拦）：
            #   done 分支是最常见的一条路，漏了它，gate 等于没接。
            if gate is not None and not answerable:
                return {"answer": "知识库里没有能回答这个问题的资料，我不编。", "refused": True,
                        "stopped_reason": "gate_refused", "steps": steps, "rounds": rnd + 1,
                        "tokens": tokens, "no_hit_seen": no_hit_seen, "sources": []}
            # ★ 所有工具调用都失败 ⇒ 这不是"答完了"，是"根本没查到东西"（2026-11-12 实测踩到：
            #   连错端口时工具全失败，而 answer 是一段"服务连接失败"的优雅话术、状态却是 done）
            degraded = tool_errors > 0 and not steps_succeeded
            return {"answer": answer, "refused": bool(no_hit_seen and not hits_seen),
                    "stopped_reason": "tool_error" if degraded else "done", "steps": steps, "rounds": rnd + 1,
                    "tokens": tokens, "no_hit_seen": no_hit_seen, "sources": sources}

        # ★★ 必须先把模型那条原样放回 messages，否则协议序列断了（probe_loop:99 同一个坑）。
        msgs.append(resp)
        for tc in resp.tool_calls:
            name = tc["name"]
            _emit(on_event, "tool_call", round=rnd + 1, tool=name, args=tc["args"])
            # ★ 改写重试上限：同一个原问题只允许一次"换个说法再查"
            arg_text = " ".join(str(v) for v in (tc["args"] or {}).values())
            qkey = _norm(arg_text)
            # ★ 只统计"**新的**不同问法"：同一句话查两次不算两次改写（去重很关键 ——
            #   第一版按"每次调用"计数，结果正常题第一次换个措辞就被截断，h1 当场被误杀）。
            if qkey != _norm(question) and qkey not in _seen_q:
                _seen_q.add(qkey)
                if rewrites >= MAX_REWRITES:
                    _emit(on_event, "error", detail=f"改写重试超过 {MAX_REWRITES} 次，停止")
                    return {"answer": f"【改写问法超过 {MAX_REWRITES} 次，我停止检索】",
                            "refused": True, "stopped_reason": "rewrite_limit",
                            "steps": steps, "rounds": rnd + 1, "tokens": tokens,
                            "no_hit_seen": no_hit_seen, "sources": sources}
                rewrites += 1

            t0 = time.perf_counter()
            fn = tools.get(name)
            err = None
            if fn is None:
                # 模型幻觉出不存在的工具名：回一句错误，不许 KeyError 崩（probe_loop:101-106 同理由）
                out = f"没有名为 {name} 的工具。现有工具：{list(tools)}。"
                err = "unknown_tool"
            else:
                try:
                    out = fn(**tc["args"])
                except ToolError as e:
                    out = f"工具调用失败：{e}"
                    err = str(e)
            tool_ms = int((time.perf_counter() - t0) * 1000)
            status = parse_signal(out)          # hit / no_hit / None（解析不了不猜）
            for doc, score, text in _RE_SRC.findall(out or ""):
                key = (doc, text[:40])
                if key not in seen_src:
                    seen_src.add(key)
                    sources.append({"doc": doc, "score": float(score), "text": text})
            if status == "hit":
                hits_seen = True
            elif status == "no_hit":
                no_hit_seen = True
            step = {"i": len(steps) + 1, "tool": name, "args": tc["args"], "tool_ms": tool_ms,
                    "status": status, "n_src": _n_src(out), "error": err}
            if err:
                tool_errors += 1
            else:
                steps_succeeded += 1
            steps.append(step)
            _emit(on_event, "tool_result", **step)
            msgs.append(ToolMessage(content=str(out), tool_call_id=tc["id"]))

        # ---- 终止层 2：预算。★ 在下一轮开始【之前】判，不跑满再报账 ----
        if tokens >= budget:
            return {"answer": f"【已达到 {budget} token 预算上限，我停止检索】",
                    "refused": bool(no_hit_seen and not hits_seen),
                    "stopped_reason": "budget", "steps": steps, "rounds": rnd + 1,
                    "tokens": tokens, "no_hit_seen": no_hit_seen, "sources": sources}

    if gate is not None and not answerable:
        # ★ 原问题本身不可答 ⇒ 无论循环里查到了什么，都按拒答处理（answer 用 /chat 的原话）
        return {"answer": "知识库里没有能回答这个问题的资料，我不编。", "refused": True,
                "stopped_reason": "gate_refused", "steps": steps, "rounds": rounds_limit,
                "tokens": tokens, "no_hit_seen": no_hit_seen, "sources": []}

    # ---- 终止层 3：轮数用完。必须给兜底文案，不能静默返回（probe_loop:118-122 同语义）----
    return {"answer": f"【超出 {rounds_limit} 轮调用上限，我停止检索】",
            "refused": bool(no_hit_seen and not hits_seen),
            "stopped_reason": "max_rounds", "steps": steps, "rounds": rounds_limit,
            "tokens": tokens, "no_hit_seen": no_hit_seen, "sources": sources}
