#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""mutate_session.py —— 证明会话轨迹评测的两条判据在坏情况下会响。

变异体：
  A. `rewrite_resolved` 恒 False（指代永远算没补全）⇒ 指标归零却不会报错；
  B. trace 里不再带 `session_id` ⇒ 会话串不起来，报告仍能生成。

★ 两种坏法都很安静：A 让指标看起来"就是差"，B 让报告结构看起来"完整"。
  所以必须各有一条用例变红。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
SESSION = ROOT / "kb" / "evalkit" / "session.py"
CTX = ROOT / "kb" / "context.py"
SERVICE = ROOT / "service.py"
TESTS = "tests/test_session_eval.py"

MUTATIONS = [
    (
        "A · rewrite_resolved 恒 False（指代永远算没补全）",
        SESSION,
        '    text = _norm(rewritten_query)\n    return any(_norm(t) in text for t in missing)\n',
        '    return False\n',
        "rewrite_resolved",
        ["test_rewrite_resolved_checks_ellipsis_filling_not_citations"],
    ),
    (
        "B · trace 不再带 session_id（会话键的唯一来源被拿掉）",
        CTX,
        '    if meta.get("session_id"):\n        out["session_id"] = str(meta["session_id"])\n',
        "",
        "session_keys",
        ["test_trace_carries_session_keys_and_turn_index",
         "test_trace_itself_carries_session_keys"],
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
    originals = {p: p.read_bytes() for p in (SESSION, CTX, SERVICE)}
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
    print(f"\n还原核对：{'一致' if same else '不一致！'}（session.py + context.py + service.py）")
    print(f"判据被证明会响：{ok}/{len(MUTATIONS)}")
    return 0 if (ok == len(MUTATIONS) and same) else 1


if __name__ == "__main__":
    raise SystemExit(main())
