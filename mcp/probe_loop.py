# -*- coding: utf-8 -*-
"""阶段 0 续：跑【完整循环】，看模型拿到「查不到」之后会不会硬答。

★ 与 probe_tool_choice.py 的区别（别把两个搞混）：
    probe_tool_choice.py  只跑第一轮 ⇒ 只能看"模型第一次想调什么"。
    本文件                跑完整个循环（调工具 → 拿结果 → 再问模型）⇒
                          才能判断它拿到"库里没有"之后会不会编。
    ★★ 第一版只跑第一轮就下结论"负例题选错了工具"，那是【没看到结果就当看到了失败】。
       工具返回什么才是判据 —— 这就是"检出≠证明"的又一次。

★★ 本文件【故意不 import 任何 MCP 库】：
   循环先用普通 Python 函数（TOOL_IMPL）直接跑通，
   等循环本身验证过了，再把它换成走 stdio 的 MCP client ——
   那样出问题能立刻分辨是【循环逻辑】还是【协议收发】。
   和阶段 1→2→3 的顺序同源：一次只验证一个新东西。
"""

import json
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.environ["LANGSMITH_TRACING"] = "false"

import env_compat                                # noqa: E402
env_compat.ensure_mmh3()                         # noqa: E402
env_compat.ensure_uuid_utils()                   # noqa: E402
from dotenv import load_dotenv                    # noqa: E402
load_dotenv(os.path.join(_ROOT, ".env"))

from langchain_core.messages import (              # noqa: E402
    HumanMessage, SystemMessage, ToolMessage,
)
from langchain_deepseek import ChatDeepSeek      # noqa: E402

from mcp.tool_schemas import MODEL_SCHEMAS       # noqa: E402
from mcp.tools_impl import TOOL_IMPL, ToolError, NO_HIT_TEXT  # noqa: E402

#★★ MAX_ROUNDS = 6，这是【量出来的】，不是拍的（2026-10-12 实测 10 道题）：
#
#     1 轮：1 题（s10 直接拒绝，没调工具）
#     2 轮：8 题
#     4 轮：1 题（s3 跨文档查找，用满 4 轮）
#
#   第一版定 4，**结果 s3 刚好顶到上限** —— 意味着再多一道复杂题就会被截断，
#   而截断的代价是答案退化成"【超出N 轮上限，我停止检索】"，那是硬失败，
#   不是"答案稍差"。⇒ 上限必须留余量。
#   定 6 的理由：s3 用满 4 轮且每一轮都有用（查评选条件 → 查认定办法 →
#   加"等级/特别困难"再查 → 确认库里没有），说明多轮检索是真实需求；
#   6 轮 × 10 题的 token 成本可忽略，而死循环的代价是无界的
#   —— 取"够用的上限"里最松的那个，是有意的取舍。
#
#   ⚠️ 但这个数只对【当前 10 道题 + 当前 schema】成立。
#     改了 schema 的description，模型的检索轮数会变，别拿旧结论当新依据。
MAX_ROUNDS = 6
QUESTIONS = os.path.join(_ROOT, "evalset", "agent_probe_questions.json")

#★★ 中文约束为什么放在【消息里】而不是只放在 schema 的 description 里：
#   第一版没管这件事，实测 s1/s3 用英文答了（"I searched the policy database
#   thoroughly."）。而 forbidden_patterns 是【中文正则】
#   （"\\d+\\s*元"、"\\d{1,2}:\\d{2}"），中英混着判就失真——
#   ★ 这是"判据和被测对象不在同一口径"的典型：不是判据写错了，是它测的东西变了。
#
#   为什么不用 system prompt 约束：① 那也是提示词，会跟着 schema 一起漂；
#   ② 工具循环的每一轮 messages 都会被重新审视，system 容易被稀释。
#   ⇒ 折中：system 说"用中文"，【并在最终判定里机械校验有没有中文输出】——
#     说了不算数，量出来才算数。
SYSTEM_PROMPT = (
    "你是学校政策问答助手。你只能依据工具返回的内容作答，"
    "工具说查不到时就明确说查不到，绝不编造数字、流程或条款。"
    "**所有回答一律使用简体中文。**"
)


