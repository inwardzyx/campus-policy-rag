#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""mutate_eval_loop.py —— 证明闭环的判据会响。

变异体：
  A. 门禁去掉"回退"检查 ⇒ 有回退的候选会被放过（最危险的一种假绿）；
  B. 扩展词表去掉"已存在就不加"⇒ 同一查询调两次会把词加两遍（幂等性丢失）；
  C. McNemar 永远返回 1.0 ⇒ 严格模式永远拒（"显著性"名存实亡）；
  D. 分类忽略题面 tags ⇒ boundary 题会被错误归到 out_of_scope。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
GATE = ROOT / "kb" / "loopgate.py"
EXPAND = ROOT / "kb" / "expand.py"
CATEGORIES = ROOT / "kb" / "categories.py"

MUTATIONS = [
    # (标签, 目标文件, 原文, 替换, 测试文件, -k 关键词, 期望失败用例)
    ("A · 门禁不查回退", GATE,
     '    if losses:\n        verdict = "reject"\n        reasons.append(f"有回退：{losses}")\n',
     "",
     "tests/test_eval_loop.py", "regression", ["test_gate_rejects_any_regression"]),
    ("B · 扩展不判重", EXPAND,
     "                if not n or n in already or n in norm:\n                    continue\n",
     "                if not n:\n                    continue\n",
     "tests/test_eval_loop.py", "idempotent", ["test_apply_appends_and_is_idempotent"]),
    ("C · McNemar 永远不显著", GATE,
     "    return min(1.0, 2 * tail)\n",
     "    return 1.0\n",
     "tests/test_grouping_significance.py", "mcnemar", ["test_mcnemar_exact_values"]),
    ("D · 分类忽略 tags", CATEGORIES,
     "    if tags:\n        return str(tags[0])\n",
     "    if False:\n        return str(tags[0])\n",
     "tests/test_grouping_significance.py", "tags_override", ["test_category_tags_override"]),
]


def run_tests(testfile: str, kw: str) -> tuple[int, list[str]]:
    r = subprocess.run(
        [sys.executable, "-m", "pytest", testfile, "-q", "--no-header",
         "-p", "no:cacheprovider", "-k", kw],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    out = (r.stdout or "") + (r.stderr or "")
    return r.returncode, [ln.strip() for ln in out.splitlines() if ln.strip().startswith("FAILED")]


def main() -> int:
    originals = {p: p.read_bytes() for p in (GATE, EXPAND, CATEGORIES)}
    before = {p: hashlib.sha256(b).hexdigest() for p, b in originals.items()}
    texts = {p: b.decode("utf-8") for p, b in originals.items()}
    ok = 0

    for label, target, old, new, testfile, kw, expect in MUTATIONS:
        text = texts[target]
        if text.count(old) != 1:
            print(f"✗ [{label}] 锚点命中 {text.count(old)} 处")
            continue
        target.write_bytes(text.replace(old, new, 1).encode("utf-8"))
        try:
            code, failed = run_tests(testfile, kw)
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
