#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""mutate_evalkit.py —— 证明评测内核的两条判据在坏情况下会响。

变异体：
  A. `_mean` 在空集时返回 0（而不是 None）⇒ "缺失"被冒名成"真的是 0"；
  B. 删掉重复 id 检查 ⇒ 两份同 id 的题会互相覆盖，报告看不出来。

★ 这两种坏法都不会报错：A 让报告更好看，B 让题集"能跑"。所以必须有用例会红。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
METRICS = ROOT / "kb" / "evalkit" / "metrics.py"
CASES = ROOT / "kb" / "evalkit" / "cases.py"
TESTS = "tests/test_eval_kit.py"

MUTATIONS = [
    (
        "A · 空集时 _mean 返回 0（缺失被冒名成 0）",
        METRICS,
        '    return round(sum(vals) / len(vals), 4) if vals else None\n',
        '    return round(sum(vals) / len(vals), 4) if vals else 0.0\n',
        "missing_trace",
        ["test_missing_trace_and_citation_are_none_not_zero"],
    ),
    (
        "B · 不查重复 id",
        CASES,
        '        elif cid in seen:\n'
        '            issues.append(f"{where}: id 重复（{cid}）—— 摘要会互相覆盖")\n',
        "",
        "broken_suites",
        ["test_validation_catches_broken_suites"],
    ),
]


def run_tests(kw: str) -> tuple[int, list[str], str]:
    r = subprocess.run(
        [sys.executable, "-m", "pytest", TESTS, "-q", "--no-header",
         "-p", "no:cacheprovider", "-k", kw],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    out = (r.stdout or "") + (r.stderr or "")
    failed = [ln.strip() for ln in out.splitlines() if ln.strip().startswith("FAILED")]
    summary = [ln.strip() for ln in out.splitlines()
               if (" passed" in ln or " failed" in ln) and "=" in ln]
    return r.returncode, failed, (summary[-1] if summary else "")


def main() -> int:
    originals = {p: p.read_bytes() for p in (METRICS, CASES)}
    before = {p: hashlib.sha256(b).hexdigest() for p, b in originals.items()}
    texts = {p: b.decode("utf-8") for p, b in originals.items()}
    ok = 0

    for label, target, old, new, kw, expect_ids in MUTATIONS:
        text = texts[target]
        n = text.count(old)
        if n != 1:
            print(f"✗ [{label}] 变异锚点命中 {n} 处（应为 1）")
            continue
        target.write_bytes(text.replace(old, new, 1).encode("utf-8"))
        try:
            code, failed, summary = run_tests(kw)
        finally:
            target.write_bytes(originals[target])

        hit = [f for f in failed if any(pid in f for pid in expect_ids)]
        if code == 0:
            print(f"✗ {label}\n   变异后仍然全绿 ⇒ 假绿")
        elif not hit:
            print(f"✗ {label}\n   非 0 退出但不是预期用例失败（可能是假红）：{failed}")
        else:
            ok += 1
            print(f"✓ {label}")
            for f in hit:
                print(f"   {f}")
            if summary:
                print(f"   {summary}")

    after = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in originals}
    same = before == after
    print(f"\n还原核对：{'一致' if same else '不一致！'}（metrics.py + cases.py）")
    print(f"判据被证明会响：{ok}/{len(MUTATIONS)}")
    return 0 if (ok == len(MUTATIONS) and same) else 1


if __name__ == "__main__":
    raise SystemExit(main())
