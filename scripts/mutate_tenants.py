#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""mutate_tenants.py —— 证明多租户的两条判据在坏情况下会响。

变异体：
  A. `Tenant.allows()` 恒 True（隔离形同虚设）⇒ 403 用例必须变红；
  B. `Registry.resolve_key()` 不比对哈希、直接放行第一个租户 ⇒ 401 用例必须变红。
★ 两种坏法都很"安静"：A 让越权访问看起来正常，B 让任何 key（甚至错的）都能进。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
TENANTS = ROOT / "tenants.py"
TESTS = "tests/test_tenants.py"

MUTATIONS = [
    (
        "A · allows() 恒 True（隔离失效）",
        '        ns = str(namespace or "")\n'
        '        return any(ns == n or ns.startswith(n.rstrip("/") + "/") for n in self.namespaces)\n',
        "        return True\n",
        "foreign_namespace",
        ["test_tenant_cannot_reach_foreign_namespace"],
    ),
    (
        "B · resolve_key 不比对哈希（任何 key 都能进）",
        "        target = hash_key(plain_key)\n",
        "        return next(iter(self._tenants.values()), None)\n",
        "requires_key",
        ["test_multi_tenant_requires_key"],
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
    original = TENANTS.read_bytes()
    before = hashlib.sha256(original).hexdigest()
    text = original.decode("utf-8")
    ok = 0

    for label, old, new, kw, expect in MUTATIONS:
        if text.count(old) != 1:
            print(f"✗ [{label}] 锚点命中 {text.count(old)} 处（应为 1）")
            continue
        TENANTS.write_bytes(text.replace(old, new, 1).encode("utf-8"))
        try:
            code, failed, summary = run_tests(kw)
        finally:
            TENANTS.write_bytes(original)

        hit = [f for f in failed if any(e in f for e in expect)]
        if code == 0:
            print(f"✗ {label}\n   仍然全绿 ⇒ 假绿")
        elif not hit:
            print(f"✗ {label}\n   非 0 退出但不是预期用例：{failed}")
        else:
            ok += 1
            print(f"✓ {label}")
            for f in hit:
                print(f"   {f}")

    same = hashlib.sha256(TENANTS.read_bytes()).hexdigest() == before
    print(f"\n还原核对：{'一致' if same else '不一致！'}（tenants.py）")
    print(f"判据被证明会响：{ok}/{len(MUTATIONS)}")
    return 0 if (ok == len(MUTATIONS) and same) else 1


if __name__ == "__main__":
    raise SystemExit(main())
