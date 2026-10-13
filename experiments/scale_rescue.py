#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""实验：语料变大后，多查能不能救回被挤掉的 gold？

★ 这是 scale_probe.py 的续集，回答一个具体问题：
    scale_probe 实测：库从 263 → 2104 块时，gold 的中位排名从 1 退化到 4，
    且有 3 道题（q9/q10/q11）掉出 top5 —— 库里确实有答案，只是没送进 top5。
    **这时候多查才有意义。** 本脚本就测：让它多查，能不能救回这3 道。

★ 为什么必须用真实模型跑，不能用启发式模拟：
    "模型会怎么换查询词"是这件事的核心，启发式（同义词表）测不出这个能力，
    测出来的只是我写死的查表有多好 —— 那是循环，不是 Agent（见 agent_multi_search.py 顶部）。

★ 判读标准（实验前写死）：
    · 救回 ≥2 道 → 多查在【真实语料规模】上有价值，值得接进主流程
    · 救回 0 道 → 语料变大不是主要瓶颈，多查是净亏，该去补【查询规划】
    · 任何一道【误放行】（本来不该答的被答了）→ 多查不可用，无论救回几道
      （这一条优先级最高：造幻觉比漏答严重）

用法：
    /d/Python-project/.venv/Scripts/python.exe experiments/scale_rescue.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "experiments"))
os.environ.setdefault("LANGSMITH_TRACING", "false")

import service as svc  # noqa: E402
import scale_probe as sp  # noqa: E402
# ★ print 里的 ⇒/✓/⚠ 在 cp936 下编不出来（stdout 是管道时）⇒ 崩在打印上。本项目已犯 7 次。
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

FACTOR = 4          # 用 4× 那档（2104 块）—— 掉出 top5 的题出现在这一档
MAX_ROUNDS = 3
TOP_K = 5


DECIDE_PROMPT = """你在帮一个校园政策问答系统找答案。

用户问题：{q}

第 {i} 轮查询词：{query}
查到的资料：
{blocks}

★ 判断标准不是"像不像"，而是"有没有用户问的那个值"：
    问"多少钱"→ 必须有具体金额；
    问"几天/几点"→ 必须有时间值；
    问"怎么办"→ 必须有步骤或流程。
  只是提到相关主题但没给出这个值，等于【没有】。

只输出 JSON：
{{"found": true 或 false, "next_query": "下一个查询词，found=true 时填空字符串"}}

★ next_query 必须是【新的检索角度】，不是原问句的同义改写。"""

DECIDE_SYSTEM = "你是检索质量判断器，只输出 JSON，不写解释。"


def rescue(index, question: str, gold: tuple[str, str]) -> dict:
    """多查最多 MAX_ROUNDS 轮，看 gold 能不能进 top_k。

    ★★ 口径陷阱（这个 bug 实测踩到过，不修会得出完全错误的结论）：
        规模实验用扰动生成副本（`保卫处·…相关1`、`…改写2`），
        它们的 doc 名改了，但【内容就是真条款的同义改写】，
        于是它们会抢占真 gold 的排名位置。
        而如果只做精确匹配（doc+clause 都相等），那些副本一律不算命中，
        gold 会显示成"第 None 名" —— 读起来像"完全找不到"，
        实际上它就排在第 5/6/7 名。

        实测 q10 就是这样：真 gold 排第 8 名，却被报成 None，
        还伴随一句"判定找到了"，看起来自相矛盾。

        ⇒ 所以这里【两路都算】：
          ① 精确命中（原始 doc 名）—— 保守口径
          ② 同源命中（doc 名以 · 分隔后的【前缀】相同）—— 扰动副本也算，
             因为副本内容就是那条真条款，学生拿到答案是一样的
        两个口径都报出来，避免用其中一个掩盖另一个。
    """
    import re
    from langchain_core.messages import SystemMessage, HumanMessage

    def hit_rank(ranked, gd, gc):
        """返回 (精确命中名次, 同源命中名次)；都没有则 (None, None)。"""
        exact = None
        prefix = None
        for i, (c, _v, _s, _r) in enumerate(ranked, 1):
            if c["doc"] == gd and c["clause"] == gc:
                exact = i
                if prefix is None:
                    prefix = i
            elif c["clause"] == gc and c["doc"].split("·")[0] == gd:
                # ★ 副本：文件名以 · 切开后前缀相同、条款号相同
                if prefix is None:
                    prefix = i
        return exact, prefix

    query = question
    seen = set()
    trace = []
    for rnd in range(1, MAX_ROUNDS + 1):
        if query in seen:
            trace.append({"round": rnd, "query": query, "note": "重复查询词"})
            break
        seen.add(query)
        hits = sp.search(index, query, k=TOP_K)
        ranked = svc.rag.rerank(query, hits)
        gold_rank, prefix_rank = hit_rank(ranked, gold[0], gold[1])

        blocks = "\n".join(
            f"  {i}. [精排 {s} 分] {c['doc']}｜{c['clause']}\n     {c['text'][:110]}"
            for i, (c, _v, s, _r) in enumerate(ranked, 1)) or "  （没查到）"
        prompt = DECIDE_PROMPT.format(q=question, i=rnd, query=query, blocks=blocks)
        try:
            resp = svc._invoke_llm(svc.rag.llm, [
                SystemMessage(content=DECIDE_SYSTEM),
                HumanMessage(content=prompt)])
        except Exception as e:  # noqa: BLE001
            trace.append({"round": rnd, "query": query,
                          "note": f"判据调用失败 {type(e).__name__}: {e}"})
            break
        raw = re.sub(r"^```(?:json)?|```$", "", resp.content.strip(), flags=re.M).strip()
        try:
            data = json.loads(raw)
            found, next_q = bool(data.get("found")), (data.get("next_query") or "").strip()
        except Exception:
            # 解析失败【不】当成 found=false —— 那是把故障伪装成结论
            trace.append({"round": rnd, "query": query, "note": f"非 JSON：{raw[:100]}"})
            break

        trace.append({"round": rnd, "query": query, "gold_rank": gold_rank,
                      "prefix_rank": prefix_rank, "found": found,
                      "next_query": next_q})
        if found or not next_q or next_q == query:
            break
        query = next_q

    # 判"救回"用【同源口径】：副本内容就是那条真条款，答出来对学生是同一件事。
    # 两个口径的差别单独报出来，不合并成一个数字。
    ranks = [t["prefix_rank"] for t in trace if t.get("prefix_rank")]
    exact_ranks = [t["gold_rank"] for t in trace if t.get("gold_rank")]
    best = min(ranks, default=None)
    return {"trace": trace,
            "best_gold_rank": min(exact_ranks, default=None),
            "best_prefix_rank": best,
            "rescued": best is not None and best <= TOP_K}


