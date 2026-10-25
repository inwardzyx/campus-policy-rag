#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""mutate_context_layer.py —— 证明上下文层那两条关键判据**在坏情况下真的会响**。

## 为什么要这个脚本
`tests/test_context.py` 里有两条"不变式"性质的用例：

  ① `docs/` 旧路径不启用上下文层（`context=None`、prompt 逐字节不变、流式 payload 无 `context` 键）；
  ② 预算裁掉的块**必须真的不喂**给生成模型（trace 与 ctx 同源）。

这两条一旦被写坏（比如有人把 gating 扩成"有 roles 就开"、或者先算 trace 再自己截），
**表现都是"看着对"**：接口正常、答案正常、trace 也像模像样。所以它们必须被反向验证一次：
故意打坏 → 指定用例必须变红 → 再按**原字节**还原。

★ 只判"退出码非 0"不够：收集错误/导入错误也是非 0，那是**假红**，
  证明不了判据在响。所以每个变异体都要求**指定用例出现在 FAILED 行里**。

★ 还原方式：把 `service.py` 的原始字节读进内存，跑完**按原字节写回**，再比 sha256 ——
  绝不靠"记得改回来"（那条纪律是被别的项目留下的临时改动坑出来的）。

用法（在仓库根，或用完整路径调）：
    D:\\Python-project\\.venv\\Scripts\\python.exe scripts\\mutate_context_layer.py
退出码 0 = 两个变异体都变红且字节还原一致。
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
TEST_FILE = "tests/test_context.py"

#: (说明, 变异锚点, 替换成, pytest -k 表达式, 期望出现在 FAILED 行里的用例名)
MUTATIONS = [
    (
        "A · 旧路径 gating 被打掉（namespace 判断恒真）",
        '    if not getattr(req, "namespace", None):\n        return list(chunks), None\n',
        "    if False:\n        return list(chunks), None\n",
        "docs_path or no_context_key or roles_do_not",
        ["test_docs_path_prompt_is_byte_identical_and_has_no_context",
         "test_stream_docs_payload_has_no_context_key"],
    ),
    (
        "B · trace 与喂进模型的块脱钩（裁掉了却照样喂）",
        "    return list(kept), ContextSummary.from_trace(trace)\n",
        "    return list(chunks), ContextSummary.from_trace(trace)\n",
        "prunes",
        ["test_namespace_chat_prunes_blocks_from_the_prompt_too",
         "test_stream_prunes_over_budget_blocks"],
    ),
]


def run_tests(kw: str) -> tuple[int, list[str], str]:
    r = subprocess.run(
        [sys.executable, "-m", "pytest", TEST_FILE, "-q", "--no-header",
         "-p", "no:cacheprovider", "-k", kw],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    out = (r.stdout or "") + (r.stderr or "")
    failed = [ln.strip() for ln in out.splitlines() if ln.strip().startswith("FAILED")]
    summary = [ln.strip() for ln in out.splitlines()
               if (" passed" in ln or " failed" in ln or " error" in ln) and "=" in ln]
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
            SERVICE.write_bytes(original)              # 无论成败都还原原始字节

        hit = [f for f in failed if any(pid in f for pid in expect_ids)]
        if code == 0:
            print(f"✗ {label}\n   变异后仍然全绿 ⇒ 这条判据是假绿")
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
