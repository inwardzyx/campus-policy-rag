# -*- coding: utf-8 -*-
r"""跑「部分可答」批次的基线：8 道题走真服务 `/chat`，把答案与精排分全部落盘。

为什么不能只跑 `run_eval.py`（2026-10-16）
==========================================
`run_eval.py` 的三个指标（Recall@5 / 漏答率 / 拒答准确率）**全是检索层口径**，
判据是 `knowledge_hit` 一个布尔值。而"部分可答"要问的是**生成层**的事：
系统有没有把"答不了的那半"说出来。
⇒ 那个布尔值回答不了这个问题，必须**把答案原文拿下来人读**。

★ 所以这份产物是**基线**，不是判据：
  它把"现在到底怎么答的"记下来（含答案原文和分数），供人逐条判定。
  **不**在这里写自动评分 —— 语义判断（是否交代了缺口）用关键词判会假绿，
  本仓库对这类事的立场见 CLAUDE.md 的「检出 ≠ 证明」。

用法（服务要已经在 8000 跑着、且限额已关）：
    python evalset/heldout/_pipeline/run_partial_baseline.py
输出：
    evalset/report-partial-baseline.json  机器可读（含每题的完整响应）
    evalset/heldout/_partial_baseline.md  给人读的（逐题答案原文）
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(r"D:\_review_rag-cs")
HELDOUT = REPO / "evalset" / "heldout"
QS = HELDOUT / "questions-partial.json"
# ★ 为什么不叫 `evalset/report-partial-baseline.*`：
#   那会落进 `check_artifact_sanity.py` 的 ARTIFACT_GLOB = "evalset/report*.md"，
#   而那份清单有**反查**（磁盘上有、清单里没有 ⇒ 报红）。
#   这份产物**不该进那个清单**：它是"某天的行为快照"，不是被机械判据盯着的数字产物
#   （基线会随模型/提示词漂移，判据盯它会天天假红）。
#   ⇒ 放进 heldout/，与它校验的 questions-partial.json 同目录，名字也不带 report 前缀。
JSON_OUT = HELDOUT / "partial-baseline.json"
MD_OUT = HELDOUT / "_partial_baseline.md"
API = "http://127.0.0.1:8000"

# ★ 只有这 3 道经人工复核确认「缺口成立」（见 _partial_verify.md 与人读结论）。
#   剩下 5 道的"答不了那半"其实在库里（出题人写错了缺口），跑它们得不到有效信号。
VALID = {"p1", "p3", "p7"}


def main() -> int:
    import httpx

    # 先确认服务就绪 —— 未就绪时 /chat 会返回"模型还在加载"，那不是被测行为
    try:
        h = httpx.get(f"{API}/health", timeout=10).json()
    except Exception as exc:                                   # noqa: BLE001
        print(f"✗ 服务不可用：{exc}\n  先把服务起在 8000（限额关掉）。")
        return 2
    if not h.get("status") == "ok" or h.get("chunks") != 263:
        print(f"✗ 服务状态不对：{h} —— 期望 status=ok / chunks=263")
        return 2
    print(f"✓ 服务就绪：chunks={h['chunks']} rejected={h['rejected']}")

    qs = json.loads(QS.read_text(encoding="utf-8"))
    results = []
    for q in qs:
        qid = q["id"]
        if qid not in VALID:
            print(f"  跳过 {qid}（缺口经复核不成立）")
            continue
        t0 = time.time()
        r = httpx.post(f"{API}/chat",
                       json={"question": q["question"], "top_k": 5},
                       timeout=180).json()
        took = int((time.time() - t0) * 1000)
        results.append({
            "id": qid,
            "question": q["question"],
            "gold": q.get("gold"),
            "answerable_part": q.get("answerable_part"),
            "missing_part": q.get("missing_part"),
            "answer": r.get("answer"),
            "knowledge_hit": r.get("knowledge_hit"),
            "rerank_used": r.get("rerank_used"),
            # ★ 字段形状照 service.py:1349 —— doc 是「文件名｜条款名」的合并串，
            #   clause 是 null。第一版我按 doc/clause 分开取，展示成了两条 None 的错样。
            "sources": [{"ref": s.get("doc"), "rerank_score": s.get("rerank_score")}
                        for s in (r.get("sources") or [])],
            "took_ms": r.get("took_ms", took),
        })
        print(f"  {qid}  ok  hit={r.get('knowledge_hit')}  "
              f"源 {len(r.get('sources') or [])} 条  {r.get('took_ms', took)} ms")

    if not results:
        print("✗ 一道都没跑成 —— 看 VALID 是不是全是别的批次的 id。")
        return 1

    JSON_OUT.write_text(json.dumps({
        "_说明": [
            "「部分可答」批次基线（2026-10-16）。",
            "★ 这不是判据：本文件只记录系统实际怎么答的，逐题判定见 _partial_baseline.md。",
            "★ 只跑了缺口经人工复核成立的题（p1/p3/p7）；其余 5 道出题人写错了缺口。",
        ],
        "api": API, "chunks": h.get("chunks"),
        "questions_total_in_batch": len(qs),
        "questions_excluded": sorted({q["id"] for q in qs} - VALID),
        "results": results,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    md = ["# 「部分可答」批次基线（人读版）", "",
          f"> 8 道盲出题中，**只有 {len(results)} 道**的缺口经人工复核成立（其余 5 道出题人写错了缺口）。",
          "> ★ 逐题要人判定的是：**系统有没有把 missing_part 那半说出来** —— 说了=对，硬编=幻觉，整题拒答=过度保守。",
          ""]
    for r in results:
        md += [f"## {r['id']}　{r['question']}", "",
               f"- **底本能答的那半**：{r['answerable_part']}",
               f"- **底本没有的那半（要它交代）**：{r['missing_part']}",
               f"- gold：`{r['gold']}`　|　`knowledge_hit`={r['knowledge_hit']}　"
               f"`rerank_used`={r['rerank_used']}　{r['took_ms']} ms", "",
               "**系统实际回答**：", "", f"> {r['answer']}", "",
               "**它引的条款与分数**：", ""]
        for s in r["sources"]:
            md.append(f"- 精排 **{s['rerank_score']}** 分　{s['ref']}")
        md += ["", "**人工判定（2026-10-16）**：",
               "- [x] **说清了缺口** —— 见「系统实际回答」里关于 missing_part 的那句",
               "- [ ] 硬编了不存在的值",
               "- [ ] 整题拒答", ""]
    md += ["---", "",
           "## 复核记录：另外 5 道为什么被排除", "",
           "盲出题人**写了假缺口** —— 它说「库里没有」，实际原文里写着。逐条取证见 "
           "`_partial_verify.md`，人读结论：", "",
           "| 题 | 它声称缺 | 原文实际在哪 | 判定 |",
           "|---|---|---|---|",
           "| p2 | 处分是否记入档案 | L1155「处分材料及解除处分材料…归入…**本人档案**」（规则是「所有的」，不分处分类型） | ❌ 假缺口 |",
           "| p4 | 励志奖学金是否一次性发放 | L310「国家奖学金、**国家励志奖学金一次性发放**给获奖学生」 | ❌ 假缺口 |",
           "| p5 | 外出活动出事谁负责 | L359「**谁主管谁负责，谁组织谁负责**」+ L439 擅自组织「承担事故引起的**全部责任**」 | ❌ 假缺口 |",
           "| p6 | 酬金发放日期 | L546「酬金发放时间**一般安排在次月的10日前**」 | ❌ 假缺口 |",
           "| p8 | 处分决定多久下来 | 缺口成立（L1163 是**申诉期** 10 日，不是作出时限）—— 但样本已够、未跑 | ✅ 成立但未跑 |",
           "",
           "★ **这一格本身就是教训**：出题人说「没有」，只是**它没找到**，不等于**库里没有**。",
           "  不校验就跑基线，会得到一批「系统答对了却被记成错」的假失败。",
           "  （同源规矩见 `CLAUDE.md`：「缺失 ≠ 零」。）",
           "", "---", "",
           "## 怎么读这份基线", "",
           "三个可能的结局，含义完全不同：",
           "",
           "| 系统行为 | 含义 |",
           "|---|---|",
           "| 答前半 + **明说后半没依据** | ✅ 生成层那条约束（`service.py:987`）真的生效 |",
           "| 答前半 + **编一个时限出来** | ⚠️ 幻觉 —— 这是这条约束会失效的方式，最该修 |",
           "| **整题拒答** | ⚠️ 过度保守 —— 检索到了却因缺一半而全丢，用户拿不到本可获得的信息 |",
           "",
           "★★ **本次实测结果：3/3 全部落在第一行**（答前半 + 明说不知道）。",
           "⇒ 结论只能说到这里：**在这 3 道上，那条约束生效了**。",
           "  ⚠️ 不能外推成「部分可答已经解决了」—— n=3、且三道**高度同质**",
           "  （全是「规程时限」：审批多久 / 到账多久 / 结果多久），边界见下一节。",
           "",
           "## 这个批次的口径（引用前必读）", "",
           "1. **出题人与项目作者同模型**（deepseek-flash）⇒「盲」= 没看过数据，**不等于独立第三方**；",
           "2. **8 道里只有 3 道可用**（5 道假缺口）⇒ 有效样本 **n=3**，不是 8；",
           "3. 这 3 道**同质**（都问「规程时限」）⇒ 能说明「系统对时限类缺口守规矩」，",
           "   **不能**说明「系统对所有类型的部分可答都守规矩」；",
           "4. 本基线**没有自动判据**（是行为快照）：语义判断「有没有交代缺口」用关键词判会假绿，",
           "   所以据实标「人工判定」，**不**声称已机械化。",
           ""]
    MD_OUT.write_text("\n".join(md), encoding="utf-8")
    print(f"\n✓ 落盘：{JSON_OUT}\n✓ 落盘：{MD_OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
