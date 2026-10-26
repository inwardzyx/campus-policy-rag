#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""mutate_audit.py —— 证明审计的两条判据在坏情况下会响。

变异体：
  A. 把**问题原文**写进审计（而不是哈希）⇒ PII 泄漏，用例必须红；
  B. 清理时把**解析不了的行**也删掉 ⇒ 手工改过的审计痕迹被抹掉，用例必须红。

★ 审计这两条都属于"平时看不出来"的坏法：A 让日志看起来更"有用"，
  B 让清理看起来更"干净"。两种都不该靠人眼发现。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "kb" / "audit.py"
TESTS = "tests/test_audit.py"

MUTATIONS = [
    (
        "A · 审计里写问题原文（PII 泄漏）",
        '        "question_sha256": question_hash(question),\n',
        '        "question": question,\n',
        "raw_question",
        ["test_event_has_no_raw_question_and_no_block_text"],
    ),
    (
        "B · 清理时删掉解析不了的行（抹掉手工痕迹）",
        "        except Exception:                                  # noqa: BLE001\n"
        "            kept.append(line)\n"
        "            continue\n",
        "        except Exception:                                  # noqa: BLE001\n"
        "            continue\n",
        "prune",
        ["test_prune_removes_old_and_keeps_bad_lines"],
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
    original = TARGET.read_bytes()
    before = hashlib.sha256(original).hexdigest()
    text = original.decode("utf-8")
    ok = 0

    for label, old, new, kw, expect_ids in MUTATIONS:
        n = text.count(old)
        if n != 1:
            print(f"✗ [{label}] 变异锚点命中 {n} 处（应为 1）")
            continue
        TARGET.write_bytes(text.replace(old, new, 1).encode("utf-8"))
        try:
            code, failed, summary = run_tests(kw)
        finally:
            TARGET.write_bytes(original)

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

    after = hashlib.sha256(TARGET.read_bytes()).hexdigest()
    same = before == after
    print(f"\nkb/audit.py 还原核对：{'一致' if same else '不一致！'}")
    print(f"判据被证明会响：{ok}/{len(MUTATIONS)}")
    return 0 if (ok == len(MUTATIONS) and same) else 1


if __name__ == "__main__":
    raise SystemExit(main())
