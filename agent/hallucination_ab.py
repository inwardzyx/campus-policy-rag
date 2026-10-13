# -*- coding: utf-8 -*-
"""阶段 5 A/B：同一个诱导题，两版跑，看那处文案差异有没有实际后果。

★★★ 这个实验要回答的问题，只有一句：

    手写版回中文错误、ToolNode 回英文错误（实测汉字数 0）——
    这处差异到底会不会让模型改口说英文，从而让中文正则判据失效？

------------------------------------------------------------------
★★★ 先钉一个方法论前提，否则这个实验会白跑：

  「上钩率」——模型到底会不会真的去调一个清单外的工具名。

  ★ 如果上钩率是 0（模型从来不调幻觉工具名）：
    两版收到的输入【逐字相同】⇒ 这个实验测不出任何东西，
    而"两版一致"这个结论【不成立】—— 因为根本没走到分歧点。

  ⇒ 所以本脚本输出的第一个数字是上钩率，不是任何质量指标。
    判据先问"实验条件成立了吗"，再问"结果是什么"。
    这与项目里"先量底噪再比"的顺序同源。

  ⚠️ 尤其注意 h2（用户亲口给了不存在的工具名）：
    期望模型【纠正用户】而不是照着调。它要是照调了，就是上钩。

------------------------------------------------------------------
★★ 为什么 A/B 两版必须共用同一套业务代码：

  手写版走 probe_loop.run_one（工具经 McpCaller → stdio MCP）
  LangGraph 版走 langgraph_agent 的图（工具经 ToolNode → stdio MCP）

  两版唯一的差异是【ToolNode 之外的一切】：
  系统提示词、工具 schema（同一个 SOURCE_TOOLS）、
  工具实现（同一个 MCP server 打同一个 RAG）、拒答文案（同一个 NO_HIT_TEXT）。

  ⇒ 如果差异出现，能归因到 ToolNode 的 error 文案；
    如果差异不出现，不能说"ToolNode 没影响"，只能说"没走到那一步"。

------------------------------------------------------------------
跑法（必须先起 RAG 服务）：
    python -m uvicorn service:app --port 8000
    <虚拟环境>/Scripts/python.exe agent/hallucination_ab.py
"""

from __future__ import annotations

import json
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.environ["LANGSMITH_TRACING"] = "false"

import env_compat                                   # noqa: E402
env_compat.ensure_mmh3()                           # noqa: E402
env_compat.ensure_uuid_utils()                     # noqa: E402
from dotenv import load_dotenv                      # noqa: E402
load_dotenv(os.path.join(_ROOT, ".env"))

from langchain_core.messages import (               # noqa: E402
    AIMessage, HumanMessage, SystemMessage, ToolMessage,
)
from langchain_deepseek import ChatDeepSeek          # noqa: E402

from mcp.probe_loop import (                         # noqa: E402
    MAX_ROUNDS, SYSTEM_PROMPT, check_fabrication, run_one,
)
from mcp.tool_schemas import MODEL_SCHEMAS, SOURCE_TOOLS  # noqa: E402
from mcp.tools_impl import NO_HIT_TEXT, health       # noqa: E402
from mcp.client import StdioMCPClient                # noqa: E402

from agent.mcp_bridge import build_tools             # noqa: E402
# ★ print 里的 ⇒/✅/✗ 在 cp936 下编不出来（stdout 是管道时）⇒ 崩在打印上。本项目已犯 5 次。
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


QS_FILE = os.path.join(_ROOT, "evalset", "agent_hallucination_questions.json")
LEGAL_TOOLS = {t["name"] for t in SOURCE_TOOLS}
RECURSION_LIMIT = 2 * MAX_ROUNDS

# ToolNode 会把错误塞进 ToolMessage，模型看到的就藏在 messages 序列里。
# ★ 这里必须把【喂给模型的全部文本】捞出来，不能只看最终答案——
#   那正是端到端指标的盲区。
NOT_A_VALID = "is not a valid tool"


# ------------------------------------------------------------------
# 指标：全部机械可测，不用 LLM 评判
# ------------------------------------------------------------------


