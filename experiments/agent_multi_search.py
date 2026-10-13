#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""实验：让模型自己决定"要不要再查一次、换什么查"（Agent 式多查）。

★ 这个实验要回答的问题（别混淆）：
    主流程的拒答判据是"精排最高分< 5 就拒答"，写死在service.py 里。
    本实验【不改主流程、不加分支到服务里】，只是在脚本里搭一个对照：
        对照组 = 主流程现状（一轮查完，分< 5 就拒）
        实验组 = LLM 自己决定换查询词再查，最多 N 轮
    问的是：多查一次能不能救回 q16/q20 这两道拒答题？
    —— 如果能，说明"拒答"里有一部分是【查得不够】造成的，
       而不是【库里真没有】，那么值得把自主决策接进主流程。
       如果不能，说明 q16/q20 是真的库里没有，多查只是烧 token。

★ 为什么必须让 LLM 自己换查询词，而不能用同义词表：
    用同义词表（"学费" → "费用/收费/标准"）是我预设的，
    那还是 write死的流水线，只是把 if-else 换成了 dict 查表 —— 不是 Agent。
    Agent 的判据是：【下一步做什么由模型根据当前情况自己决定】。
    所以这里的 prompt 只给它"当前查到了什么 + 分数多少"，
    让它输出下一个查询词或"够了"。

用法：
    /d/Python-project/.venv/Scripts/python.exe experiments/agent_multi_search.py
    需要 DEEPSEEK_API_KEY。跑的是真实模型调用，会烧一点 token。
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# ★ 必须在 import service 之前设好，关掉 LangSmith 自动追踪（否则它会拦网络）
os.environ.setdefault("LANGSMITH_TRACING", "false")

import service as svc  # noqa: E402  —— 必须晚于上面那行 env 设置
# ★ print 里的 ⇒/✓/⚠ 在 cp936 下编不出来（stdout 是管道时）⇒ 崩在打印上。本项目已犯 7 次。
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("exp")

# ★ 拒答阈值：和 service.py::rerank 里用的同一个常量含义。
#   抄过来而不是 import，是为了不改动service.py（这个实验不许碰主流程）。
REJECT_BELOW = 5
# ★ 粗捞池：一轮查几条
TOP_K = 5
# ★ 最多查几轮。第3 轮是刻意设的上限，见下面「为什么上限是 3」的小讨论。
MAX_ROUNDS = 3


# ==================================================================
# 实验组：Agent 式多查
# ==================================================================
DECIDE_PROMPT = """你在帮一个校园政策问答系统找答案。

用户问题：{q}

你已经查过 {n} 轮，这是第 {i} 轮查到的情况：
本轮查询词：{query}
{blocks}

任务：判断【这些资料里有没有能直接回答用户问题的】。

★ 判断标准不是"像不像"，而是"有没有用户问的那个值"：
    问"多少钱"→ 必须有具体金额；
    问"几天/几点"→ 必须有时间值；
    问"怎么办"→ 必须有步骤/流程。
  资料只是提到相关主题但没给出这个值，等于【没有】。

只输出 JSON，两个字段：
{{"found": true 或 false, "next_query": "下一个查询词，若 found=true 则填空字符串"}}

★ next_query 的要求：它必须是【新的检索角度】，不能只是原问句的同义改写。
  好的例子：「专项补助管理办法 学费减免 条件」
  差的例子：「学费标准多少」（等于把原问题重打一遍）
"""

# 系统提示单独一条，让模型知道自己的角色
DECIDE_SYSTEM = "你是检索质量判断器，只输出 JSON，不写解释。"


