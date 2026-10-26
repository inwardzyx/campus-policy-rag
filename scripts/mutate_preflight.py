#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""mutate_preflight.py —— 证明部署自检的判据在坏情况下会响。

变异体：
  A. 把 `check_api_key` 从 CHECKS 里删掉 ⇒ "没配 key 必须 fail" 必须红；
  B. `main()` 忽略 fail、永远返回 0 ⇒ CLI 退出码判据必须红。

★ 这两种坏法正是"自检看起来跑过了、其实什么都没拦住"的样子：
  A 让最常出事的那一项静默消失，B 让所有项都变成日志。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "scripts" / "preflight.py"
TESTS = "tests/test_preflight.py"

MUTATIONS = [
    ("A · CHECKS 里删掉 key 检查",
     "    check_api_key,\n", "",
     "missing_key", ["test_missing_key_is_a_failure"]),
    ("B · main 忽略 fail 永远返回 0",
     "    if fails:\n        return 1\n", "    if False:\n        return 1\n",
     "cli_exit", ["test_cli_exit_codes"]),
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
    original = TARGET.read_bytes()
    before = hashlib.sha256(original).hexdigest()
    text = original.decode("utf-8")
    ok = 0

    for label, old, new, kw, expect in MUTATIONS:
        if text.count(old) != 1:
            print(f"✗ [{label}] 锚点命中 {text.count(old)} 处")
            continue
        TARGET.write_bytes(text.replace(old, new, 1).encode("utf-8"))
        try:
            code, failed = run_tests(kw)
        finally:
            TARGET.write_bytes(original)
        hit = [f for f in failed if any(e in f for e in expect)]
        if code == 5:
            print(f"✗ {label}\n   -k 没选到用例")
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
    print(f"\n还原核对：{'一致' if same else '不一致！'}（scripts/preflight.py）")
    print(f"判据被证明会响：{ok}/{len(MUTATIONS)}")
    return 0 if (ok == len(MUTATIONS) and same) else 1


if __name__ == "__main__":
    raise SystemExit(main())
