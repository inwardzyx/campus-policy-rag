#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""实验：语料变大之后，多查还有没有价值？

★ 为什么要做这个实验（这是上一轮实验最大的软肋）：
    上一轮得出"多查救不了q16/q20" —— 但那是在【263 块、10 份文件、全是校规】
    的库上跑的。那个库的特点是【块少、同质、每份文件都很大块】，
    第一轮粗捞几乎总能命中（实测 gold 排名中位数 = 1）。
    真实场景里语料会变成【上千块、几十份文件、粒度更细】，那时候
    "库里明明有答案但第一轮没捞到"会变得常见 —— 那正是多查的用武之地。

    所以这里不问"多查能救q16吗"，而问一个更根本的问题：
        【把库变大，第一轮召回会退化到什么程度？退化到多少之后，多查才划算？】

★ 怎么模拟"语料变大"（不能真的去网上抓语料，那是另一个工程）：
    用【相似但不同主题】的条款去填充 —— 直接把现有条款做了两类扰动：
      ① 改写：同义替换、同义改写（模拟"同一个意思但字面不同"的条款）
      ② 插入噪声：把不相关主题的条款混进来（模拟"库里领域变杂"）
    这两类都是真实语料变大时【必然发生】的：
      条款会改版、同一条规定会在多个文件里重述。
    ⇒ 规模从263 → 526 → 1052，逼近真实"上千块"的量级。

★ 判读标准（实验前写死）：
    · 若gold 排名中位数明显下降 → 语料变大确实让第一轮变难
    · 若"第一轮没进top5 但进了 top20"的题里，多查能救回一部分
      ⇒ 多查在真实语料上有价值，且现在就能测
    · 若排名几乎不动 → 263 块的瓶颈不在库大小，多查也没用，
      该去补语料多样性而不是加循环

用法：
    /d/Python-project/.venv/Scripts/python.exe experiments/scale_probe.py
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
os.environ.setdefault("LANGSMITH_TRACING", "false")

import numpy as np  # noqa: E402
import jieba  # noqa: E402
from rank_bm25 import BM25Okapi  # noqa: E402
import service as svc  # noqa: E402
# ★ print 里的 ⇒/✓/⚠ 在 cp936 下编不出来（stdout 是管道时）⇒ 崩在打印上。本项目已犯 7 次。
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")


# ------------------------------------------------------------
# 扰动器：造出"规模更大但同源"的库
# ------------------------------------------------------------
# 同义替换表：只用校园/公文里真实常见的替换，不是随机造词
SYN = {
    "学生": "在校生", "学校": "我校", "学院": "我校", "规定": "办法",
    "应当": "应", "不得": "不准", "可以": "有权", "申请": "申报",
    "处分": "处理", "情节": "情形", "严重": "较重", "审核": "审查",
    "公布": "公示", "施行": "实施", "撤销": "取消", "撤销": "取消",
}


