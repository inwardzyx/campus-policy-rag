#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""mutate_compress.py —— 证明压缩的两条判据在坏情况下会响。

变异体：
  A. 去重阈值抬到 2.0（永不触发）⇒ 近重复不再被丢；
  B. 噪声正则改成吞掉整行内容（`.*`）⇒ 正文被删。

★ 两种坏法都很"安静"：A 让功能失效但一切正常，B 让回答少一截却看不出原因。
  所以它们必须各有一条用例会变红。
★ 还原按原字节写回 + sha256 核对。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "kb" / "compress.py"
TESTS = "tests/test_compress.py"

MUTATIONS = [
    (
        "A · 去重阈值抬到永不触发",
        "DEFAULT_DEDUP_JACCARD = 0.8\n",
        "DEFAULT_DEDUP_JACCARD = 2.0\n",
        "duplicate",
        ["test_near_duplicate_is_dropped_and_reported"],
    ),
    (
        "B · 噪声正则吞掉整行（正文被删）",
        '    r"^\\s*(?:第\\s*\\d+\\s*页(?:\\s*/\\s*共\\s*\\d+\\s*页)?|\\d{1,3}|[-—_=*·•]{3,}|\\.{4,}|…{3,})\\s*$"\n',
        '    r"^.*$"\n',
        "noise",
        ["test_noise_lines_removed_but_body_kept", "test_digits_inside_a_sentence_are_not_noise"],
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
    print(f"\nkb/compress.py 还原核对：{'一致' if same else '不一致！'}")
    print(f"判据被证明会响：{ok}/{len(MUTATIONS)}")
    return 0 if (ok == len(MUTATIONS) and same) else 1


if __name__ == "__main__":
    raise SystemExit(main())
