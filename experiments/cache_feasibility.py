# -*- coding: utf-8 -*-
"""experiments/cache_feasibility.py —— 给 rerank 加缓存，前置可行性检查

**这个实验要回答的不是"缓存能快多少"（那不用测也知道），是"缓存到底该做在哪个键上"。**

★ 为什么必须先量键，不直接写缓存：
    `RAG.rerank()`（service.py:437）的 prompt 里**含 query**（service.py:447）
    ⇒ 精排分【依赖问题】。这意味着 "候选集相同就复用结果" 是错的：
       同一个候选集，问两个不同问题，打分应该不同。
    选错键 = 2434ms 降到 0，但答案悄悄变错，而且【没有任何报错】。

所以要量三件事（每件都可能直接判死这个实验）：

    第 1 步  精确重复率      —— 问题字符串一字不差地重复
    第 2 步  候选集重复率    —— top-5 集合完全一样的有几组
                               （这一项【不能直接当缓存命中数】，见第 5 步的坑）
    第 3 步  ★ 语义相似度分布 —— "同 gold"的题对 vs "不同 gold"的题对，
                               两组余弦能不能分开。分得开 ⇒ 语义缓存可行。

    第 4 步  前置断言        —— 分不开就判实验无效，不硬写结论

★ 纪律（沿用 stale_doc_robustness.py 的教训）：
    这个脚本最容易输出的错误答案是 "0 命中" 和 "命中率 0%"。
    0 长得像 "系统很严谨"，但它其实只说明【这 20 道题是专门造的】，
    不说明真实流量没有重复。结论段会强制把这句话写出来。

★★ 落盘（2026-10-13 补，起因是盲审指出"结论没有留痕"）：
    这个脚本原来**只 print**，于是 README 里那两个数（`0.3136` / `0.7606`）
    一直是"照脚本逻辑推断 + 换算"出来的，**没有实测输出文件**，也没进任何对账判据
    ⇒ 语料一变它就悄悄过期，而"缓存被实验判死"这个结论**经不起追问一句"这数哪来的"**。
    现在它写两份产物（都在 git 里，别人 clone 也能对账）：
      · `evalset/report-cache-feasibility.json` —— 机器读：含**190 对原始余弦**，
        所以对账方可以**自己重算**极值，不必信存下来的数
      · `evalset/report-cache-feasibility.md`  —— 人读：所有数字都从上面那份生成，不手写
    README:1226 那一行的数字由 `scripts/check_readme_numbers.py` **从产物里抽**并核对。

★★ 退出码语义（也改了，理由很实在）：
    原来是 `1 = 判死`（一个**成功的结论**）、`2 = 样本不足`、`0 = 两组可分`。
    任何"跑通 = exit 0"的自动化接上去都会把结论**读反**：
    绿灯的含义恰好是"缓存可行"。现在：
      · `0` = 跑完并把产物落盘了（**判定在产物里**，不靠退出码传）
      · `2` = 前置不成立（样本不足等），产物仍会落盘，但 verdict 写"样本不足"
      · `1` = 真异常（读文件失败、模型起不来……），这种才该被当成"脚本坏了"

跑法（仓库根目录）：
    D:\\Python-project\\.venv\\Scripts\\python.exe experiments/cache_feasibility.py
    不需要 API key：全程只用 embedding 与余弦，一次生成都不调。
"""
from __future__ import annotations

import itertools
import json
import logging
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
QUESTIONS = REPO_ROOT / "evalset" / "questions.json"
REPORT_JSON = REPO_ROOT / "evalset" / "report-cache-feasibility.json"
REPORT_MD = REPO_ROOT / "evalset" / "report-cache-feasibility.md"
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("LANGSMITH_TRACING", "false")

import numpy as np                                   # noqa: E402

import service as svc                                # noqa: E402
# ★ print 里的 ⇒/✓/⚠ 在 cp936 下编不出来（stdout 是管道时）⇒ 崩在打印上。本项目已犯 7 次。
sys.stdout.reconfigure(encoding="utf-8", errors="replace")



# ==================================================================
# 第 1 步 / 第 2 步 / 第 3 步 都要用到的东西
# ==================================================================
def load_questions() -> list[dict]:
    with open(QUESTIONS, "r", encoding="utf-8") as f:
        return json.load(f)


def gold_key(q: dict) -> tuple:
    """这道题的 gold 归一成一个可哈希的键；拒答题没有 gold，返回空元组。"""
    return tuple(q.get("gold") or [])


