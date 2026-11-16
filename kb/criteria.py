# -*- coding: utf-8 -*-
"""判据版本：把"**判分/拒答的规则**"也变成一个可比的版本号。

## 为什么要它（2026-11-16 从 super-agent 的编排层学来的）

它的每条决策都带 `policyVersion` —— 判断按哪一版规则做的。
我们这边原来只有"代码版本（git sha）+ 语料 + 环境"三个维度，缺了第四个：

> **改了判据本身**（比如 agent 的 gate 语义、改写上限、k=5、gold 匹配方式），
> git sha 会变，但报告看不出"是代码变了"还是"**判分变严/变松了**" ——
> 后者更危险：分数变了，人会以为模型变好了。

## 怎么算

对**判据所在的那几个文件**取 sha256，再拼起来哈希前 8 位。
不做"语义提取"（那要人判断，会漂）；文件里连注释改了也会变 —— 这是**故意的保守**：
宁可多失效一次，也不要让"判据悄悄变了而报告还是绿的"。

⚠️ 代价（写在前面）：改这些文件里的**注释**也会让旧报告失效。
   为了降低噪音，`CRITERIA_FILES` 只收"真的决定分数/拒答"的文件，不收渲染器。
"""
from __future__ import annotations

import hashlib
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 决定"分怎么算、拒答怎么判"的文件。加/删这一行 = 判据变了，要一起改文档。
CRITERIA_FILES = (
    "agent/loop.py",            # agent 的 gate / 改写上限 / stopped_reason
    "agent/tools_local.py",     # 工具怎么拿证据（同一道闸）
    "eval/case_server.py",      # 离线/在线两层的判分入口
    "evalset/run_eval.py",      # recall_gold / knowledge_hit 的真身
    "eval/summarize.py",        # 指标定义与分母
)

#: 人读的一句话：这次判据的语义要点（改判据时**顺手改这里**，否则读者不知道变了什么）
LABEL = "agent gate 以原问题为准 + 改写去重上限 2；评分 k=5；指标分母各自算"


def version(root: str | None = None) -> str:
    """判据版本：`CRITERIA_FILES` 内容哈希的前 8 位。

    ★ 文件缺失不报错、而是把它名子写进哈希（`missing:`）—— 缺失 ≠ 空：
      两种情况的版本必须不同，否则"文件被删了"会看起来像"判据没变"。
    """
    base = root or _ROOT
    h = hashlib.sha256()
    for rel in CRITERIA_FILES:
        p = os.path.join(base, rel)
        h.update(rel.encode("utf-8"))
        if not os.path.exists(p):
            h.update(b"\0missing")
            continue
        with open(p, "rb") as f:
            h.update(b"\0" + hashlib.sha256(f.read()).digest())
    return h.hexdigest()[:8]


if __name__ == "__main__":
    print(version())
