# -*- coding: utf-8 -*-
"""evalset/probe_rerank_threshold.py —— 产品默认路径（精排分 < 5）能不能靠调阈值救？

背景：离线层（单一向量分 0.55）已经在两个分布上被证明**无解**
（`probe_threshold.py`：主集交叉 0.035，held-out 交叉 0.26）。
但产品默认走的是**在线层**：`use_rerank=true`，拒答判据是 `精排分 < 5`。

★★ 2026-10-14 更正（本文件原先写的是「那个 5 是怎么来的，全仓库没有答案」——**那句已过期**）：
  那个 5 是 **2026-09-23** 定的（`git log -S rerank -- service.py` → `b89cce7`），
  而**本探针 2026-10-10 21:22 就跑过了**（落盘：`git show 7a445b2`；分布与阈值扫描见
  `evalset/heldout/RESULTS.md` 的〈阈值扫描〉一节）。
  ⇒ **先拍后量，隔了 17 天** —— 这才是准确的说法：不是"从没量过"，是"当时没量、后来补量了"。
  ⚠️ 本文件顶部的原话与 README 里同一句都**没跟上**，属于"过期陈述"（本项目最剿的那类东西）。

这个探针就干一件事：把每个问题的**最高精排分**量出来，
然后扫一遍阈值，回答两个问题：
  1. 真实分布上，精排分能不能分开"该答"和"该拒"？
  2. `5` 是不是一个好的操作点？最好的操作点在哪、代价是什么？

★ 已量出的结论（`RESULTS.md:308-315`）：
  主集上两类**完美分开**（应答题 min **9.0** > 拒答题 max **3.0**）⇒ 所以 5 在主集上"看着很对"；
  held-out 上**交叉**，扫描找不到免费的操作点（t=5.0 拒答 39/61、误拒 5/44；
  t=8.5 拒答 53/61、误拒 8/44，而漏答率是硬指标）。

★ 只调精排、不生成答案（省掉一半调用）；105 题 ≈ 105 次调用。

跑法：<py> evalset/probe_rerank_threshold.py --questions evalset/heldout/questions.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import os  # noqa: E402

os.environ.setdefault("LANGSMITH_TRACING", "false")

import service as svc  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--questions", default=str(REPO / "evalset" / "questions.json"))
ap.add_argument("--k", type=int, default=5, help="粗捞池大小（产品当前 =5）")
A = ap.parse_args()

data = json.loads(Path(A.questions).read_text(encoding="utf-8"))
qs = data.get("questions") if isinstance(data, dict) else data
ans = [q for q in qs if q.get("type") == "answer"]
ref = [q for q in qs if q.get("type") == "refuse"]

svc.rag.startup()
print(f"\n题目 {A.questions}　应答题 {len(ans)}　拒答题 {len(ref)}　粗捞池 k={A.k}\n")

scores: dict[str, dict[str, float]] = {"answer": {}, "refuse": {}}
for grp, items in (("answer", ans), ("refuse", ref)):
    for q in items:
        cands = svc.rag.search(q["question"], k=A.k)
        try:
            ranked = svc.rag.rerank(q["question"], cands)
            top = max((s for _, _, s, _ in ranked), default=0)
        except Exception as e:                       # 精排失败：记 -1，别当成 0
            print(f"  ⚠ {q['id']} 精排失败：{type(e).__name__}: {str(e)[:80]}")
            top = -1
        scores[grp][q["id"]] = float(top)
    vals = sorted(scores[grp].values())
    if vals:
        n = len(vals)
        print(f"  {grp:6s} n={n:3d}　min={vals[0]:5.1f}　中位={vals[n//2]:5.1f}　max={vals[-1]:5.1f}")

# ---------- 扫描：阈值 t 表示"精排分 < t 就拒答" ----------
print(f"\n{'阈值':>5} {'拒答拦住':>9} {'误拒应答题':>11}  说明")
best = None
for t in [x / 2 for x in range(2, 21)]:          # 1.0 ~ 10.0，步长 0.5
    caught = sum(1 for v in scores["refuse"].values() if v < t)
    false_rej = [qid for qid, v in scores["answer"].items() if v < t]
    if best is None or (caught - len(false_rej)) > best[1]:
        best = (t, caught - len(false_rej), caught, len(false_rej))
    mark = "  ← 当前生产用 5" if abs(t - 5.0) < 1e-9 else ""
    print(f"{t:>5.1f} {caught:>5}/{len(ref):<3} {len(false_rej):>8}/{len(ans):<3}{mark}")

print(f"\n按「拦住的拒答 − 误拒的应答」净分最高的一档：t={best[0]}"
      f"（拦住 {best[2]}/{len(ref)}，误拒 {best[3]}/{len(ans)}，净 {best[1]}）")
cur_caught = sum(1 for v in scores["refuse"].values() if v < 5)
cur_false = sum(1 for v in scores["answer"].values() if v < 5)
print(f"当前生产 t=5：拦住 {cur_caught}/{len(ref)}，误拒 {cur_false}/{len(ans)}，"
      f"净 {cur_caught - cur_false}")