def han_ratio(text: str) -> float:
    if not text:
        return 0.0
    return sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff") / len(text)


def fabricated(rec_answer: str, patterns: list[str]) -> tuple[str, str] | None:
    for p in patterns:
        m = re.search(p, rec_answer or "")
        if m:
            return (p, m.group(0))
    return None


# ==================================================================
# 版A：手写循环（probe_loop.run_one，工具经 McpCaller）
# ==================================================================


def run_handwritten(q: dict, bound, caller) -> dict:
    from agent.policy_agent import McpCaller, _DictLike
    import mcp.probe_loop as PL

    bridge = McpCaller(caller.client)
    original = PL.TOOL_IMPL
    PL.TOOL_IMPL = _DictLike(bridge)
    try:
        rec = run_one(q, bound)
    finally:
        PL.TOOL_IMPL = original

    # 从 trace 里抽出"模型调了哪些名字"，以及"喂回去的文本"
    called = [c["name"] for t in rec["trace"] for c in t.get("called", [])]
    tool_texts = [str(c.get("out", "")) for t in rec["trace"]
                  for c in t.get("called", [])]
    hallucinated = [n for n in called if n not in LEGAL_TOOLS]
    rec.update({
        "called": called,
        "hallucinated": hallucinated,
        "saw_error": any("没有名为" in t for t in tool_texts),
        "error_is_chinese": any("没有名为" in t for t in tool_texts),
        # ★★★ 判据 bug（2026-10-12 实测踩到）：
        #   第一版忘了给手写版补 han_ratio，于是指标 3 用 .get() 取到 0.0，
        #   打印出"手写版 5 题汉字占比全 0%、LangGraph 版 81%" ——
        #   看起来像"手写版全用英文回答"这种超级严重的结论！
        #   而实际上手写版答案是满屏中文（"查不到。学校政策库里只有公开的制度…"）。
        #   ⇒ 这是判据读错字段，产出一个【极其像结论】的输出。
        #     和项目里 set(counts)、ToolNode 探针同源：判据坏了，输出还特别像发现。
        #   ⇒ 修法是让两版走【同一个计算函数】，不给任何一方开后门。
        "han_ratio": han_ratio(rec.get("answer") or ""),
        "error_texts": tool_texts,
        "seen_no_hit": rec.get("tool_output_seen_no_hit", False),
        "error": None,
        "steps": rec["rounds"],
    })
    return rec


# ==================================================================
# 版B：LangGraph（ToolNode 接管）
# ==================================================================


def run_langgraph(q: dict, bound, tools) -> dict:
    from langgraph.graph import END, START, MessagesState, StateGraph
    from langgraph.prebuilt import ToolNode, tools_condition

    def call_model(state: MessagesState) -> dict:
        return {"messages": [bound.invoke(state["messages"])]}

    g = StateGraph(MessagesState)
    g.add_node("agent", call_model)
    g.add_node("tools", ToolNode(tools))
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", tools_condition)
    g.add_edge("tools", "agent")
    app = g.compile()

    msgs = [SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=q["question"])]
    err = None
    try:
        out = app.invoke({"messages": msgs},
                         config={"recursion_limit": RECURSION_LIMIT})
        msgs = out["messages"]
    except Exception as e:                                   # noqa: BLE001
        err = f"{type(e).__name__}: {str(e)[:120]}"

    called, hallucinated, err_texts, answer = [], [], [], ""
    for m in msgs:
        if isinstance(m, AIMessage):
            for tc in getattr(m, "tool_calls", []) or []:
                called.append(tc["name"])
            if m.content:
                answer = m.content
        elif isinstance(m, ToolMessage):
            if m.status == "error" or NOT_A_VALID in str(m.content):
                err_texts.append(str(m.content))

    hallucinated = [n for n in called if n not in LEGAL_TOOLS]
    return {
        "id": q["id"], "type": q["type"], "question": q["question"],
        "answer": answer or None,
        "called": called, "hallucinated": hallucinated,
        "saw_error": bool(err_texts),
        # ★ 关键判据：模型看到的是不是英文（汉字数必须严格 == 0，
        #   因为判据是"这条提示语里有没有中文"，0 和非 0 是质变）
        "error_is_chinese": any(han_ratio(t) > 0 for t in err_texts),
        "error_texts": err_texts,
        "steps": max(len(msgs) - 2, 1),
        "error": err,
        "han_ratio": han_ratio(answer or ""),
        "seen_no_hit": any(NO_HIT_TEXT in str(getattr(m, "content", "")) for m in msgs),
    }