def top_set(q: dict, k: int = 5) -> frozenset:
    """这个问题的粗捞 top-k 候选集（用 (doc, clause) 标识一个块）。"""
    cands = svc.rag.search(q["question"], k=k)
    return frozenset((c["doc"], c["clause"]) for c, _ in cands)


def embed_all(questions: list[dict]) -> dict:
    """一次把所有问题 embed 成向量，返回 {qid: np.array}。

    为什么一次批量而不是循环里单个调：query_embed 每次调用都有一次模型前向，
    20 个问题分 20 次会慢；批量只多花一点内存。
    """
    texts = [q["question"] for q in questions]
    vecs = list(svc.rag.model.query_embed(texts))
    return {q["id"]: np.asarray(v, dtype="float32") for q, v in zip(questions, vecs)}


def cos(a: np.ndarray, b: np.ndarray) -> float:
    """余弦相似度。bge 的输出已经归一化，这里再点一次保证落在 [-1, 1]。"""
    return float(np.dot(a, b))


# ==================================================================
# 落盘：JSON 是真相源，md 全部由它生成（所以两边不可能漂）
# ==================================================================
def write_artifacts(res: dict) -> None:
    res["generated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    res["exit_code_semantics"] = ("0=跑完并落盘（判定见 verdict）；"
                                 "2=前置不成立（样本不足）；1=脚本真异常")
    REPORT_JSON.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")

    s, d = res["step3_same_gold"], res["step3_diff_gold"]
    md = f"""# 实验报告：能不能给 rerank 加缓存（`experiments/cache_feasibility.py` 的产物）

> ★ 这份文件是**生成的**，不要手改：所有数字来自 `report-cache-feasibility.json`，
> 而 README 里那一行的数字由 `scripts/check_readme_numbers.py` 从这份 JSON **抽取并核对**。
> 重跑：`python experiments/cache_feasibility.py`（不需要 API key）

- 生成时间：{res['generated_at']}
- 语料：入库 **{res['corpus_chunks']}** 块 / 拦下 **{res['corpus_rejected']}** 块
- 题目：**{res['n_questions']}** 道（`evalset/questions.json`）

## 结论

**{res['verdict_zh']}**

## 数字

| 量的是什么 | 实测 |
|---|---|
| 精确重复（问题字符串一字不差） | **{res['step1_exact_duplicate_groups']}/{res['n_questions']}** 组（去重后 {res['step1_unique_questions']} 种问法） |
| 候选集重复（top-5 集合完全相同） | **{res['step2_shared_groups']}/{res['n_questions']}** 组（能"省" {res['step2_n_saved_rerank_calls']} 次精排） |
| 同 gold 题对的余弦 | {s['n']} 对：最小 **{s['min']:.4f}** / 中位 {s['median']:.4f} / 最大 {s['max']:.4f} |
| 不同 gold 题对的余弦 | {d['n']} 对：最小 {d['min']:.4f} / 中位 {d['median']:.4f} / 最大 **{d['max']:.4f}** |
| 两组分得开吗（前置断言） | **{'分得开' if res['step4_separable'] else '重叠 —— 分不开'}** |

## 怎么读这个结论（★ 分层，别把弱的说成强的）

| 内容 | 强度 | 说明 |
|---|---|---|
| `rerank()` 的 prompt 逐字含 query ⇒ 精排分是 (query, candidates) 的函数 ⇒ **候选集复用会静默返回错误排序** | ★★★ **决定性** | **结构性，与流量无关**：换多少流量、换什么题集都成立。**结论只挂在这条腿上** |
| 同 gold 最低余弦 **{s['min']:.4f}** < 不同 gold 最高余弦 **{d['max']:.4f}**（两组重叠） | ★★ 加强 | 说明"靠阈值救"也不成立。数字在 `report-cache-feasibility.json` 里、能从 `pairs_all` **重算** |
| 精确重复 **{res['step1_exact_duplicate_groups']}/{res['n_questions']}** 组 | ★ **限制，不是证据** | 这是**题集设计的必然结果**（评测集本来就要避免重复）。本项目**无对外流量** ⇒ **此项不可观测** |

★ 为什么要分层：把"0/20"当证据，等于把一个**观测能力的边界**说成一条结论 ——
  而「测不了」和「测了没有」是两件完全不同的事。

## 这份实验**测不了**什么（写在这里，免得别人以为漏测了）

- **真实流量的重复率 / 相似问法分布**：本项目**没有对外流量**
  （校园政策 QA，服务未对外部署）⇒ 这一项**在结构上就观测不了**，不是"还没采集"。
  ★ 2026-10-14 更正口径：原来这里写的是"**刻意不记录问题文本**"，
  加多轮会话（`session.py`）之后那句话不再精确 —— 服务端**会在临时存储里持有问题原文**
  （只留最近 5 轮、TTL 30 分钟，仅用于服务本次对话；不进日志、不入持久化库、不做行为分析）。
  ⇒ 准确的说法是「**不留存**」，而不是「不记录」：任何服务都必须持有问题原文才能回答它，
  能承诺也能被验证的是"过期即消失、不拿去分析"。
  ⚠️ 且要如实写：Redis 开了 AOF（compose 里 `--appendonly yes`）⇒ 会话数据会落进容器卷，
  **由 TTL 到期清除**；要更严就给会话换独立实例/db 并关 AOF。
  要谈真实重复率，仍然得先有对外流量 + 一套合规的采集与同意方案。
- 所以上面那个"0/20"**永远不能**被外推成"真实流量里没有重复"（会话数据也**不用于**这个统计）。

## 原始数据

{res['n_pairs']} 对题的全部余弦都在 `report-cache-feasibility.json` 的 `pairs_all` 里
（含 `same_gold` 标记）—— 对账方可以**自己重算**上面那两个极值，不必信这份报告。
"""
    REPORT_MD.write_text(md, encoding="utf-8", newline="")
    print(f"\n[落盘] {REPORT_JSON.relative_to(REPO_ROOT)}")
    print(f"[落盘] {REPORT_MD.relative_to(REPO_ROOT)}")
    print("       （README 那一行的数字由 check_readme_numbers.py 从 JSON 抽取核对）")


