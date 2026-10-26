# -*- coding: utf-8 -*-
"""版本裁决（as-of）：按"这件事发生在哪一天"挑出当时有效的条款。

## 为什么企业问答必须有这一层
制度会改版。同一个问题在 2024 年和 2026 年问，答案可能相反（报销比例、请假审批层级）。
只按"现行有效"检索，会让"去年发生的事"被按今年的条款回答 —— 这比拒答更危险，
因为它**看起来完全正常**。设计稿 §3.2 就是这么要求的：`event_time` + 版本裁决 + 答案标版本。

## v1 的规则（刻意很小）
输入是检索候选（按相关度排好），每条块可以带：
- `effective_date`：`YYYY-MM-DD`，空字符串 = **日期未知**（不要当成 1970）；
- `status`：`active` / `deprecated`。

1. **有 `event_time`**：只留 `effective_date <= event_time` 的块；
   `effective_date` 为空的块**保留但计入 `unknown_date`**（不假装能裁决）；
   晚于 event_time 的块丢，reason=`not_yet_effective`。
2. **没带 `event_time`**（默认现行）：丢 `status=deprecated` 的块，reason=`stale`。
3. **同一 (doc, clause) 多版本**：在留下来的里面取 `effective_date` 最大的那条，
   其余 reason=`superseded`。

★ 丢弃的块**不删**，而是复制一份打 `_drop_reason`：选块那一步会把它记进 trace.excluded
  （带 chars 与原因），这样"为什么没用这条"在响应里就能看见，而不是只在日志里。

## 为什么不做的事
- 不做"过渡期条款同时引用"（设计稿 §3.2 允许，但需要人工标注 `transition`，当前语料没有）；
- 不改答案措辞让它写"按 X 年 X 月版本"——那是模板的事（见 HANDOFF §5），本模块只管选块。
"""
from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

STATUS_ACTIVE = "active"
STATUS_DEPRECATED = "deprecated"
VALID_STATUS = (STATUS_ACTIVE, STATUS_DEPRECATED)

REASON_NOT_YET = "not_yet_effective"
REASON_STALE = "stale"
REASON_SUPERSEDED = "superseded"


def _chunk_of(item: Any) -> Mapping[str, Any]:
    """候选可能是块本身，也可能是 `(块, 分数)`（粗捞）或四元组（精排）。"""
    if isinstance(item, (tuple, list)):
        return item[0]
    return item


def _date(chunk: Mapping[str, Any]) -> str:
    return str(chunk.get("effective_date") or "")


def _status(chunk: Mapping[str, Any]) -> str:
    s = str(chunk.get("status") or STATUS_ACTIVE).lower()
    return s if s in VALID_STATUS else STATUS_ACTIVE


def filter_versions(
    items: Sequence[Any],
    *,
    event_time: str | None = None,
    key: Callable[[Mapping[str, Any]], tuple[str, str]] | None = None,
) -> tuple[list[Any], list[dict], dict[str, Any]]:
    """返回 `(留下的候选, 被丢弃的块副本, 报告)`。

    `key(chunk)` 默认用 `(doc, clause)` 分组；同组视为"同一条款的不同版本"。
    """
    key = key or (lambda c: (str(c.get("doc") or ""), str(c.get("clause") or "")))
    kept: list[Any] = []
    dropped: list[dict] = []
    groups: dict[tuple[str, str], list[Any]] = {}

    for item in items:
        groups.setdefault(key(_chunk_of(item)), []).append(item)

    unknown_date = 0
    as_of = event_time or ""
    for _, group in groups.items():
        # ① 先按时间/状态筛
        alive: list[Any] = []
        for item in group:
            chunk = _chunk_of(item)
            eff = _date(chunk)
            if event_time:
                if eff and eff > event_time:
                    bad = dict(chunk, _drop_reason=REASON_NOT_YET)
                    dropped.append(bad)
                    continue
                if not eff:
                    unknown_date += 1
            elif _status(chunk) == STATUS_DEPRECATED:
                bad = dict(chunk, _drop_reason=REASON_STALE)
                dropped.append(bad)
                continue
            alive.append(item)

        # ② 同一条款多版本：只留"当时最新的那一版"
        alive.sort(key=lambda it: _date(_chunk_of(it)), reverse=True)
        kept.extend(alive[:1])
        for item in alive[1:]:
            dropped.append(dict(_chunk_of(item), _drop_reason=REASON_SUPERSEDED))

    report = {
        "event_time": as_of or None,
        "groups": len(groups),
        "kept": len(kept),
        "dropped": len(dropped),
        "dropped_by_reason": _count(dropped),
        "unknown_date_blocks": unknown_date,
    }
    return kept, dropped, report


def _count(dropped: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for d in dropped:
        r = str(d.get("_drop_reason") or "")
        out[r] = out.get(r, 0) + 1
    return out
