# -*- coding: utf-8 -*-
"""README 里的**测试条数**必须与真实收集结果一致 —— 现算，不许写死。

## 为什么要有这个脚本（2026-10-14）

README 那句「前两个 job 加起来 **覆盖全部 57 条**（22 + 35）」是**过期的**：
实测收集到 **145** 条，而 CI 的 job 划分和测试文件都变过好几轮
（`test_limits.py` 后来加进了快 job，`test_guard.py` 从 35 涨到 50）。
⇒ 过期条数比没有条数更坏 —— 它长得像实测，而且**自相矛盾**：
同一份 README 里 171 行写「60 passed」、1672 行写「57 条」，两处打架。

★★ 关键取舍：**条数本身不是判据，能"现算"才是。**
  所以这里不把 145 写进 README 当常量，而是让 README 声明**每个 job 跑哪些文件**，
  由本脚本 `pytest --collect-only` 现算并逐格比对。
  测试再涨，README 只有在**跑的文件**变了时才需要改 —— 那种改动本来就该人手看一眼。

★ 为什么用 `--collect-only` 而不是 `pytest`：收集不需要 Redis / 不需要模型 / 不需要 key，
  全程约 3 秒，可以挂在 pre-commit 上。真的跑一遍要几十秒且有环境依赖，挂钩子不合适。

跑法：
    <venv>/Scripts/python.exe scripts/check_test_counts.py
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

# ★ print 里的 ✓/✗ 在 cp936 下编不出来（stdout 是管道时）⇒ 崩在打印上。本项目已犯 7 次。
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"

# ★ 这张表**不是** README 的副本，而是"哪个 job 该跑哪些文件"的声明。
#   条数由 collect 现算，绝不写死 —— 写死就等于把数据抄一份到自己家里（假绿第二种形态）。
JOBS = {
    "fast": ["tests/test_loader.py", "tests/test_limits.py"],
    "full": ["tests/test_guard.py", "tests/test_eval_set.py", "tests/test_llm_resilience.py"],
    "docker": ["tests/test_guard.py"],          # 容器里再跑一遍，不增加覆盖
}


def collect_counts() -> dict[str, int]:
    """跑一次 `pytest --collect-only -q`，按文件统计收集到的用例数。

    ★ 一次调用拿全部：`-q` 模式每个 item 一行（`path::name`），
      按 `::` 前那段 group 一下就得到逐文件条数。
      逐文件各跑一次 pytest 要多花十几秒，挂在钩子上不划算。
    """
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "--collect-only", "-q"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(ROOT),
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    out = (r.stdout or "") + (r.stderr or "")
    counts: dict[str, int] = {}
    for line in out.splitlines():
        if "::" not in line:
            continue
        f = line.split("::", 1)[0].strip().replace("\\", "/")
        if f.endswith(".py"):
            counts[f] = counts.get(f, 0) + 1
    total_m = re.search(r"(\d+)\s+tests? collected", out)
    counts["__total__"] = int(total_m.group(1)) if total_m else -1
    return counts


def read_readme_claims() -> dict[str, list[str]]:
    """从 README 里抠出"每个 job 跑哪些测试文件"。

    ★ 抠的是**文件名**而不是条数 —— 这正是本次要做的事：
      把"会腐烂的条数"换成"不会腐烂的文件清单"。

    ★★ 返回 **list 不是 set**（2026-10-14：变异测试当场抓出来的洞）：
      第一版用 set，于是「`test_loader.py` + `test_loader.py`」这种**重复写**被吃掉，
      而变异②正是打这个方向（多写一个文件名凑数）⇒ **漏检**。
      ⇒ 判"多写/少写"必须保留重复项：用 `collections.Counter` 比，别用集合。
    """
    text = README.read_text(encoding="utf-8")
    claims: dict[str, list[str]] = {}
    for job in JOBS:
        # 表格那三行的行首是 `| **fast** | …`，job 名带星号
        m = re.search(rf"^\|\s*\*\*{job}\*\*\s*\|(.*)$", text, re.M)
        claims[job] = [f"tests/{n}" for n in re.findall(r"(test_\w+\.py)", m.group(1))] \
            if m else []
    return claims


def main() -> int:
    counts = collect_counts()
    if counts.get("__total__", -1) < 0:
        print("✗ pytest --collect-only 没报出总数 —— 收集阶段就炸了，先修环境")
        return 1
    total = counts["__total__"]
    if total == 0:
        print("✗ 收集到 0 条用例 —— 这是空转的绿，不是通过")
        return 1

    claims = read_readme_claims()
    bad: list[str] = []
    for job, files in JOBS.items():
        want = Counter(files)
        if not claims[job]:
            bad.append(f"  README 里抠不到 job「{job}」跑哪些测试文件 —— "
                       f"表格结构或写法变了，本脚本没跟上（★ 别直接删这条，先确认新写法）")
            continue
        got = Counter(claims[job])
        missing = want - got
        extra = got - want
        if missing or extra:
            bad.append(f"  job「{job}」跑的测试文件和 README 写的不一致："
                       f"README 少写了 {sorted(missing.elements()) or '无'}，"
                       f"多写了 {sorted(extra.elements()) or '无'}")

    # ★★ 真正的判据：**前两个 job 的并集必须覆盖全部测试文件**
    #    这才是 README 那句承诺的实质（"不存在只在本地跑过的测试"）。
    #    只比条数的话，删掉一个文件、把另一个文件多跑几遍也能凑出同一个数。
    covered = set(JOBS["fast"]) | set(JOBS["full"])
    all_files = {k for k in counts if k.endswith(".py")}
    uncovered = all_files - covered
    if uncovered:
        bad.append(f"  ★ 有测试文件不在前两个 job 里：{sorted(uncovered)} ⇒ "
                   f"README 那句「不存在只在本地跑过的测试」就是假话")
    # 反向：声明的文件必须真的存在且有用例（防"声明了一个空文件"）
    for f in sorted(covered):
        if counts.get(f, 0) == 0:
            bad.append(f"  {f} 收集到 0 条用例 —— 它在 CI 里跑，但什么都没测")

    # 逐文件条数打印出来（给人看，同时进 CI 日志便于复核）
    print(f"现算：共收集 {total} 条")
    for job, files in JOBS.items():
        parts = " + ".join(f"{Path(f).name} {counts.get(f, 0)}" for f in files)
        print(f"  {job:7s} {parts}")
    union_files = sorted(covered)
    union_n = sum(counts.get(f, 0) for f in union_files)
    print(f"  前两个 job 并集：{union_n} 条（{len(union_files)} 个文件）"
          f"　docker 不增加覆盖")

    if bad:
        print(f"\n✗ {len(bad)} 处对不上：")
        print("\n".join(bad))
        return 1
    print("✓ README 声明的测试文件与真实收集结果一致；前两个 job 覆盖全部测试文件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