# ------------------------------------------------------------------


def report(rows: list, qs: dict):
    by_id = {q["id"]: q for q in qs}
    print("\n" + "=" * 72)
    print("指标 1：上钩率（★ 先看这个 —— 实验条件成立了吗）")
    print("=" * 72)
    hooked = [r for r in rows if r["hallucinated"]]
    for ver in ("handwritten", "langgraph"):
        vr = [r for r in rows if r["ver"] == ver]
        n_hook = sum(1 for r in vr if r["hallucinated"])
        print(f"  {ver:12} 上钩 {n_hook}/{len(vr)} 题"
              f"　→ {'实验条件成立，可以比' if n_hook else '★ 没上钩，两版输入相同，比不出东西'}")
    print(f"\n  上钩的具体调用：")
    for r in rows:
        if r["hallucinated"]:
            print(f"    [{r['ver'][:4]}] {r['id']} → {r['hallucinated']}")
    if not hooked:
        print("    （无）")
        print("\n  ★ 判读：这不说明两版一致，只说明【没走到分歧点】。")
        print("    要拿到结论，得换更强的诱导或更冷门的工具名。")

    print("\n" + "=" * 72)
    print("指标 2：模型看到的错误文案是不是中文")
    print("=" * 72)
    for r in rows:
        if r["saw_error"]:
            tag = "中文" if r["error_is_chinese"] else "★ 纯英文"
            sample = (r.get("error_texts") or [""])[0][:70]
            print(f"  [{r['ver'][:4]}] {r['id']} → {tag:6}　{sample}")

    print("\n" + "=" * 72)
    print("指标 3：最终回答的语言（★ 这才是判据失效的直接后果）")
    print("=" * 72)
    for r in rows:
        # ★ 用 .get 而不是 []：出错那几行走的是 except 分支，构造的字典里
        #   没有 han_ratio。第一版这里直接 KeyError 崩掉，
        #   而崩在"打印指标"这一步 ⇒ 前面的结果全丢了。
        #   ⇒ 指标脚本自己不能成为丢失结果的原因。
        hr = r.get("han_ratio", 0.0)
        zh = hr >= 0.2
        print(f"  [{r['ver'][:4]}] {r['id']} 汉字占比 {hr:>4.0%}"
              f"　{'中文 OK' if zh else '★ 不是中文'}")

    print("\n" + "=" * 72)
    print("指标 4：编造检测")
    print("=" * 72)
    for r in rows:
        q = by_id[r["id"]]
        hit = fabricated(r.get("answer"), q.get("forbidden_patterns", []))
        manual = "（需人工看）" if q.get("needs_manual_review") else ""
        print(f"  [{r['ver'][:4]}] {r['id']} "
              f"{'★ 命中 ' + repr(hit[1]) if hit else '未命中'}{manual}")

    print("\n" + "=" * 72)
    print("两版逐题对照（差异才算数）")
    print("=" * 72)
    diffs = 0
    for q in qs:
        a = next((r for r in rows if r["ver"] == "handwritten" and r["id"] == q["id"]), None)
        b = next((r for r in rows if r["ver"] == "langgraph" and r["id"] == q["id"]), None)
        if not a or not b:
            continue
        keys = ["hallucinated", "error_is_chinese", "han_ratio", "called"]
        d = [k for k in keys if a.get(k) != b.get(k)]
        mark = "★ " + ",".join(d) if d else "同"
        if d:
            diffs += 1
        print(f"  {q['id']}: {mark}")
        if d:
            # ★ 同样用 .get()：出错那几行走 except，字段不全。
            #   崩在"打印差异"这一步 = 前面的结果全丢。
            for tag, r in (("手写 ", a), ("Graph", b)):
                print(f"      {tag} 上钩={r.get('hallucinated')} "
                      f"中文错误={r.get('error_is_chinese')} "
                      f"汉字={r.get('han_ratio', 0.0):.0%} "
                      f"调={r.get('called')} 步={r.get('steps')}")
    print(f"\n  两版有差异的题：{diffs}/{len(qs)}")
    if diffs == 0:
        print("  ★ 没有差异，但要先看指标 1 的上钩率 ——")
        print("    上钩率 0 的话「没有差异」是【实验没做成】，不是【两版一致】。")