def main() -> None:
    svc.rag.startup()
    base = list(svc.rag.chunks)

    if not os.environ.get("DEEPSEEK_API_KEY"):
        logging.error("需要 DEEPSEEK_API_KEY")
        return

    #先复现 scale_probe 的 4× 场景，并挑出掉出 top5 的题
    chunks = sp.make_scaled_corpus(base, FACTOR)
    index = sp.build_index(chunks)
    print(f"语料放大到 {len(chunks)} 块（{FACTOR}×），比基准 {len(base)} 块")

    qs = json.loads((REPO_ROOT / "evalset" / "questions.json").read_text(encoding="utf-8"))
    ans = [q for q in qs if q["type"] == "answer"]

    # 挑出"库里确实有答案、但第一轮没进 top5"的题 —— 多查该发挥作用的场景
    targets = []
    for q in ans:
        gd, gc = q["gold"][0], q["gold"][1]
        hits = sp.search(index, q["question"], k=TOP_K)
        if not any(c["doc"] == gd and c["clause"] == gc for c, _v in hits):
            # 确认它在 top20 里（真的只是没进 top5，不是压根找不到）
            hits20 = sp.search(index, q["question"], k=20)
            r = next((i for i, (c, _v) in enumerate(hits20, 1)
                      if c["doc"] == gd and c["clause"] == gc), None)
            targets.append((q, r))
    print(f"\n第一轮没进 top5 但确实在库里的：{len(targets)} 道")

    results = []
    for q, first_rank in targets:
        gd, gc = q["gold"][0], q["gold"][1]
        print("\n" + "=" * 66)
        print(f"{q['id']} {q['question']}　（第一轮 gold 排第 {first_rank} 名）")
        print("=" * 66)
        out = rescue(index, q["question"], (gd, gc))
        for t in out["trace"]:
            if t.get("note"):
                print(f"  第{t['round']} 轮 {t['query']!r} 中止：{t['note']}")
                continue
            print(f"  第{t['round']} 轮 {t['query']!r}")
            pref = t.get("prefix_rank")
            exa = t.get("gold_rank")
            if pref is None:
                print("          gold 不在 top5")
            else:
                print(f"          同源gold 排第 {pref} 名"
                      + ("　★ 进 top5 了" if pref <= TOP_K else "")
                      + (f"（原始条目第 {exa} 名）" if exa else "（命中的是扰动副本）"))
            print(f"          判定{'找到了' if t['found'] else '还没找到'}"
                  + (f"，下一步 {t['next_query']!r}" if t["next_query"] else ""))
        print(f"  ⇒ {'★ 救回' if out['rescued'] else '没救回'}"
              f"（同源 gold 最好排第 {out['best_prefix_rank']} 名，"
              f"原始条目最好第 {out['best_gold_rank']} 名）")
        results.append({"id": q["id"], "question": q["question"],
                        "first_rank": first_rank, **out})

    print("\n" + "=" * 66)
    print("汇总")
    print("=" * 66)
    n = len(results)
    ok = sum(1 for r in results if r["rescued"])
    print(f"救回 {ok}/{n}")
    for r in results:
        print(f"  {r['id']} 第一轮第 {r['first_rank']} → 多查后同源第 "
              f"{r['best_prefix_rank']}（原始第 {r['best_gold_rank']}）　"
              f"{'★ 救回' if r['rescued'] else '没救回'}")
    print()
    print("判读：")
    print(f"  救回 ≥2 道 ⇒ 多查在真实语料规模上有价值（当前 {ok}/{n}）")
    print(f"  救回 0 道 ⇒ 语料变大不是主要瓶颈，多查是净亏（当前 {ok}/{n}）")
    print("  ★ 本实验全部是【应答题】，所以不存在误放行风险 ——")
    print("    误放行的风险要靠 agent_multi_search.py 的拒答题测（那边 0 误放行）")
    print()
    print("★ 同源口径 vs 原始口径为什么要分开报：")
    print("    扰动副本（改写/相关N）的内容就是那条真条款，学生拿到是同一件事；")
    print("    但严格按原始文件名数，它们【不算】gold 命中。")
    print("    只报其中一个都会失真：只报原始口径 → 把'排在第5名'说成'完全找不到'；")
    print("    只报同源口径 → 会把'找到一条副本'当成'检索成功'，掩盖精确性问题。")

    out_path = REPO_ROOT / "experiments" / "_scale_rescue_result.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"\n轨迹已写入 {out_path}")


if __name__ == "__main__":
    main()
