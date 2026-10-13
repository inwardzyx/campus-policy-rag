#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""配对实验：离线层 vs 在线层，拒答判据到底是不是超集关系。

★ 为什么要单独写这个脚本（这是 DSH 独立审稿逼出来的）：

  我最初打算这样报成绩：
      "离线层拒答 3/5，在线层拒答 5/5，在线层更好。"
  DSH 指出这个报法有三个问题：
    ① 【指标不对称】离线层和在线层用的是两套判据，
       两个比例不是同一个量，不能直接比大小；
    ② 【缺配对表】两个边际比例说明不了"在线层是不是把离线层拒的题也拒了"。
       如果在线层换了3 道别的题，看起来也是 5/5，实际是退步；
    ③ 【n=5 太小】3/5 和 5/5 的置信区间几乎完全重叠，
       统计上根本区分不开。

  所以这个脚本只做一件事：把【同一批题】分别过两层，
  然后算出配对表（2×2）+ Wilson 置信区间 + McNemar 检验。

结论（2026-10-10 实测，见本文档末尾）：
  在线层是离线层的真超集，0 漏 ⇒ DSH 担心的"换了别的题"没有发生。
  但 n=5 的统计现实依然成立，这是方向性证据，不是结论。

用法：
    /d/Python-project/.venv/Scripts/python.exe evalset/probe_pair_layers.py

★ 本脚本【不需要 API key】：在线层那一列是通过实跑 run_eval.py --with-llm
  得到的真实结果，写在 _ONLINE_REFUSE 里。为了让这个脚本能离线复现，
  这里硬编码了那次实测的逐题结果。
  ——这是本脚本最大的弱点：在线层数字不会自动更新。
  改 questions.json 或改 service.py 的 prompt 之后，必须重跑
  run_eval.py --with-llm 并手工更新下面的 _ONLINE_REFUSE，
  否则这个脚本会拿旧数字骗你。
  （这是本脚本接受的"已知欠账"，不是"已解决"。）
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
import sys
# ★ print 里的 ⇒/✓/⚠ 在 cp936 下编不出来（stdout 是管道时）⇒ 崩在打印上。本项目已犯 7 次。
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


REPO_ROOT = Path(__file__).resolve().parents[1]
QUESTIONS = REPO_ROOT / "evalset" / "questions.json"

# ============================================================
# 2026-10-10 在线层实测快照（真实 DEEPSEEK_API_KEY 跑出来的）
# 来源：evalset/run_eval.py --with-llm --report
# ★ 硬编码的代价见本文件顶部说明。
# ============================================================
_ONLINE_REFUSE = {
    # qid: (是否拒答, top1 精排分, 模型给的理由)
    "q15": (True,  None, "无相关资料，库里没有图书馆作息时间"),
    "q16": (True,  3, "提到新生资助最高6000元，间接暗示学费可能不超过或超过6000元，"
                      "但未给出具体学费标准"),
    "q17": (True,  None, "无相关资料"),
    "q19": (True,  None, "无相关资料，库里没有宿舍熄灯时间"),
    "q20": (True,  0, "该资料讲的是转借、冒用证件的处分规定，与补办学生证流程无关"),
}

