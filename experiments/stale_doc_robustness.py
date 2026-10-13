#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
脏数据抗性实验：故意把【已废止的旧规定】混进检索库，看系统会不会引用它们。

★ 为什么要做这个实验（动机，不是事后找理由）：
  真实场景里，"混进来的脏数据"有两种来源：
    ① 网页抓取残留（导航菜单、"智能校务平台"这类）—— 本项目已经处理过，15 条在 docs/inbox/
    ② **已废止的旧规定**—— 学校手册滞后于上位法、手册滞后于口头通知，都会留下这类东西。
       ★ 这一类比 ① 危险得多，因为它【格式规范、有文号、有条款编号】，看起来特别可信，
         RAG 会自信地引用它并给出条款号—— 而用户完全看不出来它是废的。
  问题是：这种脏数据能不能被拦住？如果拦不住，说明项目的护栏有缺口。

★ 这个实验的性质是【制造已知答案的问题】，答案不是"好/坏"，而是可测的：
  混入的旧规定，主题上跟现行规定高度重叠（都是课程考核/请假）。
  所以系统【很可能】会检索到它们。真正要测的是：
  ① 它会不会被检索到？          → 预期会（主题重叠是设计使然）
  ② 它会不会被【引用并输出】？  → 这才是关键
  ③ 精排（rerank）能不能把它压下去？

★ 诚实声明（很重要）：
  本实验【不】主张"本项目能识别废止条款"。恰恰相反，它大概率不能。
  ★ 废止信息写在文档里，但检索层看不出"这条已废止"与"这条现行"有什么不同——
     两者都是格式完美的中文规章，embedding 眼里一样。
  所以本实验的真实产出是：
    - 如果系统引用了废条款 → **证明"混语料就有风险"**，本项目的设计选择（少而精、
      宁可拒答）是被这个实验支持的，不是拍脑袋。
    - 如果系统没引用 → 说明护栏（精排/阈值）意外地起作用了，是个真实的强项。
  ★ 两种结果都有价值。**不敢做出已知答案的实验，才是真的没价值。**

跑法：cd 到仓库根目录，然后
    python experiments/stale_doc_robustness.py
