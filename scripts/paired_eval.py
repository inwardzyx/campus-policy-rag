#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""paired_eval.py —— 按 `experiments/closed-loop-protocol.md` 判"到底有没有变好"。

## 它拒绝做的事
- 不比两个总数（会被"修好 3 题、弄坏 1 题"骗）；
- 不在指纹不一致时给结论（那是两个系统在比）；
- 证据不足时**不返回 0**（空转必须报红）。

## 用法
```powershell
# 每臂 K=3 份报告（同一题集）
python scripts/paired_eval.py `
  --a evalset/loop-company-52/baseline.json `
  --b evalset/loop-company-52/candidate.json `
  --md experiments/closed-loop-verdict.md
```
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kb.loopgate import _ok as _row_ok, mcnemar_exact   # noqa: E402  （同一口径，不另写一份）


def _load(paths: list[str]) -> list[dict]:
    return [json.loads(Path(p).read_text(encoding="utf-8")) for p in paths]


def _majority(rows_by_report: list[dict[str, dict]]) -> dict[str, bool]:
    """逐题多数（> K/2）。缺题按 False 处理（缺题不能算"对"）。"""
    ids = set()
    for rep in rows_by_report:
        ids |= set(rep)
    out: dict[str, bool] = {}
    for cid in ids:
        votes = [bool(rep.get(cid)) for rep in rows_by_report]
        out[cid] = sum(votes) * 2 > len(votes)
    return out


def _rows(report: dict) -> dict[str, dict]:
    return {str(r.get("id")): r for r in (report.get("rows") or [])}


def _fp(report: dict) -> str | None:
    meta = report.get("meta") or {}
    return meta.get("state_fingerprint")


def mcnemar_and_ci(fixed: list[str], broke: list[str], n: int,
                   *, iters: int = 5000, seed: int = 42) -> dict:
    """返回 p 值 + Δ 的 bootstrap 95% CI。"""
    p = mcnemar_exact(len(fixed), len(broke))
    deltas: list[float] = []
    rng = random.Random(seed)
    for _ in range(iters):
        f = sum(1 for _ in range(n) if rng.random() < len(fixed) / n) if n else 0
        b = sum(1 for _ in range(n) if rng.random() < len(broke) / n) if n else 0
        deltas.append((f - b) / n if n else 0.0)
    deltas.sort()
    lo = deltas[int(0.025 * (iters - 1))]
    hi = deltas[int(0.975 * (iters - 1))]
    return {"p": round(p, 4), "delta": round((len(fixed) - len(broke)) / n, 4) if n else None,
            "ci95": [round(lo, 4), round(hi, 4)]}


def guardrails(before: list[dict], after: list[dict], before_rows: dict, after_rows: dict) -> dict:
    """四道护栏：回归 / 拒答回退 / 越权泄漏 / p95 不超 1.5×。"""
    def refused_ok(rows: dict) -> tuple[int, int]:
        ref = [r for r in rows.values() if str(r.get("type")) == "refuse"]
        return sum(1 for r in ref if r.get("bucket") == "refuse_ok"), len(ref)

    def leaks(rows: dict) -> int:
        return sum(1 for r in rows.values() if r.get("bucket") == "acl_leak")

    b_ok, b_n = refused_ok(before_rows)
    a_ok, a_n = refused_ok(after_rows)
    p95b = [((r.get("aggregate") or {}).get("p95_ms") or 0) for r in before]
    p95a = [((r.get("aggregate") or {}).get("p95_ms") or 0) for r in after]
    base = sorted(x for x in p95b if x)
    new = sorted(x for x in p95a if x)
    ratio = (sum(new) / len(new)) / (sum(base) / len(base)) if base and new else None
    return {
        "refuse_before": [b_ok, b_n], "refuse_after": [a_ok, a_n],
        "refuse_dropped": (a_ok / a_n if a_n else 1.0) < (b_ok / b_n if b_n else 1.0),
        "leaks_after": leaks(after_rows),
        "p95_ratio": round(ratio, 2) if ratio else None,
        "p95_too_slow": bool(ratio and ratio > 1.5),
    }


def evaluate(a_paths: list[str], b_paths: list[str], alpha: float = 0.05) -> dict:
    before_reports, after_reports = _load(a_paths), _load(b_paths)
    fp_before = {_fp(r) for r in before_reports if _fp(r)}
    fp_after = {_fp(r) for r in after_reports if _fp(r)}
    comparable = (fp_before == fp_after) and len(fp_before) == 1
    if not comparable:
        return {"verdict": "不可比", "reason": f"指纹不一致：before={sorted(fp_before)} after={sorted(fp_after)}",
                "p": None, "ci95": None, "fixed": [], "broke": []}

    b_major = _majority([{k: _row_ok(v) for k, v in _rows(r).items()} for r in before_reports])
    a_major = _majority([{k: _row_ok(v) for k, v in _rows(r).items()} for r in after_reports])
    common = sorted(set(b_major) & set(a_major))
    fixed = [c for c in common if not b_major[c] and a_major[c]]
    broke = [c for c in common if b_major[c] and not a_major[c]]
    unchanged = len(common) - len(fixed) - len(broke)
    stats = mcnemar_and_ci(fixed, broke, len(common))
    g = guardrails(before_reports, after_reports,
                   {k: v for r in before_reports for k, v in _rows(r).items()},
                   {k: v for r in after_reports for k, v in _rows(r).items()})

    evidence = (
        stats["p"] < alpha
        and stats["ci95"][0] > 0
        and not g["refuse_dropped"]
        and g["leaks_after"] == 0
        and not g["p95_too_slow"]
    )
    reasons = []
    if stats["p"] >= alpha:
        reasons.append(f"McNemar p={stats['p']} >= {alpha}")
    if stats["ci95"][0] <= 0:
        reasons.append(f"Δ 的 95% CI 下界 {stats['ci95'][0]} <= 0")
    if g["refuse_dropped"]:
        reasons.append("拒答率回退")
    if g["leaks_after"]:
        reasons.append(f"越权泄漏 {g['leaks_after']} 条")
    if g["p95_too_slow"]:
        reasons.append(f"p95 变为 {g['p95_ratio']}×（>1.5×）")

    return {
        "verdict": "有证据" if evidence else "无证据",
        "reasons": reasons,
        "n": len(common), "fixed": fixed, "broke": broke, "unchanged": unchanged,
        **stats,
        "guardrails": g,
        "fingerprint": sorted(fp_before)[0],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="闭环配对验收")
    ap.add_argument("--a", action="append", required=True, help="before 报告（可重复，K=3）")
    ap.add_argument("--b", action="append", required=True, help="after 报告（可重复，K=3）")
    ap.add_argument("--alpha", type=float, default=0.05)
    ap.add_argument("--md", default="")
    args = ap.parse_args()

    result = evaluate(args.a, args.b, args.alpha)
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if args.md:
        lines = [f"# 闭环验收：{result['verdict']}", "",
                 f"- 指纹：`{result.get('fingerprint') or '?'}`",
                 f"- 题数：{result.get('n')}　fixed={len(result.get('fixed', []))} "
                 f"broke={len(result.get('broke', []))} unchanged={result.get('unchanged')}",
                 f"- Δ={result.get('delta')}　95% CI={result.get('ci95')}　McNemar p={result.get('p')}"]
        if result.get("reasons"):
            lines.append("- 未通过：" + "；".join(result["reasons"]))
        lines.append("")
        Path(args.md).parent.mkdir(parents=True, exist_ok=True)
        Path(args.md).write_text("\n".join(lines), encoding="utf-8", newline="\n")
        print(f"✓ {args.md}")

    return 0 if result["verdict"] == "有证据" else 2


if __name__ == "__main__":
    raise SystemExit(main())