# 离线层实测（向量分 ≥ 0.55 就放行）
# 来源：evalset/report-vector.md，可用 run_eval.py --report 复现
_VECTOR_REJECT = 0.55


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score区间（不是正态近似区间）。

    n 小的时候正态近似会给出越界或者离谱的范围，Wilson 在 n=5 上还算靠谱。
    公式：见 NIST / Newcombe 1998。
    """
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z / denom) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, center - half), min(1.0, center + half))


def mcnemar_exact(b: int, c: int) -> float:
    """双侧精确 McNemar检验（binomial 检验，不用卡方近似）。

    b/c 是不一致的对数。这个检验的检定功效很低——
    n=5 时不一致对只有2 个，p 最小也只能到 0.5，
    所以这里算它只是为了【如实告诉读者"这个证据不显著"】，
    而不是指望它能给出显著性。
    """
    n = b + c
    if n == 0:
        return 1.0
    # P(X >= max(b,c)) * 2，X ~ Binom(n, 0.5)
    k = max(b, c)
    tail = sum(math.comb(n, i) for i in range(k, n + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def main() -> None:
    qs = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    refuse = [q for q in qs if q["type"] == "refuse"]

    print("=" * 64)
    print("配对实验：同一批拒答题，离线层（向量分）vs 在线层（精排分）")
    print("=" * 64)
    print(f"拒答题 n = {len(refuse)}")
    print()

    # ---------- 离线层：实跑向量分（这个能真跑，不依赖 key）----------
    rows = []
    import sys
    sys.path.insert(0, str(REPO_ROOT))
    import service as svc  # noqa: E402

    svc.rag.startup()
    for q in refuse:
        # rag.search() 返回 [(chunk_dict, 向量分), ...]
        #   —— 注意返回的是【元组】，不是 dict；而且顺序由 RRF 决定，
        #      那个向量分只用于展示，不是排序依据（见 service.py search 的注释）。
        hits = svc.rag.search(q["question"], k=5)
        top, score = hits[0] if hits else (None, 0.0)
        score = float(score)
        # 离线层判据：向量分 >= 阈值 ⇒ 放行（不拒答）
        offline_refused = score < _VECTOR_REJECT
        online = _ONLINE_REFUSE.get(q["id"])
        if online is None:
            print(f"  ⚠ _ONLINE_REFUSE 缺少 {q['id']} 的在线层快照，"
                  f"请重跑 run_eval.py --with-llm 并更新本文件")
            return
        rows.append({
            "id": q["id"],
            "q": q["question"],
            "top1": top["text"][:38] if top else "(空)",
            "vec": score,
            "off_refuse": offline_refused,
            "on_refuse": online[0],
            "rerank": online[1],
            "reason": online[2],
        })

    # ---------- 逐题对照 ----------
    print("逐题对照")
    print("-" * 64)
    print(f"{'题':<5}{'向量分':<9}{'离线':<7}{'精排分':<8}{'在线':<7}备注")
    for r in rows:
        vec_f = f"{r['vec']:.4f}"
        rr = "--" if r["rerank"] is None else str(r["rerank"])
        print(f"{r['id']:<6}{vec_f:<10}{'拒' if r['off_refuse'] else '放':<8}"
              f"{rr:<9}{'拒' if r['on_refuse'] else '放':<8}{r['reason'][:26]}")

    # ---------- 2×2 配对表 ----------
    both = sum(1 for r in rows if r["off_refuse"] and r["on_refuse"])
    off_only = sum(1 for r in rows if r["off_refuse"] and not r["on_refuse"])
    on_only = sum(1 for r in rows if not r["off_refuse"] and r["on_refuse"])
    neither = sum(1 for r in rows if not r["off_refuse"] and not r["on_refuse"])

    print()
    print("=" * 64)
    print("2x2 配对表（DSH 要求的关键证据：看的是超集关系，不是边际比例）")
    print("=" * 64)
    print(f"{'':<22}{'在线也拒':<14}{'在线放行':<14}")
    print(f"{'离线拒':<20}{both:<16}{off_only:<14}")
    print(f"{'离线放行':<20}{on_only:<16}{neither:<14}")
    print()
    is_superset = (off_only == 0)
    print(f"★ 在线 ⊇ 离线？ {is_superset}")
    if is_superset:
        print(f"  （离线拒的 {both} 道在线全拒，漏掉 {off_only} 道）")
        print("  ⇒ 两层不是\"各拒各的\"，在线层确实是离线层的严格加强。")
    else:
        print(f"  ⚠ 在线层漏掉了离线层拒的 {off_only} 道 ⇒ 不是超集，"
              f"报\"在线 5/5\"是误导。")
        for r in rows:
            if r["off_refuse"] and not r["on_refuse"]:
                print(f"     {r['id']} {r['q']}")

    # ---------- Wilson 区间 ----------
    n = len(rows)
    k_off = both + off_only
    k_on = both + on_only
    lo_off, hi_off = wilson(k_off, n)
    lo_on, hi_on = wilson(k_on, n)

    print()
    print("=" * 64)
    print(f"n = {n} 的统计现实（为什么这只能算方向性证据）")
    print("=" * 64)
    print(f"  离线层 {k_off}/{n}　95% Wilson 区间 = [{lo_off:.2f}, {hi_off:.2f}]")
    print(f"  在线层 {k_on}/{n}　95% Wilson 区间 = [{lo_on:.2f}, {hi_on:.2f}]")
    overlap = not (hi_off < lo_on or hi_on < lo_off)
    if overlap:
        print("  ⇒ 两个区间重叠 ⇒ 两层的差异【统计上不可区分】。")
        print("     n=5 时这是必然的：再多的信心也变不出信息量。")
    p = mcnemar_exact(off_only, on_only)
    print(f"  McNemar 精确检验：不一致对 {off_only + on_only} 个，p = {p:.3f}")
    if p > 0.05:
        print(f"  ⇒ p > 0.05，不显著。检验的功效被n={n} 限死了。")

    # ---------- 可证伪的下一步 ----------
    print()
    print("=" * 64)
    print("要把这个结论变成结论，需要什么")
    print("=" * 64)
    print(f"  拒答集每类 ≥10 条（DSH 建议的6 类）⇒ n≥60")
    print(f"  当前 n={n}，按最保守的 Wilson 宽度，要让区间不重叠至少需要 n≈30/类。")
    print("  另一个便宜的办法：把【在线层精排分的分布】当连续量报，")
    print("  不二值化成拒/放——5 道题的精排分是 3/0/--/--/--，")
    print("  边际极差比 0/1 的二值信息多得多。")

    print()
    print("★ 已知欠账：_ONLINE_REFUSE 是硬编码的离线快照。")
    print("  改了 prompt 或题目之后必须重跑 run_eval.py --with-llm 并手工更新它，")
    print("  否则本脚本会拿旧数字骗人。见本文件顶部说明。")


if __name__ == "__main__":
    main()