# -*- coding: utf-8 -*-
"""题型分类：给"分组指标"用。

## 为什么要分类
20 道题里如果 15 道是数值题、2 道多跳，混在一起报一个 14/15 会掩盖"多跳全错"。
分组之后，小样本的 Wilson 区间会如实变宽——**能看出哪一类没被覆盖**。

## 分类规则（可覆盖）
1. 题面显式 `tags` 优先（如 boundary 题写 `tags: ["boundary"]`）；
2. 拒答/越权题按类型归 `out_of_scope` / `acl_deny`；
3. 问题里出现单位词（学时/天/元/比例…）归 `numeric`；
4. 其余 `other`。
"""
from __future__ import annotations

from typing import Any, Mapping

from kb.values import _UNIT_ALIAS


def asked_units(text: str) -> set[str]:
    q = text or ""
    return {unit for raw, unit in _UNIT_ALIAS.items() if raw in q}


def category_of(case: Mapping[str, Any]) -> str:
    tags = case.get("tags") or []
    if tags:
        return str(tags[0])
    typ = str(case.get("type") or "answer")
    if typ == "refuse":
        return "out_of_scope"
    if typ == "deny":
        return "acl_deny"
    if asked_units(str(case.get("question") or "")):
        return "numeric"
    return "other"
