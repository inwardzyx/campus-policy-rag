#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""mutate_cache.py —— 证明缓存的判据会响。

变异体：
  A. `key_of` 只取第一个参数 ⇒ 不同历史共用一条改写（多轮指代会串味）；
  B. `LRUCache.get` 永远未命中 ⇒ 缓存等于没有，但测试如果只测延迟就看不出来。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "cache.py"
TESTS = "tests/test_cache.py"

MUTATIONS = [
    ("A · key 只取第一个参数", TARGET,
     '    for part in parts:\n        h.update(repr(part).encode("utf-8"))\n        h.update(b"\\x1f")\n',
     '    h.update(repr(parts[0] if parts else "").encode("utf-8"))\n',
     "key_includes_history", ["test_key_includes_history"]),
    ("B · get 永远未命中", TARGET,
     "            if key not in self._data:\n                self.misses += 1\n                return None\n",
     "            if True:\n                self.misses += 1\n                return None\n",
     "skips_model", ["test_rerank_hit_skips_model_call"]),
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
    print(f"\n还原核对：{'一致' if same else '不一致！'}（cache.py）")
    print(f"判据被证明会响：{ok}/{len(MUTATIONS)}")
    return 0 if (ok == len(MUTATIONS) and same) else 1


if __name__ == "__main__":
    raise SystemExit(main())