def paraphrase(text: str, rng: random.Random) -> str:
    """同义改写：模拟"同一规定在另一个文件里换了说法"。"""
    out = text
    for a, b in SYN.items():
        if a in out:
            out = out.replace(a, b)
    # 掐掉一部分末尾内容，让它不100% 等于原块（否则会被查重关拦掉，
    # 也测不出"同义但不同字面"这个真实难点）
    if len(out) > 60:
        cut = rng.randint(10, min(60, len(out) // 4))
        out = out[:-cut]
    return out


def make_scaled_corpus(base: list[dict], factor: int, seed: int = 7):
    """把语料放大 factor 倍。**factor=1 必须原样返回 base**（不扰动）。

    ★ 为什么 factor=1 必须原样返回：
        之前写的是"循环 factor 次"，factor=1 时也会生成 263 条改写副本，
        于是"1×"其实已经变成 526 块 —— 标签和数据对不上，
        拿它当基准态会把"扰动的影响"和"规模的影响"混在一起。
        已修正：1× 直接返回 base。
        （实测修正前后 q12 的排名从18 变成 10，18 那个是被污染的基准。）
    """
    if factor <= 1:
        return list(base)

    rng = random.Random(seed)
    chunks = list(base)
    seen = {(c["doc"], c["clause"]) for c in base}

    # ① 改写副本：模拟"同规定多版本/多处重述" —— 语料变多时最常见的形态
    for i in range(factor):
        for c in base:
            nid = f"{c['doc']}·改写{i+1}"
            key = (nid, c["clause"])
            if key in seen:
                continue
            seen.add(key)
            chunks.append({**c, "doc": nid, "text": paraphrase(c["text"], rng)})

    # ② 领域噪声：把块按主题重新命名后混入 —— 模拟"库里领域变杂"
    #    做法是随机贴一个不相关主题的标签但保留原文向量，
    #    这样它们会参与相似度竞争，模拟"干扰项变多"
    topics = ["财务处", "后勤集团", "网络中心", "图书馆", "一卡通",
              "校医院", "保卫处", "就业指导", "国际交流", "继续教育"]
    for i in range(max(0, factor - 1)):
        for c in base:
            nid = f"{c['doc']}·相关{i+1}"
            key = (nid, c["clause"])
            if key in seen:
                continue
            seen.add(key)
            chunks.append({**c, "doc": f"{rng.choice(topics)}·{nid}"})

    return chunks


def build_index(chunks: list[dict]):
    """按 service.py::startup 里的方式重建一个可search 的索引（只读，不动主流程）。

    ★ BM25 是直接 new 出来的，不是从 rag 上借用 ——
      因为规模实验的 chunks 和主流程的 chunks 不是同一批，
      共用索引就会拿"基准库"的 BM25 去搜"放大库"，结果没有意义。
    """
    texts = [c["text"] for c in chunks]
    # ★ 刻意【不】做 L2 归一化 —— service.py::startup 里没有这一步，
    #   有了这一步排名会变（实测 q12 从第 10 名变18 名）。
    #   这个实验要和主流程同口径，否则"规模变了"和"口径变了"两件事混在一起，
    #   结论会站不住。已用基准态验证过：不归一化时 1× 的排名与主流程一致。
    embeds = np.array(list(svc.rag.model.passage_embed(texts)), dtype="float32")
    bm25 = BM25Okapi([list(jieba.cut_for_search(t)) for t in texts])
    return {"texts": texts, "embeds": embeds, "bm25": bm25, "chunks": chunks}


def search(index, query: str, k: int = 5):
    # 不归一化 query —— 与 service.py::search 同口径（它也没归一化）
    q = np.array(list(svc.rag.model.query_embed([query])), dtype="float32")[0]
    vs = index["embeds"] @ q
    order = np.argsort(-vs)
    # RRF 融合：和 service.py::search 同口径
    tokens = list(jieba.cut_for_search(query))
    bs = index["bm25"].get_scores(tokens)
    border = np.argsort(-bs)
    rrf = {}
    for rank, idx in enumerate(order):
        rrf[idx] = rrf.get(idx, 0.0) + 1.0 / (60 + rank)
    for rank, idx in enumerate(border):
        rrf[idx] = rrf.get(idx, 0.0) + 1.0 / (60 + rank)
    top = sorted(rrf.items(), key=lambda kv: kv[1], reverse=True)[:k]
    return [(index["chunks"][idx], float(vs[idx])) for idx, _ in top]


def main() -> None:
    svc.rag.startup()
    base = list(svc.rag.chunks)
    print(f"基准库 {len(base)} 块 / {len({c['doc'] for c in base})} 份文件")

    qs = json.loads((REPO_ROOT / "evalset" / "questions.json").read_text(encoding="utf-8"))
    ans = [q for q in qs if q["type"] == "answer"]

    for factor in (1, 2, 4):
        chunks = make_scaled_corpus(base, factor)
        n = len(chunks)
        label = f"{n:>5} 块（{factor}×）"
        print("\n" + "=" * 64)
        print(f"规模 {label}")
        print("=" * 64)
        index = build_index(chunks)

        ranks = []
        lost_top5 = []          # ★ 关键指标：gold 掉出 top5 但还在 top20
        for q in ans:
            hits = search(index, q["question"], k=20)
            gd, gc = q["gold"][0], q["gold"][1]
            rank = None
            for i, (c, _v) in enumerate(hits, 1):
                # 改写副本不算命中 gold（同一条款的副本不代表答得对）
                if c["doc"] == gd and c["clause"] == gc:
                    rank = i
                    break
            ranks.append(rank)
            if rank is not None and rank > 5:
                lost_top5.append((q["id"], q["question"], rank))

        ok = [r for r in ranks if r]
        print(f"gold 进 top20：{len(ok)}/{len(ans)}"
              f"中位排名 {int(np.median(ok)) if ok else '-'}"
              f"　掉出 top5 但在 top20：{len(lost_top5)} 道")
        for qid, qt, r in lost_top5:
            print(f"    {qid} {qt[:26]} 第 {r} 名")

        #★★ 基准态自检：1× 必须和主流程给出同一个排名。
        #   这个自检是必要的 —— 之前 factor=1 时也做了扰动（标签写 1×、
        #   实际 526 块），基准就被污染了，而当时看不出来。
        #   凡是自己搭的索引与主流程有差异，一律先怀疑口径，不许先下结论。
        if factor == 1:
            probe_q = "我要请一个礼拜的假，需要哪一级批准？"
            main_rank = next(
                (i for i, (c, _v) in enumerate(svc.rag.search(probe_q, k=20), 1)
                 if c["doc"] == "学生请销假制度.md"
                 and c["clause"] == "总则-第三条"), None)
            my_rank = lost_top5[0][2] if any(t[0] == "q12" for t in lost_top5) else None
            if my_rank is None:
                # q12 不在 lost_top5 里，说明它进了 top5，取它的真实排名
                my_rank = next(
                    (i for i, (c, _v) in enumerate(search(index, probe_q, k=20), 1)
                     if c["doc"] == "学生请销假制度.md"
                     and c["clause"] == "总则-第三条"), None)
            if my_rank != main_rank:
                print(f"  ⚠⚠ 基准态自检失败：本脚本 q12 排第 {my_rank}，"
                      f"主流程排第 {main_rank} ⇒ 索引口径不一致，下面所有结论都不可信")
                return
            print(f"  基准态自检通过（本脚本与主流程 q12 排名一致：第 {my_rank} 名）")

        if factor > 1 and lost_top5:
            print(f"\n  ★ 这 {len(lost_top5)} 道就是多查该发挥作用的场景")
            print("    （库里确实有答案，只是第一轮排序没把它送进 top5）")


if __name__ == "__main__":
    main()
