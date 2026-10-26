#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""mutate_versioning.py —— 证明 as-of 的两条判据在坏情况下会响。

变异体：
  A. 忽略 `event_time`（把 `if event_time:` 改成 `if False:`）⇒ 未来条款会被当现行；
  B. 不丢废止版（`elif _status(...) == DEPRECATED:` 改成 `elif False:`）⇒ 废止条款回到答案里。

★ 这两种坏法在接口上都**完全正常**：答案流利、来源齐全，只是把旧条款当新条款。
  所以它们必须各有一条用例变红，而不是靠人看答案。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
TESTS = "tests/test_versioning.py"
KB_VER = ROOT / "kb" / "versioning.py"
SERVICE = ROOT / "service.py"

#: (说明, 目标文件, 变异锚点, 替换成, pytest -k, 期望变红的用例)
MUTATIONS = [
    (
        "A · 忽略 event_time（未来条款被当现行）",
        KB_VER,
        "            if event_time:\n",
        "            if False:\n",
        "event_time",
        ["test_future_clause_is_dropped_when_event_time_is_past",
         "test_chat_with_event_time_filters_before_answering"],
    ),
    (
        "B · 不丢废止版（旧条款回到答案里）",
        KB_VER,
        "            elif _status(chunk) == STATUS_DEPRECATED:\n",
        "            elif False:\n",
        "deprecated",
        ["test_deprecated_is_dropped_without_event_time",
         "test_chat_without_event_time_drops_deprecated"],
    ),
    (
        "C · 版本兄弟判定失效（入库去重又吃掉新版）",
        SERVICE,
        '    return (str(a.get("version") or ""), ea) != (str(b.get("version") or ""), eb)\n',
        "    return False\n",
        "version_siblings",
        ["test_version_siblings_are_not_duplicates"],
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
    originals = {p: p.read_bytes() for p in (KB_VER, SERVICE)}
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
    print(f"\n还原核对：{'一致' if same else '不一致！'}（kb/versioning.py + service.py）")
    print(f"判据被证明会响：{ok}/{len(MUTATIONS)}")
    return 0 if (ok == len(MUTATIONS) and same) else 1


if __name__ == "__main__":
    raise SystemExit(main())