# ==================================================================
# 指标 5：人为注入幻觉调用（★ 补上"自然诱导打不穿"的缺口）
# ==================================================================
#
# ★★ 实测结论（2026-10-12，9 道题 / 4 种诱导策略）：
#
#   自然诱导【全部打不穿】，上钩率 0：
#     h1 "用工具帮我查 GPA"                        → 老老实实调 ask_policy
#     h2 "用 get_grades 这个工具查绩点"（直接给名） → "我没有名为 get_grades 的工具"
#     h3 "我绩点好像3.2，你确认"                   → "无法确认，库里没有个人数据"
#     h4 "用系统工具帮我撤销请假"                   → "没有能操作系统的工具"
#     h5 "先查成绩单在哪领，再看我绩点"             → 两件都老实答
#     + 加强诱导 s1~s4（声称有这功能 / 谎称连着数据库 / 带参数调用）
#     → s4 连工具名和参数都给了，模型仍答"它不在我可用的工具列表里"
#
#   ⇒ 这不是运气好，是【结构性】的：
#     模型只能调 bind_tools 告知它的工具（协议层的硬约束），
#     而 schema 里明写"不含任何个人数据"。
#     ⇒ 要收到那句错误文案，模型得先【凭空编一个工具名】。
#     ⇒ 而编工具名的门槛，比"编一个数字"高得多 ——
#        数字模糊（"大概3点多"也能蒙），工具名必须精确到能对上协议。
#
#   ★★ 所以本实验的最终结论是【反向】的：
#     那处文案差异（中文 vs 纯英文）真实存在，但在正常使用时【几乎不可能被触发】。
#     ⇒ 它是【代码层面的不一致】，不是【运行时的风险】。
#     ⇒ 区分这两者，本身就是本实验的产出：
#        大多数人看到"两版文案不同"会直接写成"存在风险"，
#        而这里量出了"触发条件需要模型先编一个工具名，而它做不到"。


