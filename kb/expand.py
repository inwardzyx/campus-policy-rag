# -*- coding: utf-8 -*-
"""查询扩展词表（评测闭环的"候选"落点）。

## 为什么是这个词表
评测失败的绝大多数是"**检索没抓到**"：用户说的词和条款里的词对不上
（"二专业" vs "第二学士学位"、"处分" vs "纪律处分种类"）。
换 embedding 模型成本高、还动不了 BM25；加几条**受控的**同义扩展，
改动小、可解释、能一键回滚——正好适合做"闭环 v0"的候选载体。

## 纪律
- 只做**扩展**（往查询里加词），不改原词、不改生成提示；
- 词表是数据文件，不是代码：回滚 = 换回旧文件（见 `evalset/loop/registry.json`）；
- `apply()` 必须幂等：同一查询调两次不会把词加两遍。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_PUNCT = re.compile(r"[\s，。！？、；：,.!?;:\"'“”‘’（）()《》【】\[\]…—\-~]+")


def normalize(text: str) -> str:
    return _PUNCT.sub("", (text or "").lower())


class Lexicon:
    def __init__(self, entries: list[dict[str, Any]] | None = None) -> None:
        self.entries = entries or []

    @classmethod
    def load(cls, path: str | Path | None) -> "Lexicon | None":
        if not path:
            return None
        p = Path(path)
        if not p.exists():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        return cls(list(data.get("entries") or []))

    def apply(self, query: str) -> tuple[str, list[str]]:
        """返回 (扩展后的查询, 命中的条目 id 列表)。没有命中就原样返回。"""
        norm = normalize(query)
        if not norm:
            return query, []
        already = set(norm)
        added: list[str] = []
        matched: list[str] = []
        for entry in self.entries:
            terms = [normalize(t) for t in entry.get("match") or [] if t]
            if not terms or not all(t in norm for t in terms):
                continue
            matched.append(str(entry.get("id") or ""))
            for extra in entry.get("add") or []:
                n = normalize(extra)
                if not n or n in already or n in norm:
                    continue
                added.append(extra)
                already.add(n)
        if not added:
            return query, matched
        return f"{query} {' '.join(added)}", matched