"""

# ==================================================================
# ★★ 冻结注记（2026-10-12 加）—— 本文件的逻辑与期望值【一律不动】
# ==================================================================
# 为什么不动：本脚本是 README〈上面五道关卡拦不住什么〉那组数字
# （`5/9`、`2/3`、块数 `68 → 78`）的**产生源**，引用链必须保住。
# 把它的期望值改成"修复后"的样子，等于把脚本悄悄换成了另一个东西
# ——README 与 experiments/_脏数据抗性实验.md 都还指着它当出处。
#
# ★ 它现在跑起来会发生什么（预期行为，不是坏了）：
#   等 `status: 现行/已废止` 那道入库关卡上线之后，夹具
#   `experiments/stale_docs/` 会带上 `status: 已废止`，于是样本会被新关卡拦下，
#   **块数不再变化** ⇒ 本脚本会走进下面那个 `★★ 实验无效：混入后块数没变`
#   分支并 return。那是正常的（它自己的清理逻辑在那段里照常执行，见第 130-138 行）。
#   想验"修复后确实拦住了"，去跑 `experiments/stale_status_guard.py`（两臂含负对照）。
#
# ★ 口径说明：本脚本的原始数字产生时，`docs/kb/` 只有 2 份文件、全库 68 块；
#   今天 `docs/kb/` 是 10 份、全库 263 块。两者不可混排（README 那张表已加注）。
# ==================================================================

import os
import sys
import shutil
import tempfile
from pathlib import Path
# ★ print 里的 ⇒/✓/⚠ 在 cp936 下编不出来（stdout 是管道时）⇒ 崩在打印上。本项目已犯 7 次。
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# ---------- 路径 ----------
ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

# 已废止的旧规定，放这里；脚本会临时拷进 docs/kb/ 再跑，跑完删掉。
STALE_DIR = Path(__file__).resolve().parent / "stale_docs"


def hr(title):
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def main():
    # ---------------------------------------------------------------
    # 第 0 步：把主服务搬进来（它会sys.path 处理，直接 import 就行）
    # ---------------------------------------------------------------
    sys.path.insert(0, str(ROOT))
    # ★ 必须在 import service 之前调用：uuid_utils 的 DLL 被应用控制策略拦，
    #   凡import langchain 的模块都起不来。详见 env_compat 的 docstring。
    import env_compat
    env_compat.ensure_uuid_utils()
    from service import RAG, VEC_REJECT_THRESHOLD

    print(f"拒答阈值 VEC_REJECT_THRESHOLD = {VEC_REJECT_THRESHOLD}")
    print("（README 记录过这个值曾经失效；0.55 是在【当前语料】上量的，"
          "语料一变分布就变——这本身就是一个要复测的理由）")

    # ---------------------------------------------------------------
    # 第 1 步：干净基线（不混脏数据）
    # ---------------------------------------------------------------
    hr("第1 步：干净基线—— 只用 docs/kb/ 现有的 2 份现行规定")
    clean = RAG()
    clean.startup()
    print(f"入库 {len(clean.chunks)} 块，拦下 {len(clean.rejected)} 块，"
          f"耗时 {clean.boot_time:.1f} 秒")

    # 设计的问题：全部命中现行规定，且主题跟旧规定高度重叠
    questions = [
        ("考试作弊会被怎么处理？", "现行规定应有答案"),
        ("课程考核不合格还能补考吗？", "★ 旧规定说'不能补考只能重修'——这是要被污染的问题"),
        ("请假三天要谁批准？", "★ 两份规定都有这条，看会不会引到旧的"),
    ]

    baseline = {}
    for q, note in questions:
        print(f"\n--- 【{q}】")
        print(f"    ({note})")
        try:
            hits = clean.search(q, k=3)
            for i, (chunk, vec) in enumerate(hits, 1):
                src = chunk.get("doc", "?")
                print(f"    {i}. 向量分={vec:.4f}  来源={src}")
                print(f"       {chunk['text'][:70].replace(chr(10), ' ')}...")
            baseline[q] = [c.get("doc", "?") for c, _ in hits]
        except Exception as e:
            print(f"    !! 检索失败：{type(e).__name__}: {e}")
            baseline[q] = []

    # ---------------------------------------------------------------
    # 第 2 步：混入已废止的旧规定，重建索引
    # ---------------------------------------------------------------
    hr("第 2 步：混入 2 份【已废止】的旧规定，重建索引（其余完全不变）")
    kb = DOCS / "kb"
    added = []
    for f in STALE_DIR.glob("*.md"):
        dst = kb / f.name
        if dst.exists():
            print(f"  ★ {dst.name} 已存在，跳过（先删掉 kb 里的同名文件）")
            continue
        shutil.copy2(f, dst)
        added.append(dst)
        print(f"  已注入：{dst.name}")

    if not added:
        print("\n没有注入任何文件 —— 实验无效，退出。")
        return

    dirty = RAG()
    dirty.startup()
    print(f"\n入库 {len(dirty.chunks)} 块（基线是 {len(clean.chunks)}），"
          f"拦下 {len(dirty.rejected)} 块，耗时 {dirty.boot_time:.1f} 秒")

    # ★★ 第一版实验在这里踩过坑，务必保留这个检查：
    #   样本文件必须带 --- 包裹的 doc/version/source 元信息，否则 loader 直接判不合格
    #   （kb/loader.py 的 REQUIRED_META），文件在 kb/ 里但根本不进索引。
    #   症状是"块数没变 + 废止文档 0/0"，看起来像"系统成功拦住了脏数据"，
    #   实际是【样本压根没入库】—— 这种假阴性比实验失败更危险。
    if len(dirty.chunks) == len(clean.chunks):
        print("\n★★ 实验无效：混入后块数没变，说明样本没进库。")
        print("   查 kb/loader.py 的 REQUIRED_META = ('doc', 'version', 'source')，")
        print("   样本文件开头必须有 --- 包裹的这三项元信息。")
        for f in added:
            if f.exists():
                f.unlink()
                print(f"   已清理 {f.name}")
        return
    print(f"\n样本确实入库了（块数 {len(clean.chunks)} → {len(dirty.chunks)}），实验有效。")

    # ---------------------------------------------------------------
    # 第 3 步：同样的问题再问一遍，对比来源
    # ---------------------------------------------------------------
    hr("第 3 步：对比 —— 旧规定有没有被检索出来")
    polluted = {}
    for q, _ in questions:
        print(f"\n--- 【{q}】")
        try:
            hits = dirty.search(q, k=3)
            for i, (chunk, vec) in enumerate(hits, 1):
                src = chunk.get("doc", "?")
                flag = " ★★废止文档" if "已废止" in src else ""
                print(f"    {i}. 向量分={vec:.4f}  来源={src}{flag}")
                print(f"       {chunk['text'][:70].replace(chr(10), ' ')}...")
            polluted[q] = [c.get("doc", "?") for c, _ in hits]
        except Exception as e:
            print(f"    !! 检索失败：{type(e).__name__}: {e}")
            polluted[q] = []

    # ---------------------------------------------------------------
    # 第 4 步：精排能不能把废条款压下去
    # ---------------------------------------------------------------
    hr("第 4 步：精排（rerank）能不能把废条款压下去")
    print("精排是让大模型给候选打分，理论上它可能'读出'废止信息。")
    print("这一步就是在测：它到底读不读得出来。\n")
    for q, _ in questions:
        try:
            cands = dirty.search(q, k=3)
            if not cands:
                continue
            ranked = dirty.rerank(q, cands)
            print(f"--- 【{q}】")
            for i, (chunk, vec, score, reason) in enumerate(ranked, 1):
                src = chunk.get("doc", "?")
                flag = " ★★废止文档" if "已废止" in src else ""
                print(f"    {i}. 精排分={score:.2f} 向量分={vec:.4f} 来源={src}{flag}")
                print(f"       理由：{str(reason)[:90]}")
                print(f"       原文：{chunk['text'][:70].replace(chr(10), ' ')}...")
            print()
        except Exception as e:
            print(f"--- 【{q}】!! 精排失败：{type(e).__name__}: {e}\n")

    # ---------------------------------------------------------------
    # 第 5 步：结论
    # ---------------------------------------------------------------
    hr("第 5 步：结论（自己读数据下判断，别被结论牵着走）")

    # ★ 匹配方式改过一次：原本拿 doc 值去和文件名比对，但样本的 doc 元信息里
    #   用了书名号（学工〔2018〕12号）而文件名是学工2018_12号，
    #   两者永远不相等 → 统计恒为 0，又是一次"看起来像成功"的假阴性。
    #   现在直接按【文档名里带"已废止"】判断，它才是这个实验真正关心的信号。
    is_stale = lambda s: "已废止" in str(s)
    stale_names = {f.name for f in added}
    got_stale = 0
    total = 0
    for q, _note in questions:
        for src in polluted.get(q, []):
            total += 1
            if is_stale(src):
                got_stale += 1

    print(f"\n检索 top-3 里出现废止文档的次数：{got_stale} / {total}")
    print(f"基线块数 {len(clean.chunks)} → 混入后 {len(dirty.chunks)}")
    top1 = sum(1 for q, _ in questions
               if polluted.get(q) and is_stale(polluted[q][0]))
    print(f"检索 top-1 就是废止文档的题数：{top1} / {len(questions)}")

    print("\n怎么读这个数：")
    print("  · 出现次数 > 0 → 检索层【拦不住】废条款。这是预期内的：")
    print("    废止条款和现行条款在embedding 眼里没有区别，都是格式完美的规章。")
    print("    ★ 所以本项目真正的防线不是'能识别废止'，而是'少而精 + 宁可拒答'。")
    print("\n★ 实测结论（2026-10-10 跑出来的，不要凭预期改这个）：")
    print("  检索层：废止文档【会被捞出来】，而且分数比现行规定更高——")
    print("          因为它与问题的字面/语义重合度更高（'课程考核不合格不能补考只能重修'")
    print("          这句几乎逐字命中问题）。")
    print("  精排层：★ 读不出来。精排理由写的是'直接明确回答……完全切题'，")
    print("          它在评价【切题程度】，不是在判断【这条是不是废的】。")
    print("  ⇒ 结论：当前设计下，混入废止条款会被当成权威依据引用。")
    print("    这不是实现 bug，是任务本身不可解—— 语料里没有任何字段区分'现行/已废止'，")
    print("    除非把 status 做成元信息字段并让精排显式检查它。")
    print("    ⇒ 所以正确的做法是【入库前把关】（docs/kb 只放已审核的现行条款），")
    print("       而不是指望检索层兜住。")

    # ---------------------------------------------------------------
    # 清理：把注入的文件删掉，别污染仓库
    # ---------------------------------------------------------------
    hr("清理")
    for f in added:
        f.unlink()
        print(f"  已删除 {f.name}")
    print("\n★ 建议：把 stale_docs/ 保留在仓库里（它是实验素材，不是语料）。")
    print("  它的作用是'可复现这个实验'—— 删了就没人能重跑一遍。")


if __name__ == "__main__":
    main()