def run_one(q, bound):
    """跑一道题的完整循环。返回观察记录。"""
    # ★SystemMessage 放在最前，后面每轮追加的消息都在它之后
    msgs = [SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=q["question"])]
    trace = []                       # 每轮记一笔：模型要调什么、返回了什么

    for rnd in range(MAX_ROUNDS):
        resp = bound.invoke(msgs)

        # ---- 终止层1：模型不再要工具 ⇒ 这才是最终答案 ----
        if not resp.tool_calls:
            trace.append({"round": rnd, "called": [], "final": resp.content})
            return {"id": q["id"], "type": q["type"], "question": q["question"],
                    "want_tool": q.get("want_tool"), "trace": trace,
                    "answer": resp.content, "rounds": rnd + 1,
                    "tool_output_seen_no_hit": any(NO_HIT_TEXT in str(t) for t in trace)}

        # ---- 执行模型要的工具（可能一轮多个）----
        called = []
        # ★★ 必须先把模型那条原样放回messages，否则协议序列断了，
        #    模型看不到"这个结果是给哪次调用的"。这一行漏了后面全错。
        msgs.append(resp)
        for tc in resp.tool_calls:
            fn = TOOL_IMPL.get(tc["name"])
            if fn is None:
                # ★ 用 .get() 不用 []：负例工具不在清单里，
                #   模型若幻觉出一个不存在的工具名，要【回一句错误】而不是 KeyError 崩掉。
                #   这与 service.py 里"缺键时拦下并说明，而不是抛 KeyError"同一原则。
                out = f"没有名为 {tc['name']} 的工具。现有工具：{list(TOOL_IMPL)}。"
                called.append({"name": tc["name"], "args": tc["args"], "error": True})
            else:
                try:
                    out = fn(**tc["args"])
                    called.append({"name": tc["name"], "args": tc["args"], "out": out})
                except ToolError as e:
                    out = f"工具调用失败：{e}"
                    called.append({"name": tc["name"], "args": tc["args"], "error": str(e)})
            msgs.append(ToolMessage(content=str(out), tool_call_id=tc["id"]))
        trace.append({"round": rnd, "called": called, "final": None})

    # ---- 终止层2：轮数用完。必须给一个兜底，不能静默返回 ----
    return {"id": q["id"], "type": q["type"], "question": q["question"],
            "want_tool": q.get("want_tool"), "trace": trace,
            "answer": f"【超出 {MAX_ROUNDS} 轮调用上限，我停止检索】", "rounds": MAX_ROUNDS,
            "tool_output_seen_no_hit": any(NO_HIT_TEXT in str(t) for t in trace)}


def check_fabrication(rec, forbidden):
    """★ 机械判据：正则扫有没有编造。不用 LLM 评判 —— 机械的可复现。"""
    for p in forbidden:
        m = re.search(p, rec["answer"] or "")
        if m:
            return p, m.group(0)
    return None, None


def check_language(rec):
    """★ 校验"用中文回答"这条指令【真的生效了】。

    ★★ 为什么必须量，而不是信 system prompt 里写了什么：
      提示词是【请求】，不是【事实】。模型可以无视它。
      而 forbidden_patterns 是中文正则 —— 判据和被测对象必须同一口径，
      否则不是判据写错了，是它测的东西变了（第一版 s1/s3 用英文答，
      中文正则根本扫不到那些数字）。
    ⇒ 所以每题都机械量一次：中文标点或汉字占比不足就报出来。

    判据用【中文字符占比】而不是"有没有中文标点"——
    因为 s10 那种回答里只有 "GPA"、"URL" 之类英文缩写，
    用标点判会误报。
    """
    text = rec["answer"] or ""
    if not text:
        return False, 0.0
    han = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    ratio = han / len(text)
    # ★ 阈值 0.2 是拍的：中文正文里夹英文术语时占比会在 0.3~0.6，
    #   纯英文则接近 0。0.2 足够把"答了但没用中文"挑出来，又不会误报术语多的回答。
    return ratio >= 0.2, ratio


def main():
    llm = ChatDeepSeek(model="deepseek-chat", temperature=0)
    bound = llm.bind_tools(MODEL_SCHEMAS)
    qs = json.load(open(QUESTIONS, encoding="utf-8"))["questions"]

    print(f"题数 ={len(qs)}　轮数上限 ={MAX_ROUNDS}　工具 ={list(TOOL_IMPL)}\n")
    rows = []
    for q in qs:
        rec = run_one(q, bound)
        rows.append(rec)
        names = [c["name"] for r in rec["trace"] for c in r.get("called", [])]
        print(f"--- {rec['id']} [{rec['type']}] {rec['question']}")
        print(f"    调用了 {names or '（无）'}　共 {rec['rounds']} 轮"
              f"　见到『查不到』={rec['tool_output_seen_no_hit']}")
        zh, ratio = check_language(rec)
        print(f"    汉字占比 {ratio:.0%}　中文={'OK' if zh else '!! 不达标'}")
        print(f"    回答：{(rec['answer'] or '')[:200]}")
        p, hit = check_fabrication(rec, q.get("forbidden_patterns", []))
        if p:
            print(f"    [!!] 命中禁答模式 {p!r} -> {hit!r}")
        if not zh:
            print("    [!!] 不是中文回答 —— 禁答正则对它失效，判定不可信")
        print()
    return rows


if __name__ == "__main__":
    rows = main()
    with open(os.path.join(_ROOT, "mcp", "_loop_out.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    print("完整记录已写入 mcp/_loop_out.json")