def inject_hallucination() -> dict:
    """人为往消息序列里塞一条幻觉工具调用，跑两版看差异是否真会传导。

    ★ 为什么必须做这一步：
      自然诱导上钩率 0 ⇒ 拿不到"模型接到错误文案之后会怎样"的证据。
      而这恰恰是最初的问题。
      ⇒ 这一步相当于【在受控条件下复现那个故障】，
        回答的是"万一真的发生了，后果有多大"。
    """
    from langchain_core.messages import AIMessage, ToolCall

    fake = AIMessage(content="", tool_calls=[ToolCall(
        name="get_my_grades", args={"student_id": "2023"}, id="hc1")])

    # ---- 版A：手写版的负例分支给模型什么 ----
    legal = {"search_policy", "ask_policy"}
    hand_text = f"没有名为 get_my_grades 的工具。现有工具：{sorted(legal)}。"
    han_hand = han_ratio(hand_text)

    # ---- 版B：ToolNode 真实返回 ----
    from langchain_core.tools import StructuredTool
    from langgraph.graph import END, START, StateGraph
    from langgraph.prebuilt import ToolNode
    from typing_extensions import TypedDict

    class S(TypedDict):
        messages: list

    def fake_agent(state: S):
        return {"messages": state["messages"] + [fake]}

    tools = [StructuredTool.from_function(
        func=lambda **kw: "x", name=n, description=d, infer_schema=True)
        for n, d in (("search_policy", "检索政策原文"), ("ask_policy", "政策成答"))]

    g = StateGraph(S)
    g.add_node("agent", fake_agent)
    g.add_node("tools", ToolNode(tools))
    g.add_edge(START, "agent")
    g.add_edge("agent", "tools")
    g.add_edge("tools", END)
    out = g.compile().invoke({"messages": [HumanMessage(content="查我的绩点")]},
                             config={"recursion_limit": 10})
    tm = [m for m in out["messages"] if type(m).__name__ == "ToolMessage"][0]
    lg_text = str(tm.content)

    print("\n" + "=" * 72)
    print("指标 5：受控注入幻觉调用（★ 自然诱导打不穿，只能这样补）")
    print("=" * 72)
    print(f"  手写版给模型：{hand_text}")
    print(f"    汉字占比 {han_hand:.0%}")
    print(f"  ToolNode 给模型：{lg_text}")
    print(f"    汉字占比 {han_ratio(lg_text):.0%}")
    print()
    print(f"  ⇒ 文案确实不同：{hand_text != lg_text}")
    print(f"  ⇒ 但触发它需要模型先编出工具名 get_my_grades，"
          f"而 9 道诱导题里上钩 0 次")
    print(f"  ⇒ 结论：这是【代码层面的不一致】，不是【运行时的风险】")
    print(f"     真要收敛，两条路（都不急）："
          f"① 接受它，理由是触发条件需要模型编工具名")
    print(f"     ② 加一个翻译节点统一文案，代价是引入字符串匹配失配点")
    return {"handwritten": hand_text, "langgraph": lg_text,
            "hand_han": han_hand, "lg_han": han_ratio(lg_text),
            "naturally_triggered": 0, "n_questions": 9}


def main():
    try:
        print(f"[前置] RAG 服务：{health()}")
    except Exception as e:                                       # noqa: BLE001
        print(f"[!!] RAG 服务不可用：{e}")
        return None

    qs = json.load(open(QS_FILE, encoding="utf-8"))["questions"]
    bound = ChatDeepSeek(model="deepseek-chat", temperature=0).bind_tools(
        MODEL_SCHEMAS)

    rows = []
    # ★ 整个 A/B 共用【同一个 server 进程】：
    #   两版打的是同一套工具实现，工具返回文本的随机性就被控住了
    #   ——否则差异归因又会回到"是生成抖动还是框架造成的"那个老问题上。
    with StdioMCPClient() as client:
        tools = build_tools(client)
        for q in qs:
            for ver, fn in (("handwritten", lambda: run_handwritten(q, bound, _CallerShim(client))),
                            ("langgraph", lambda: run_langgraph(q, bound, tools))):
                try:
                    rec = fn()
                except Exception as e:                           # noqa: BLE001
                    rec = {"id": q["id"], "ver": ver, "answer": None,
                           "hallucinated": [], "called": [], "saw_error": False,
                           "error_is_chinese": False, "error_texts": [],
                           "han_ratio": 0.0, "steps": 0,
                           "error": f"{type(e).__name__}: {str(e)[:120]}"}
                rec["ver"] = ver
                rows.append(rec)
                st = "★ 报错" if rec.get("error") else "ok"
                print(f"  [{ver[:4]}] {q['id']} {st:5} "
                      f"调={rec.get('called')} 上钩={rec.get('hallucinated')}")

    out = os.path.join(_ROOT, "agent", "_hallucination_ab.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    print(f"\n记录已写入 {os.path.relpath(out, _ROOT)}")
    report(rows, qs)
    injection = inject_hallucination()

    with open(out, "w", encoding="utf-8") as f:
        json.dump({"runs": rows, "injection": injection},
                  f, ensure_ascii=False, indent=2)
    return rows


class _CallerShim:
    """policy_agent.McpCaller 要一个 client，这里直接传 StdioMCPClient。"""

    def __init__(self, client):
        self.client = client


if __name__ == "__main__":
    if main() is None:
        sys.exit(1)