def run_agent_multi_search(rag, question: str) -> dict:
    """跑实验组：模型自己决定换查询词再查。返回完整轨迹。"""
    from langchain_core.messages import SystemMessage, HumanMessage

    query = question                # ★ 第 1 轮用原问题，之后由模型自己改写
    seen: set[str] = set()
    trace = []

    for rnd in range(1, MAX_ROUNDS + 1):
        if query in seen:
            #★ 防呆：模型给了重复的查询词 → 不再查，直接停。
            #   不设这个上限的话，模型可能一直给同一个词，烧 3 次钱得到同一个结果。
            trace.append({"round": rnd, "query": query,
                          "note": "重复查询词，停止", "score": None,
                          "found": None, "next_query": None})
            break
        seen.add(query)

        cands = rag.search(query, k=TOP_K)
        ranked = rag.rerank(query, cands)
        top_score = ranked[0][2] if ranked else 0
        top_doc = (f"{ranked[0][0]['doc']}｜{ranked[0][0]['clause']}"
                   if ranked else "(空)")

        # 把这轮看到的东西拼给模型判断
        # rerank() 返回 [(块, 向量分, 精排分, 理由)] —— 顺序别搞错，
        #   之前这里把第 4 个（理由）当成 text 用过，报NameError。
        blocks = "\n".join(
            f"  {i}. [精排 {s} 分] {c['doc']}｜{c['clause']}\n     {c['text'][:120]}"
            for i, (c, _v, s, _r) in enumerate(ranked, 1)
        ) or "  （没查到任何块）"

        prompt = DECIDE_PROMPT.format(q=question, n=rnd, i=rnd,
                                      query=query, blocks=blocks)
        try:
            # ★ 用 service._invoke_llm（模块级函数），不是 rag._invoke ——
            #   后者不存在，写错会 AttributeError，而那会被except 吞成
            #   "判据调用失败"，看起来像模型的问题。凡是 except 里不带类型区分的
            #   except Exception 都会制造这种误导，这里是靠注释记住的。
            resp = svc._invoke_llm(svc.rag.llm, [
                SystemMessage(content=DECIDE_SYSTEM),
                HumanMessage(content=prompt),
            ])
        except Exception as e:                    # noqa: BLE001 —— 失败要看得见
            trace.append({"round": rnd, "query": query,
                          "note": f"判据调用失败：{type(e).__name__}: {e}",
                          "score": top_score, "found": None, "next_query": None})
            break

        raw = re.sub(r"^```(?:json)?|```$", "", resp.content.strip(), flags=re.M).strip()
        try:
            data = json.loads(raw)
            found = bool(data.get("found"))
            next_q = (data.get("next_query") or "").strip()
        except Exception:
            # ★ 解析失败【不许当成 found=false】—— 那等于"模型说不出话"被翻译成"库里没有"。
            #   这正是本项目反复强调的那类错误：把故障伪装成一个明确的结论。
            trace.append({"round": rnd, "query": query,
                          "note": f"判据返回非 JSON，原样：{raw[:120]}",
                          "score": top_score, "found": None, "next_query": None})
            break

        trace.append({"round": rnd, "query": query, "top_doc": top_doc,
                      "score": top_score, "found": found, "next_query": next_q})

        if found or not next_q or next_q == query:
            break
        query = next_q

    return {"trace": trace, "rounds": len(trace)}


def _classify(base_refuse: bool, agent_refuse: bool) -> tuple[bool, bool]:
    """把 (基线拒答, agent 拒答) 映射成 (救回, 弄坏)。

    ★ 2026-10-12 修：这两行原来写反了 ——
        旧写法 rescued = (not base) and (not agent)  ⇒ 实际是"两臂都没拒"
        旧写法 broke   = base and (not agent)        ⇒ 这个才是【救回】的正确定义
      后果：任何"被救回"的题会被印成「⚠ 误放行」，任何"被弄坏"的题会被印成「不变」，
      **报告的标签会说反**。
      ★ 本次 5 道题两臂都拒答（base=agent=True），正好落在真值表里唯一写对的那一格，
        所以打印出来的"救回 0 / 误放行 0"是对的 —— **结论没错，错的是判据**；
        但下次只要有一道题被救回或被弄坏，报告就会说反。

    真值表：
        (T, T) → 不变          (T, F) → ★救回
        (F, T) → ⚠ 弄坏        (F, F) → 不变
    """
    return (base_refuse and not agent_refuse), ((not base_refuse) and agent_refuse)


def _self_check() -> None:
    """四格真值表自检。

    ★ 照本仓规矩：**判据必须能证明它在坏情况下会响**。
      把 `_classify` 改回错的写法，这里立刻断言失败 —— 所以这不是装饰。
    """
    want = {
        (True,  True):  (False, False),
        (True,  False): (True,  False),   # 基线拒、多查后放行 = 救回
        (False, True):  (False, True),    # 基线放、多查后拒 = 弄坏
        (False, False): (False, False),
    }
    for (b, a), exp in want.items():
        got = _classify(b, a)
        assert got == exp, f"判据错：(base拒={b}, agent拒={a}) 得到 {got}，应为 {exp}"
    print("✓ 判据自检：四格真值表全部正确（改坏它会立刻在这里报错）")


