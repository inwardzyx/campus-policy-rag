#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""mutate_deploy_smoke.py —— 证明部署验收的两条判据会响。

变异体：
  A. `_pair` 不拆 `doc｜clause` ⇒ /search 形状判不中（假阴性）；
  B. 报告里的通过数改成"总数"⇒ 有失败也显示全过（假绿，最危险的一种）。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "scripts" / "deploy_smoke.py"
TESTS = "tests/test_deploy_smoke.py"

MUTATIONS = [
    ("A · 不拆 doc｜clause", TARGET,
     '    if clause is None and "｜" in doc:\n        doc, _, clause = doc.partition("｜")\n',
     "", "both_source_shapes", ["test_pair_accepts_both_source_shapes"]),
    ("B · 报告通过数写成总数", TARGET,
     '    ok = sum(1 for c in checks if c["ok"])\n    lines = [f"# 部署验收',
     '    ok = len(checks)\n    lines = [f"# 部署验收', "counts_only_passed",
     ["test_md_report_counts_only_passed_checks"]),
]


def run_tests(kw: str) -> tuple[int, list[str]]:
    r = subprocess.run(
        [sys.executable, "-m", "pytest", TESTS, "-q", "--no-header",
         "-p", "no:cacheprovider", "-k", kw],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    out = (r.stdout or "") + (r.stderr or "")
    return r.returncode, [ln.strip() for ln in out.splitlines() if ln.strip().startswith("FAILED")]


def main() -> int:
    original = TARGET.read_bytes()
    before = hashlib.sha256(original).hexdigest()
    text = original.decode("utf-8")
    ok = 0

    for label, target, old, new, kw, expect in MUTATIONS:
        if text.count(old) != 1:
            print(f"✗ [{label}] 锚点命中 {text.count(old)} 处")
            continue
        target.write_bytes(text.replace(old, new, 1).encode("utf-8"))
        try:
            code, failed = run_tests(kw)
        finally:
            target.write_bytes(original)
        hit = [f for f in failed if any(e in f for e in expect)]
        if code == 5:
            print(f"✗ {label}\n   -k 未选中用例")
        elif code == 0:
            print(f"✗ {label}\n   仍然全绿 ⇒ 假绿")
        elif not hit:
            print(f"✗ {label}\n   非 0 但不是预期用例：{failed}")
        else:
            ok += 1
            print(f"✓ {label}")
            for f in hit:
                print(f"   {f}")

    same = hashlib.sha256(TARGET.read_bytes()).hexdigest() == before
    print(f"\n还原核对：{'一致' if same else '不一致！'}（deploy_smoke.py）")
    print(f"判据被证明会响：{ok}/{len(MUTATIONS)}")
    return 0 if (ok == len(MUTATIONS) and same) else 1


if __name__ == "__main__":
    raise SystemExit(main())
