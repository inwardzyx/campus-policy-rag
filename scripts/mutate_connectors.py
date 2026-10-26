#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""mutate_connectors.py —— 证明 S1 的两条判据在坏情况下会响。

变异体：
  A. 同步引擎忽略 ACL 变化（只比内容哈希）⇒ 权限改了却永远"unchanged"，
     这是最危险的一种坏法：看起来同步一切正常，实际权限没跟上；
  B. 报错清单里删掉"发版"这一步 ⇒ 用户照做仍然 99991672，白折腾一轮。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
CORE = ROOT / "kb" / "connectors" / "core.py"
FEISHU = ROOT / "kb" / "feishu.py"
TESTS = "tests/test_connectors.py"

MUTATIONS = [
    (
        "A · 同步忽略 ACL 变化（权限改了却算 unchanged）",
        CORE,
        '            if _acl_tuple(old.get("acl")) != _acl_tuple(doc.acl):\n'
        '                reasons.append("acl")\n',
        "",
        "acl_change",
        ["test_acl_change_takes_effect_without_reindex"],
    ),
    (
        "B · 报错清单删掉「发版」这一步",
        FEISHU,
        '            "2) 版本管理与发布 → 创建版本 → 申请发布（**不发版，权限不生效**）；",\n',
        "",
        "permission_error",
        ["test_permission_error_explains_next_steps"],
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
    originals = {p: p.read_bytes() for p in (CORE, FEISHU)}
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
    print(f"\n还原核对：{'一致' if same else '不一致！'}（core.py + feishu.py）")
    print(f"判据被证明会响：{ok}/{len(MUTATIONS)}")
    return 0 if (ok == len(MUTATIONS) and same) else 1


if __name__ == "__main__":
    raise SystemExit(main())
