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

SERVICE = ROOT / "service.py"

MUTATIONS = [
    (
        "A · allows() 恒 True（隔离失效）",
        TENANTS,
        '        ns = str(namespace or "")\n'
        '        return any(ns == n or ns.startswith(n.rstrip("/") + "/") for n in self.namespaces)\n',
        "        return True\n",
        "foreign_namespace",
        ["test_tenant_cannot_reach_foreign_namespace"],
    ),
    (
        "B · resolve_key 不比对哈希（任何 key 都能进）",
        TENANTS,
        "        target = hash_key(plain_key)\n",
        "        return next(iter(self._tenants.values()), None)\n",
        "requires_key",
        ["test_multi_tenant_requires_key"],
    ),
    (
        "C · 写接口不校验租户（任何人都能写别人 namespace）",
        SERVICE,
        "    tenant = _tenant(request)\n"
        "    _assert_tenant_namespace(tenant, namespace)\n"
        "    return tenant\n",
        "    return TENANTS.default()\n",
        "write_only_own",
        ["test_tenant_can_write_only_own_namespace"],
    ),
    (
        "E · 隧道头不拒（公网可无 token 进管理接口）",
        SERVICE,
        "    if host not in (\"127.0.0.1\", \"::1\", \"testclient\") or proxied:\n",
        "    if host not in (\"127.0.0.1\", \"::1\", \"testclient\"):\n",
        "blocks_tunnel",
        ["test_admin_guard_blocks_tunnel_when_no_token"],
    ),
    (
        "D · 配额不分租户（A 打满会拖垮 B）",
        SERVICE,
        "    who = who_override or limits_mod.client_id(",
        "    who = limits_mod.client_id(",
        "quota_does_not_leak",
        ["test_per_tenant_quota_does_not_leak_across_tenants"],
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
    originals = {p: p.read_bytes() for p in (TENANTS, SERVICE)}
    before = {p: hashlib.sha256(b).hexdigest() for p, b in originals.items()}
    texts = {p: b.decode("utf-8") for p, b in originals.items()}
    ok = 0

    for label, target, old, new, kw, expect in MUTATIONS:
        text = texts[target]
        if text.count(old) != 1:
            print(f"✗ [{label}] 锚点命中 {text.count(old)} 处（应为 1）")
            continue
        target.write_bytes(text.replace(old, new, 1).encode("utf-8"))
        try:
            code, failed, summary = run_tests(kw)
        finally:
            target.write_bytes(originals[target])

        hit = [f for f in failed if any(e in f for e in expect)]
        if code == 5:
            print(f"✗ {label}\n   -k 没选到任何用例（退出码 5）—— 是选择器写错了，不是判据没响")
        elif code == 0:
            print(f"✗ {label}\n   仍然全绿 ⇒ 假绿")
        elif not hit:
            print(f"✗ {label}\n   非 0 退出但不是预期用例：{failed}")
        else:
            ok += 1
            print(f"✓ {label}")
            for f in hit:
                print(f"   {f}")

    same = all(hashlib.sha256(p.read_bytes()).hexdigest() == before[p] for p in originals)
    print(f"\n还原核对：{'一致' if same else '不一致！'}（tenants.py + service.py）")
    print(f"判据被证明会响：{ok}/{len(MUTATIONS)}")
    return 0 if (ok == len(MUTATIONS) and same) else 1


if __name__ == "__main__":
    raise SystemExit(main())
