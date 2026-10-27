# -*- coding: utf-8 -*-
"""评测闭环的门禁：只凭**配对结果**决定"候选"能不能上线。

## 为什么要有门禁（而不是"看着变好了就上"）
评测集只有 20 题，一次运行的波动就能造出"+1"。所以规矩定死：
- **有净收益**（题从不对变对，≥ min_gain）；
- **没有回退**（原本对的不能变错）；
- **拒答题不能变错**（该拒的还得拒，否则就是在教模型编）；
- **不能出现越权泄漏**（deny 套件里出现 acl_leak 直接拒绝）。

四条全过才 accept；任何一条不过就 reject（候选留在 registry 里，状态 rejected）。
"""
from __future__ import annotations

from typing import Any, Iterable


def _index(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(r.get("id")): r for r in rows if r.get("id") is not None}


def _ok(row: dict[str, Any] | None) -> bool:
    """这一题算不算"做对了"。

    ★ 用报告里的 bucket 判定，而不是自己再算一遍指标 ——
      否则门禁和报告会各有一套口径（本项目踩过的"两处各写一份判据"）。
    """
    if not row:
        return False
    bucket = str(row.get("bucket") or "")
    typ = str(row.get("type") or "")
    # ★ 拒答/越权题里，"答了"不算对：必须刚好是 refuse_ok
    if typ in ("refuse", "deny"):
        return bucket == "refuse_ok"
    return bucket == "ok"


def decide(before_rows: Iterable[dict[str, Any]], after_rows: Iterable[dict[str, Any]],
           *, min_gain: int = 1) -> dict[str, Any]:
    before = _index(before_rows)
    after = _index(after_rows)
    common = sorted(set(before) & set(after))

    gains: list[str] = []
    losses: list[str] = []
    refuse_losses: list[str] = []
    leaks: list[str] = []

    for cid in common:
        b, a = before[cid], after[cid]
        b_ok, a_ok = _ok(b), _ok(a)
        if not b_ok and a_ok:
            gains.append(cid)
        if b_ok and not a_ok:
            losses.append(cid)
            if str(b.get("type")) == "refuse":
                refuse_losses.append(cid)
        if str(a.get("bucket") or "") == "acl_leak":
            leaks.append(cid)

    verdict = "accept"
    reasons: list[str] = []
    if len(gains) < min_gain:
        verdict = "reject"
        reasons.append(f"净收益不足：+{len(gains)} < {min_gain}")
    if losses:
        verdict = "reject"
        reasons.append(f"有回退：{losses}")
    if refuse_losses:
        verdict = "reject"
        reasons.append(f"该拒答的变作答：{refuse_losses}")
    if leaks:
        verdict = "reject"
        reasons.append(f"越权泄漏：{leaks}")

    return {
        "verdict": verdict,
        "reasons": reasons,
        "n_common": len(common),
        "gains": gains,
        "losses": losses,
        "refuse_losses": refuse_losses,
        "leaks": leaks,
        "min_gain": min_gain,
    }
