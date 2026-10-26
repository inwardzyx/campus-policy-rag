#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""mutate_normalize.py —— 证明"真实 PDF 能切出条款"这条判据会响。

变异体：
  A. 补完标题后**不走** markdown 切块（仍然退回定长切块）⇒ 条款号丢失，用例必须红；
  B. 去掉"长尾正文不许当标题"的挡板 ⇒ 正文行被误提成条款，用例必须红。

★ 这两种坏法都不会崩：A 让真实 PDF 静默退回"无条款块"，B 让正文多出假条款。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "kb" / "source.py"
NORMALIZE = ROOT / "kb" / "normalize.py"
TESTS = "tests/test_normalize.py"

MUTATIONS = [
    ("A · 补完标题仍退回定长切块",
     SOURCE,
     "        if added or re.search(r\"(?m)^#{1,3}\\s+\", text):\n            return chunk_markdown(text, raw.meta)\n        return chunk_plain_text(raw.text, raw.meta)\n",
     "        return chunk_plain_text(raw.text, raw.meta)\n",
     "pdf_text_without", ["test_pdf_text_without_headings_gets_clause_blocks"]),
    ("B · 去掉长尾挡板（正文误提成条款）",
     NORMALIZE,
     '        if rest.startswith(("所述", "所指", "规定", "明确", "所称", "之下")):\n'
     "            out.append(line)\n            continue\n",
     "",
     "long_tail", ["test_long_tail_is_not_treated_as_heading"]),
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
    originals = {p: p.read_bytes() for p in (SOURCE, NORMALIZE)}
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

    same = all(hashlib.sha256(p.read_bytes()).hexdigest() == before[p] for p in originals)
    print(f"\n还原核对：{'一致' if same else '不一致！'}（source.py + normalize.py）")
    print(f"判据被证明会响：{ok}/{len(MUTATIONS)}")
    return 0 if (ok == len(MUTATIONS) and same) else 1


if __name__ == "__main__":
    raise SystemExit(main())