def main() -> None:
    _self_check()
    qs = json.loads((REPO_ROOT / "evalset" / "questions.json").read_text(encoding="utf-8"))
    refuse = [q for q in qs if q["type"] == "refuse"]
    # ★ 只跑离线层没拦住的那两道 + 一道对照组。
    #   对照组很重要：多查如果对"本来就该拒"的题也乱翻，说明它没有判别力。
    targets = [q for q in refuse if q["id"] in ("q16", "q20")]
    control = [q for q in refuse if q["id"] in ("q15", "q17", "q19")]

    svc.rag.startup()
    logger.info("入库 %d 块", len(svc.rag.chunks))

    if not os.environ.get("DEEPSEEK_API_KEY"):
        logger.error("没有 DEEPSEEK_API_KEY，本实验需要真实模型调用，不做假跑。")
        return

    results = {}
    for group, items in (("实验组：离线层漏掉的两道", targets),
                         ("对照组：本来就该拒的三道", control)):
        print("=" * 72)
        print(group)
        print("=" * 72)
        for q in items:
            print(f"\n--- {q['id']} {q['question']}  [{q.get('boundary') and 'boundary' or 'clear'}]")
            # 对照：主流程现状（一轮查完）
            cands = svc.rag.search(q["question"], k=TOP_K)
            ranked = svc.rag.rerank(q["question"], cands)
            base_score = ranked[0][2] if ranked else 0
            base_doc = (f"{ranked[0][0]['doc']}｜{ranked[0][0]['clause']}"
                        if ranked else "(空)")
            base_refuse = base_score < REJECT_BELOW
            print(f"  现状（1 轮）：精排 {base_score} 分 "
                  f"{'→ 拒答' if base_refuse else '→ 放行'}　命中 {base_doc}")

            agent = run_agent_multi_search(svc.rag, q["question"])
            print(f"  实验组（最多 {MAX_ROUNDS} 轮）：")
            for t in agent["trace"]:
                if t.get("score") is None:
                    print(f"    第{t['round']} 轮 查询词={t['query']!r}  "
                          f"中止原因：{t.get('note')}")
                    continue
                verdict = {True: "判定【找到了】", False: "判定【还没找到】",
                           None: "未知"}[t["found"]]
                print(f"    第{t['round']} 轮 查询词={t['query']!r}")
                print(f"            精排 {t['score']} 分· {t.get('top_doc','')}")
                print(f"            {verdict}"
                      + (f"，下一步查：{t['next_query']!r}" if t["next_query"] else ""))
            last = agent["trace"][-1]
            agent_refuse = last.get("found") is not True
            print(f"  ⇒ 多查后的最终判断：{'拒答' if agent_refuse else '放行'}"
                  f"（用了 {agent['rounds']} 轮）")

            rescued, broke = _classify(base_refuse, agent_refuse)
            results[q["id"]] = {
                "question": q["question"],
                "base_score": base_score,
                "base_refuse": base_refuse,
                "agent_rounds": agent["rounds"],
                "agent_refuse": agent_refuse,
                "rescued": rescued,
                "broke": broke,
                "trace": agent["trace"],
            }
            print()

    # ---------------- 汇总 ----------------
    print("=" * 72)
    print("汇总")
    print("=" * 72)
    print(f"{'题':<6}{'现状':<12}{'多查后':<12}{'变化'}")
    for qid, r in results.items():
        change = ("★ 救回" if r["rescued"] else
                  "⚠ 误放行" if r["broke"] else "不变")
        print(f"{qid:<7}{'拒' if r['base_refuse'] else '放':<13}"
              f"{'拒' if r['agent_refuse'] else '放':<13}{change}")
    n_rescued = sum(1 for r in results.values() if r["rescued"])
    n_broke = sum(1 for r in results.values() if r["broke"])
    print()
    print(f"救回 {n_rescued} 道，误放行 {n_broke} 道")
    print()
    print("★ 判读标准（先写好，免得事后找理由）：")
    print("  · 实验组救回 q16/q20 【且】对照组三道仍拒答")
    print("    ⇒ 多查有判别力，值得接进主流程。")
    print("  · 实验组全都没救回")
    print("    ⇒ 库里真没有，多查只是烧 token；接进主流程是净亏。")
    print("  · 对照组被误放行")
    print("    ⇒ 多查没有判别力，它只是在乱翻 —— 接进主流程会制造幻觉。")

    out = REPO_ROOT / "experiments" / "_agent_multi_search_result.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n轨迹已写入 {out}")


if __name__ == "__main__":
    main()
