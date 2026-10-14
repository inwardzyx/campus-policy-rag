# -*- coding: utf-8 -*-
"""`check_test_counts.py` 的变异测试：证明它在坏情况下**真的会响**。

## 为什么单独一个脚本、不塞进现有的 mutate_check_*.py

现有那四个变异套件分别盯：数字对账（README↔产物）、产物合理性、判据纪律、路径引用。
本条盯的是**第五类对象**：README 里"CI 跑哪些测试文件"这句声明 ↔ pytest 真实收集结果。
目标不同、注入方式也不同（要跑 pytest），塞进任何一个都是硬凑。

## ★ 三态判据（本仓 2026-10-14 那次踩坑的教训）

原来的写法把"没注入上"直接算失败。但这条判据**在仓库状态下有合法的不适用**：
CI 里 `test_limits.py` 在快 job 跑（有 Redis service），而本机的 CI 定义不同时
"少写一个文件"可能本来就成立。
⇒ 所以这里分三态，**只有 `漏检` 才算失败**：
    · 注入上 + 被检出   → ✓ 通过
    · 注入上 + 没检出   → ✗ 漏检（判据抓不到它声称能抓的）
    · **注入不上**（原文不存在）→ ⚠ 不适用，**不算失败**，但必须打印出来
★ 边界说清：第三种会让这个套件在"README 大改结构"后静默变成空转 ——
  所以收尾要**断言至少有一条真的注入上了**，否则就是空转的绿。

跑法：
    <venv>/Scripts/python.exe scripts/mutate_check_test_counts.py
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
CHECK = ROOT / "scripts" / "check_test_counts.py"
README = ROOT / "README.md"

# (说明, 要替换的原文, 替换成什么, 期望被哪条判据抓到)
MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "★ 快 job 少写一个测试文件（test_limits.py 从 README 里删掉）",
        "`tests/test_loader.py` + `tests/test_limits.py`",
        "`tests/test_loader.py`",
        "「job 跑的测试文件和 README 写的不一致」+「有测试文件不在前两个 job 里」",
    ),
    (
        "★ 快 job 多写一个不存在的文件（凑数）",
        "`tests/test_loader.py` + `tests/test_limits.py`",
        "`tests/test_loader.py` + `tests/test_limits.py` + `tests/test_loader.py`",
        "同上（多写）",
    ),
    (
        "★ 把 full job 的表格行整行删掉（README 抠不到那个 job）",
        "| **full** | `requirements.txt` + 下载 92MB 模型 |",
        "| **fullx** | `requirements.txt` + 下载 92MB 模型 |",
        "「README 里抠不到 job「full」跑哪些测试文件」",
    ),
]


def run_check() -> tuple[int, str]:
    r = subprocess.run([sys.executable, str(CHECK)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=str(ROOT))
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def main() -> int:
    original = README.read_text(encoding="utf-8")

    code, out = run_check()
    if code != 0:
        print("✗ 未注入变异时检查就报错了 —— 先修好再测：")
        print(out)
        return 2
    print("✓ 基线：未注入变异时报绿")

    undetected: list[str] = []
    not_applied: list[str] = []
    applied = 0
    for desc, old, new, expect in MUTATIONS:
        if old not in original:
            not_applied.append(desc)
            print(f"⚠ 不适用（原文不存在）：{desc}")
            continue
        README.write_text(original.replace(old, new, 1), encoding="utf-8", newline="")
        try:
            code, out = run_check()
            if code == 0:
                undetected.append(desc)
                print(f"✗ 漏检：{desc}（期望由{expect} 抓到）")
            else:
                first = next((ln.strip() for ln in out.splitlines()
                              if ln.strip().startswith("✗")), "(没打印错误行)")
                print(f"✓ 检出：{desc}")
                print(f"    {first}")
                applied += 1
        finally:
            README.write_text(original, encoding="utf-8", newline="")

    code, out = run_check()
    if code != 0:
        print("✗ 全部恢复后仍报错 —— README 可能没还原干净")
        print(out)
        return 2

    print()
    if not_applied:
        print(f"⚠ {len(not_applied)} 条不适用（仓库状态所限，不算失败）")
    if undetected:
        print(f"✗ {len(undetected)} 条漏检 —— 判据抓不到它声称能抓的东西：")
        for d in undetected:
            print(f"    - {d}")
        return 1
    if applied == 0:
        print("✗ 一条变异都没注入上 —— 这是**空转的绿**，不能算通过")
        return 1
    print(f"✓ {applied} 条变异全部检出，恢复后仍报绿")
    return 0


if __name__ == "__main__":
    sys.exit(main())
