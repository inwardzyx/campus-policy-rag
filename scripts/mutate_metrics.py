#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""mutate_metrics.py —— 证明容量/成本判据会响。

变异体：
  A. 分位数用 `int(p*n)-1`（少一格）⇒ p95 变成第 4 个，"容量结论"会系统性偏低；
  B. `_note_usage` 直接 return ⇒ 面板永远 0（假指标）。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
LOADSTATS = ROOT / "kb" / "loadstats.py"
SERVICE = ROOT / "service.py"

MUTATIONS = [
    ("A · 分位数少取一格", LOADSTATS,
     "    idx = min(len(data) - 1, max(0, math.ceil(p * len(data)) - 1))\n",
     "    idx = min(len(data) - 1, max(0, int(p * len(data)) - 1))\n",
     "loadstats", ["test_p95_uses_nearest_rank"]),
    ("B · 用量计数不累加", SERVICE,
     '    _USAGE_TOTALS["requests"] += 1\n',
     "    return\n",
     "usage_counters", ["test_usage_counters_accumulate"]),
]


def run_tests(kw: str) -> tuple[int, list[str]]:
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_loadstats.py", "tests/test_metrics_endpoints.py",
         "-q", "--no-header", "-p", "no:cacheprovider", "-k", kw],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    out = (r.stdout or "") + (r.stderr or "")
    return r.returncode, [ln.strip() for ln in out.splitlines() if ln.strip().startswith("FAILED")]


def main() -> int:
    originals = {p: p.read_bytes() for p in (LOADSTATS, SERVICE)}
    before = {p: hashlib.sha256(b).hexdigest() for p, b in originals.items()}
    texts = {p: b.decode("utf-8") for p, b in originals.items()}
    ok = 0

    for label, target, old, new, kw, expect in MUTATIONS:
        text = texts[target]
        if text.count(old) != 1:
            print(f"✗ [{label}] 锚点命中 {text.count(old)} 处")
            continue
        target.write_bytes(text.replace(old, new, 1).encode("utf-8"))
        try:
            code, failed = run_tests(kw)
        finally:
            target.write_bytes(originals[target])
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

    same = all(hashlib.sha256(p.read_bytes()).hexdigest() == before[p] for p in originals)
    print(f"\n还原核对：{'一致' if same else '不一致！'}（loadstats.py + service.py）")
    print(f"判据被证明会响：{ok}/{len(MUTATIONS)}")
    return 0 if (ok == len(MUTATIONS) and same) else 1


if __name__ == "__main__":
    raise SystemExit(main())
