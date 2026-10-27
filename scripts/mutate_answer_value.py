#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""mutate_answer_value.py —— 证明 P0 判据会响。

变异体：
  A. `match()` 永远返回 True ⇒ 值写错也判通过（这正是要防的"看得见对、看不见错"）；
  B. 拒答细分永远落 refuse_fabricated ⇒ "引用过块"的越界就查不出来。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
VALUES = ROOT / "kb" / "values.py"
ATTR = ROOT / "kb" / "attribution.py"
TESTS = "tests/test_answer_value.py"

MUTATIONS = [
    ("A · 值匹配永远通过", VALUES,
     '    got = extract(answer or "")\n    return any(pair in got for pair in expected)\n',
     "    return True\n",
     "wrong_value", ["test_score_row_flags_wrong_value"]),
    ("B · 拒答永远记成编造", ATTR,
     '        if used:\n            out.update(bucket="refuse_context_overreach",',
     '        if False:\n            out.update(bucket="refuse_context_overreach",',
     "overreach", ["test_refuse_answered_with_citations_is_overreach"]),
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
    originals = {p: p.read_bytes() for p in (VALUES, ATTR)}
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
    print(f"\n还原核对：{'一致' if same else '不一致！'}")
    print(f"判据被证明会响：{ok}/{len(MUTATIONS)}")
    return 0 if (ok == len(MUTATIONS) and same) else 1


if __name__ == "__main__":
    raise SystemExit(main())
