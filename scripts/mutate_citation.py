#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""mutate_citation.py —— 证明引用校验的两条判据**在坏情况下真的会响**。

变异体：
  A. 编号整体偏移（`start=1` 改 `start=0`）⇒ 模板不再从 [1] 开始，编号/引用全错位；
  B. 越界编号不再写进 `degraded`（把标记那一行变成 `if False:`）⇒ 编了引用也没人知道。

★ 只判"退出码非 0"是假红：要求**指定用例**出现在 FAILED 行里。
★ 还原：把 service.py 原始字节写回并比 sha256 —— 不靠"记得改回来"。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
SERVICE = ROOT / "service.py"
TESTS = "tests/test_citation.py"

MUTATIONS = [
    (
        "A · 编号从 0 开始（引用整体错位一位）",
        "    return [{**c, \"cite_id\": i} for i, c in enumerate(chunks, start=1)]\n",
        "    return [{**c, \"cite_id\": i} for i, c in enumerate(chunks, start=0)]\n",
        "numbered",
        ["test_namespace_prompt_is_numbered_and_citation_attached"],
    ),
    (
        "B · 越界编号不再标记 degraded（编了引用也没人知道）",
        '    if report.get("unknown_ids"):\n        context_summary.degraded["citation"] = "unknown_id"\n',
        '    if False:\n        context_summary.degraded["citation"] = "unknown_id"\n',
        "degraded",
        ["test_unknown_citation_id_is_reported_as_degraded"],
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
    original = SERVICE.read_bytes()
    before = hashlib.sha256(original).hexdigest()
    text = original.decode("utf-8")
    ok = 0

    for label, old, new, kw, expect_ids in MUTATIONS:
        n = text.count(old)
        if n != 1:
            print(f"✗ [{label}] 变异锚点命中 {n} 处（应为 1）—— 用例没测到真代码")
            continue
        SERVICE.write_bytes(text.replace(old, new, 1).encode("utf-8"))
        try:
            code, failed, summary = run_tests(kw)
        finally:
            SERVICE.write_bytes(original)

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

    after = hashlib.sha256(SERVICE.read_bytes()).hexdigest()
    same = before == after
    print(f"\nservice.py 还原核对：{'一致' if same else '不一致！'}")
    print(f"判据被证明会响：{ok}/{len(MUTATIONS)}")
    return 0 if (ok == len(MUTATIONS) and same) else 1


if __name__ == "__main__":
    raise SystemExit(main())
