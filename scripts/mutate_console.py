#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""mutate_console.py —— 证明控制台的两条判据会响。

变异体：
  A. 页面里出现 `.innerHTML =`（XSS 硬规矩被破坏）⇒ 用例必须红；
  B. 页面把 `/admin/tenants` 改成不存在的 `/admin/tenant` ⇒ "只调已有端点"必须红。
★ 这两种坏法在浏览器里都不会立刻报错：A 渲染看起来"更简单"，B 只有点下去才 404。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "static" / "console.html"
TESTS = "tests/test_console.py"

MUTATIONS = [
    ("A · 页面用了 .innerHTML =", 'document.createElement("td");', 'document.createElement("td"); td2.innerHTML = "";',
     "innerhtml", ["test_console_has_no_innerhtml_assignment"]),
    ("B · 页面调了不存在的管理端点", "/admin/tenants", "/admin/tenant", "known_endpoints",
     ["test_console_only_calls_known_endpoints"]),
]


def run_tests(kw: str) -> tuple[int, list[str]]:
    r = subprocess.run(
        [sys.executable, "-m", "pytest", TESTS, "-q", "--no-header",
         "-p", "no:cacheprovider", "-k", kw],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    out = (r.stdout or "") + (r.stderr or "")
    failed = [ln.strip() for ln in out.splitlines() if ln.strip().startswith("FAILED")]
    return r.returncode, failed


def main() -> int:
    original = PAGE.read_bytes()
    before = hashlib.sha256(original).hexdigest()
    text = original.decode("utf-8")
    ok = 0

    for label, old, new, kw, expect in MUTATIONS:
        if text.count(old) < 1:
            print(f"✗ [{label}] 锚点找不到")
            continue
        PAGE.write_bytes(text.replace(old, new, 1).encode("utf-8"))
        try:
            code, failed = run_tests(kw)
        finally:
            PAGE.write_bytes(original)
        hit = [f for f in failed if any(e in f for e in expect)]
        if code == 5:
            print(f"✗ {label}\n   -k 没选到用例（选择器写错）")
        elif code == 0:
            print(f"✗ {label}\n   仍然全绿 ⇒ 假绿")
        elif not hit:
            print(f"✗ {label}\n   非 0 退出但不是预期用例：{failed}")
        else:
            ok += 1
            print(f"✓ {label}")
            for f in hit:
                print(f"   {f}")

    same = hashlib.sha256(PAGE.read_bytes()).hexdigest() == before
    print(f"\n还原核对：{'一致' if same else '不一致！'}（static/console.html）")
    print(f"判据被证明会响：{ok}/{len(MUTATIONS)}")
    return 0 if (ok == len(MUTATIONS) and same) else 1


if __name__ == "__main__":
    raise SystemExit(main())