# ==================================================================
# 主流程
# ==================================================================
def main() -> int:
    qs = load_questions()
    svc.rag.startup()
    res: dict = {
        "n_questions": len(qs),
        "corpus_chunks": len(svc.rag.chunks),
        "corpus_rejected": len(svc.rag.rejected),
        # ★★ 观测能力的边界（2026-10-13 补，起因是一次"把题集当流量"的纠正）：
        #   这个仓库**没有对外流量**（校园政策 QA，服务未对外部署）。
        #   ⇒ 所谓"精确重复率"在本项目里**不可观测**：`0/20` 只说明"这个自造题集内部没有重复"，
        #     不构成关于真实流量的任何结论。
        #   ★ 写进产物而不是只写在 README 里：产物是这份结论的**出处**，出处必须自带边界。
        #
        # ★★★ 2026-10-14 更正：`question_text_logged` 由 False 改成 **True**。
        #   起因：加多轮会话（`session.py`）之后，服务端**会在临时存储里持有问题原文**
        #   （Redis / 进程内，TTL 30 分钟、只留最近 5 轮）。
        #   ⇒ 原来那句"刻意不落盘"不再精确，这条边界必须重新划：
        #        **不留存**（不进日志、不入持久化库、不做行为分析），而不是"不记录"。
        #     理由：任何服务都必须持有问题原文才能回答它（至少在请求生命周期内），
        #     所以"不记录"本来就是个不精确的说法；能承诺、也能被验证的是"不留存"。
        #   ⚠️ 同时如实写出 **Redis 的 AOF**：compose 里 `--appendonly yes`（为验"重启后额度还在"），
        #     所以会话数据会落进容器卷，**由 TTL 到期清除**。这条不能装作没有 ——
        #     部署时若要更严，给会话换独立实例/db 并关掉 AOF。
        "observability": {
            "has_real_traffic": False,
            "why": "校园政策 QA，服务未对外部署 ⇒ 结构上没有对外流量",
            "question_text_logged": True,
            "logged_scope": ("多轮会话的临时存储：只存最近 5 轮的**问题原文**，"
                             "TTL 30 分钟，仅用于服务本次对话；"
                             "不进日志、不入持久化库、不做行为分析"),
            "disk_note": ("⚠️ Redis 开了 AOF（compose 里 --appendonly yes）⇒ 会话数据会落进"
                          "容器卷，由 TTL 到期清除。要更严：会话用独立实例/db 并关 AOF。"),
            "consequence": ("精确重复率仍不可观测（会话数据不用于分析）；"
                            "step1 的 0/20 只在『本自造题集内』成立"),
        },
    }
    print(f"[前置] 入库 {res['corpus_chunks']} 块，拦下 {res['corpus_rejected']} 块")
    print(f"[前置] 题目 {res['n_questions']} 道\n")

    # ---------------- 第 1 步：精确重复率 ----------------
    print("=" * 68)
    print("第 1 步　精确重复率（问题字符串一字不差地重复）")
    print("=" * 68)
    seen: dict[str, list[str]] = {}
    for q in qs:
        seen.setdefault(q["question"].strip(), []).append(q["id"])
    dup_groups = {k: v for k, v in seen.items() if len(v) > 1}
    res["step1_unique_questions"] = len(seen)
    res["step1_exact_duplicate_groups"] = len(dup_groups)
    print(f"去重后 {len(seen)} 种问法 / 共 {len(qs)} 道题")
    if dup_groups:
        for text, ids in dup_groups.items():
            print(f"  重复：{ids} → {text}")
    else:
        print("精确重复：0 组")
    print("\n★ 结论：精确字符串缓存【在本题集上】不可能有命中。")
    print("  ⚠️ 这句话的适用范围（别读成『真实流量里没有重复』）：")
    print("     · 评测集本来就要避免重复，否则等于送分 ⇒ 0 是**题集设计的必然结果**；")
    print("     · 本项目**无对外流量**（校园政策 QA，未部署）⇒ 真实重复率**在结构上不可观测**；")
    print("       会话临时存储（TTL 30 分钟、只留 5 轮）**不用于**这个统计。")
    print("     ⇒ 这一项是【观测能力的边界】，不是证据。判死的依据在第 3/4 步。\n")

    # ---------------- 第 2 步：候选集重复率 ----------------
    print("=" * 68)
    print("第 2 步　候选集重复率（top-5 集合完全一样的有几组）")
    print("=" * 68)
    tsets = {q["id"]: top_set(q) for q in qs}
    by_set: dict[frozenset, list[str]] = {}
    for qid, s in tsets.items():
        by_set.setdefault(s, []).append(qid)
    shared = {s: ids for s, ids in by_set.items() if len(ids) > 1}
    id2q = {q["id"]: q for q in qs}
    n_saved = sum(len(ids) - 1 for ids in shared.values())
    res["step2_distinct_top5_sets"] = len(by_set)
    res["step2_shared_groups"] = len(shared)
    res["step2_n_saved_rerank_calls"] = n_saved
    print(f"不同的 top-5 集合：{len(by_set)} 种 / {len(qs)} 道题")
    print(f"共享同一集合的组：{len(shared)} 组")
    for s, ids in shared.items():
        golds = {i: gold_key(id2q[i]) for i in ids}
        same = len(set(golds.values())) == 1
        mark = "gold 相同" if same else "★ gold 不同"
        print(f"  {ids} → {mark}")
        for i in ids:
            print(f"      {i}: {id2q[i]['question']}")
    print(f"\n如果（错误地）按候选集复用，能省 {n_saved} 次精排调用 "
          f"（{len(qs)} → {len(by_set)} 次，少 {n_saved / len(qs) * 100:.0f}%）。")
    print("★ 但这个数【不是】能省的钱 —— 见第 5 步，那里有个正确性陷阱。\n")

    # ---------------- 第 3 步：★ 语义相似度分布 ----------------
    print("=" * 68)
    print("第 3 步　★ 语义相似度分布：同 gold 的题对 vs 不同 gold 的题对")
    print("=" * 68)
    vecs = embed_all(qs)
    ids = [q["id"] for q in qs]

    pairs_all: list[dict] = []
    same_pairs, diff_pairs = [], []
    for a, b in itertools.combinations(ids, 2):
        s = cos(vecs[a], vecs[b])
        same = gold_key(id2q[a]) == gold_key(id2q[b])
        pairs_all.append({"a": a, "b": b, "cos": round(s, 6), "same_gold": same})
        (same_pairs if same else diff_pairs).append((a, b, s))
    res["n_pairs"] = len(pairs_all)
    res["pairs_all"] = pairs_all

    def stat(name, pairs, key):
        if not pairs:
            print(f"  {name}：0 对（无法比较）")
            res[key] = {"n": 0, "min": None, "median": None, "max": None}
            return None
        vals = sorted(p[2] for p in pairs)
        n = len(vals)
        med = vals[n // 2]
        print(f"  {name}：{n} 对　最小 {vals[0]:.4f}　中位 {med:.4f}　最大 {vals[-1]:.4f}")
        res[key] = {"n": n, "min": round(vals[0], 6), "median": round(med, 6),
                    "max": round(vals[-1], 6)}
        return vals

    print(f"\n共 {len(ids) * (len(ids) - 1) // 2} 对题：")
    vs = stat("同 gold", same_pairs, "step3_same_gold")
    vd = stat("不同 gold", diff_pairs, "step3_diff_gold")

    # ---------------- 第 4 步：★ 前置断言 ----------------
    print("\n" + "=" * 68)
    print("第 4 步　★ 前置断言：两组分不分开？分不开就判实验无效")
    print("=" * 68)
    if not vs or not vd:
        print("样本不足，无法判断（本次不出结论）。")
        res["step4_separable"] = None
        res["verdict"] = "insufficient_samples"
        res["verdict_zh"] = "样本不足，这次不出结论（不能靠『看起来像』硬写一个）"
        write_artifacts(res)
        return 2
    # 分开的判据：同 gold 的【最小值】必须高于不同 gold 的【最大值】。
    # 只有完全 separable 才敢谈阈值；重叠就得靠更多数据，不能靠调阈值硬凑。
    sep = vs[0] > vd[-1]
    res["step4_separable"] = bool(sep)
    print(f"同 gold 最低 {vs[0]:.4f}　vs　不同 gold 最高 {vd[-1]:.4f}")
    if not sep:
        print("\n★★ 实验结论：两组【重叠】⇒ 靠余弦阈值分不开同义问法和不同问法。")
        print("   ⇒ 语义缓存【不可行】，任何阈值都会误命中，直接判死。")
        print("   ⇒ 要做只能上 LLM 判等价（更贵）或人工定白名单，不在本实验范围。")
        res["verdict"] = "overlap_kills_semantic_cache"
        res["verdict_zh"] = ("判死：同 gold 与不同 gold 两组余弦【重叠】"
                             "⇒ 任何阈值都会误命中，语义缓存不可行")
        write_artifacts(res)
        return 0                      # ★ 0 = 跑完并落盘（判定在产物里，见 docstring）
    print("\n两组完全 separable ⇒ 阈值可选，落在中间即可。")

    print("\n各阈值下的语义缓存表现（命中 = 与已问过的某题余弦 ≥ 阈值）：")
    print(f"  {'阈值':>6}  {'命中数':>6}  {'命中率':>8}  {'误命中':>6}  说明")
    best = None
    sweep = []
    for th in (0.99, 0.97, 0.95, 0.92, 0.90, 0.85, 0.80):
        hit = wrong = 0
        order = [q["id"] for q in qs]
        cache: list[str] = []
        for qid in order:
            bests = max((cos(vecs[qid], vecs[c]) for c in cache), default=0.0)
            if bests >= th:
                hit += 1
                # 误命中 = 命中的那道题和它不是同一个 gold ⇒ 答案会错
                hit_ids = [c for c in cache if cos(vecs[qid], vecs[c]) >= th]
                if any(gold_key(id2q[c]) != gold_key(id2q[qid]) for c in hit_ids):
                    wrong += 1
            else:
                cache.append(qid)
        rate = hit / len(qs) * 100
        note = "安全" if wrong == 0 else f"⚠ 有 {wrong} 次答案会错"
        print(f"  {th:>6.2f}  {hit:>6}  {rate:>7.1f}%  {wrong:>6}  {note}")
        sweep.append({"threshold": th, "hits": hit, "wrong": wrong})
        if wrong == 0 and (best is None or hit > best[1]):
            best = (th, hit, rate)
    res["step5_threshold_sweep"] = sweep
    if best:
        print(f"\n★ 最优安全阈值 {best[0]:.2f}：命中 {best[1]}/{len(qs)}（{best[2]:.1f}%），0 误命中。")
        res["verdict"] = "separable"
        res["verdict_zh"] = (f"两组可分：最优安全阈值 {best[0]:.2f}，"
                             f"命中 {best[1]}/{len(qs)}，0 误命中")
    else:
        res["verdict"] = "separable_but_no_safe_threshold"
        res["verdict_zh"] = "两组可分，但扫过的阈值里没有一条做到 0 误命中"
    write_artifacts(res)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    raise SystemExit(main())
