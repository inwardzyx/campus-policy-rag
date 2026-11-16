# -*- coding: utf-8 -*-
"""agent 的「处置」（disposition）：这次请求**到底发生了什么**，用一张表说清。

## 为什么需要它（学自 super-agent 的 `disposition` 表）

现在 `/agent` 只返回 `steps` + `stopped_reason`。看起来够用，但有一类问题答不上来：

> 这条请求**为什么没有产出工作**？是没收到？收到了被 gate 拒？工具坏了？改写超限？

`stopped_reason` 答的是"循环怎么结束的"，不是"这次该不该被当成一次有效工作"。
super-agent 把每个事件都落一条 `disposition`（fanout / filtered / stored），
所以"没工单"能分成三种可查的情况 —— 我们照这个做一版。

## 分类规则（纯函数，可单测）

| kind | 条件 | 含义 |
|---|---|---|
| `answered` | `done` + 有来源 + 没拒答 | 正常答出来了 |
| `refused` | `gate_refused` 或 `refused` | 原问题过不了闸（系统选择不答） |
| `degraded` | `tool_error` / `rewrite_limit` / `budget` / `max_rounds` | 跑过但没跑完整 / 工具坏了 |
| `error` | 其它（含 `no_tool` 之外未知值） | 合同被破坏 |
"""
from __future__ import annotations

_DEGRADED = {"tool_error", "rewrite_limit", "budget", "max_rounds"}


def classify(rec: dict) -> dict:
    """把一次 `run_loop` 的结果压成一条可落库的处置记录。

    ★ 不猜：`stopped_reason` 是未知值时归 `error`，而不是归 `answered`
      （未知状态当成功，是这个项目反复踩过的坑）。
    """
    stopped = str(rec.get("stopped_reason") or "")
    refused = bool(rec.get("refused"))
    n_src = len(rec.get("sources") or [])

    if stopped == "gate_refused" or refused:
        return {"kind": "refused", "code": stopped or "refused",
                "detail": "原问题过不了 /chat 那道闸；循环里查到什么都不能推翻它"}
    if stopped in _DEGRADED:
        return {"kind": "degraded", "code": stopped, "detail": _detail_of(stopped)}
    if stopped == "done" and n_src > 0:
        return {"kind": "answered", "code": "done", "detail": f"{n_src} 条来源"}
    if stopped == "done":
        # 跑完但零来源：模型没调工具（no_tool 应在 loop 里先拦掉），或工具都没命中
        return {"kind": "degraded", "code": "done_no_source",
                "detail": "跑完了但没有任何来源 —— 答案没有证据"}
    return {"kind": "error", "code": stopped or "unknown",
            "detail": "未知的收尾状态：不许当成功"}


def _detail_of(code: str) -> str:
    return {
        "tool_error": "所有工具调用都失败（这段回答没有证据）",
        "rewrite_limit": "改写问法超过上限（换说法直到命中的后门）",
        "budget": "token 预算截断",
        "max_rounds": "轮数上限截断",
    }.get(code, code)


def to_line(d: dict) -> str:
    """给人看的一行：`refused(gate_refused): 原问题…`"""
    return f"{d['kind']}({d['code']}): {d['detail']}"
