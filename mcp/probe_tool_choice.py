# -*- coding: utf-8 -*-
"""阶段 0：接真 LLM，跑 10 道题，出一张「模型选了哪个工具」的观察表。

★ 这个脚本的定位必须说清楚：
  它【不是评测】。10 道题量太小，统计上没有意义。
  它要回答的是一个更具体的问题：**我写的 schema，够不够让模型选对工具。**
  ⇒ 判读方式是「人读输出」，不是算通过率。

★★ 为什么这一步排在写 MCP 协议【之前】：
  schema 里的 description 是**提示词的一部分**，它和模型、和题集是耦合的。
  先把协议写完再调描述，等于协议层跟着提示词一起返工。
  ⇒ 而且顺序反了会踩同一个坑：本项目 20 题那套"既是调参集又是评测集"，
    被认定成自证循环。**同一个坑不要在一个项目里踩两次。**

跑法（务必先关 trace，否则每次 LLM 调用都联网上传，慢到能把脚本拖住）：
    set LANGSMITH_TRACING=false
    <你的虚拟环境>/Scripts/python.exe mcp/probe_tool_choice.py
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["LANGSMITH_TRACING"] = "false"
import env_compat                                # noqa: E402
env_compat.ensure_mmh3()                         # noqa: E402 本机 DLL 被策略拦截时的降级
env_compat.ensure_uuid_utils()                   # noqa: E402

from dotenv import load_dotenv                    # noqa: E402
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from langchain_core.messages import HumanMessage  # noqa: E402
from langchain_deepseek import ChatDeepSeek       # noqa: E402

from mcp.tool_schemas import MODEL_SCHEMAS        # noqa: E402

QUESTIONS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "evalset", "agent_probe_questions.json")


def load_questions():
    with open(QUESTIONS, encoding="utf-8") as f:
        return json.load(f)["questions"]


def hit_forbidden(text, patterns):
    """★ 判「有没有编造」用机械正则，不用 LLM 评判 —— 理由同README 里的数字对账：
    机械判据可复现、不随评判模型的心情变。返回命中���那条，没有则 None。
    """
    for p in patterns:
        m = re.search(p, text)
        if m:
            return p, m.group(0)
    return None, None


def probe(q, bound):
    """跑一道题，返回观察记录。"""
    msgs = [HumanMessage(content=q["question"])]
    resp = bound.invoke(msgs)

    picked = [tc["name"] for tc in (resp.tool_calls or [])]
    args = {tc["name"]: tc["args"] for tc in (resp.tool_calls or [])}
    return {
        "id": q["id"],
        "type": q["type"],
        "question": q["question"],
        "want_tool": q.get("want_tool"),
        "picked": picked,
        "args": args,
        # 没有工具调用时的直接回答 —— 这就是"模型自己答了"，
        # 对 negative 题来说，这可能正是期望行为（它知道自己没有这个能力）
        "direct_answer": (resp.content or "") if not picked else None,
    }


def main():
    llm = ChatDeepSeek(model="deepseek-chat", temperature=0)
    bound = llm.bind_tools(MODEL_SCHEMAS)
    qs = load_questions()

    print(f"题数 ={len(qs)}　工具 = {[s['function']['name'] for s in MODEL_SCHEMAS]}\n")
    rows = []
    for q in qs:
        r = probe(q, bound)
        rows.append(r)

        want = q.get("want_tool") or "（不调工具）"
        got = ", ".join(r["picked"]) or "（直接回答）"
        mark = "  " if r["picked"] == ([q["want_tool"]] if q.get("want_tool") else []) else "? "
        print(f"{mark}{r['id']:<4} {r['type']:<12} 期望 {want:<18} 实际 {got}")
        for name, a in r["args"].items():
            print(f"       参数 {name}({json.dumps(a, ensure_ascii=False)[:90]})")
        if r["direct_answer"]:
            print(f"       直接回答：{r['direct_answer'][:150]}")
        q["_forbidden"] = q.get("forbidden_patterns", [])
        pat, hit = hit_forbidden(r["direct_answer"] or "", q["_forbidden"])
        if pat:
            print(f"       [!!] 命中禁答模式 {pat!r} -> {hit!r}")
    return rows


if __name__ == "__main__":
    rows = main()
    with open(os.path.join(os.path.dirname(__file__), "_probe_out.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    print("\n观察表已写入 mcp/_probe_out.json")
