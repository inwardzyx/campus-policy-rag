"""`check_file_refs.py` 的变异测试：造出"路径指错了"，验证判据会响、且诊断指对原因。

为什么需要这个
----------------
`check_file_refs.py` 盯的是「文档/脚本里写的仓库内路径，文件真的在不在」。
它上岗第一天就抓出 5 处真错（README 指一份不存在的报告、
两个文件把 `mcp/` 的探针写成 `agent/`、注释指一个不存在的 tests 文件、
夹具说明指 `tests/fixtures/dirty_inbox/README.md`）。　refcheck-ignore
⇒ 判据本身也必须被证明"在坏情况下会响"，否则它只是又一次"看起来检查过了"。

★ 三种方向都要测，缺一种就会出现"看着测过了、其实那个方向从没跑过"：
    ① 载体侧：把**已存在**的路径写错（最常见的手滑）
    ② 数据侧：被引用的文件**消失**（判据存在的根本理由：文件删了、引用还留着）
    ③ 真空自检：载体列表坏掉时不许报绿（扫不到东西的检查器一定在报假绿）

★★ 一个自指的坑（写这个文件时当场踩到）：
    下面那些"坏路径"如果按字面写成本文件里的字符串，
    `check_file_refs.py` 会先报**我自己这一行**（它扫的载体包括 scripts/**/*.py）。
    这不是判据太严，而是它分不清"我在造一个坏路径"和"我真写错了路径"。
    处理办法有两条，本文件两条都用：
      · 运行时**拼**出来的坏路径（`"evalset/report-vector" + "X.md"`）—— 字面上不构成路径
      · 正文里**引述**历史错路径的地方，那一行加 `refcheck-ignore`

用法：
    python scripts/mutate_check_refs.py

判据：
  - 每条变异都必须被检出，且报出的内容里有该变异注入的那个路径
  - 不注入时必须全绿；全部还原后必须还绿
  - 注入失败（找不到原文串）**算失败**，不许静默跳过
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# ★ 与同族脚本一致：✓✗ 在 cp936 里编不出来，stdout 被管道接住时会崩在打印上。
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parent.parent
CHECK = REPO_ROOT / "scripts" / "check_file_refs.py"
PY = sys.executable

# 运行时拼出来的坏路径（字面上不构成"仓库内路径"，免得判据先报本文件）
BAD_REPORT = "evalset/report-vector" + "X.md"
BAD_TEST = "tests/test_guard" + "_missing.py"

# (说明, 要改的文件, 原文, 替换成, 期望报出来的内容里包含什么)
MUTATIONS: list[tuple[str, str, str, str, str]] = [
    (
        "载体侧：README 里一个存在的路径写错一个字符",
        "README.md",
        "`evalset/report-vector.md`",
        f"`{BAD_REPORT}`",
        BAD_REPORT,
    ),
    (
        "载体侧：脚本注释里的路径写错（这次指向 tests/ 下一个不存在的文件）",
        "scripts/check_token_report.py",
        "tests/test_guard.py 里的",
        f"{BAD_TEST} 里的",
        BAD_TEST,
    ),
    (
        "★★ 真空自检：载体列表被改空 ⇒ 不许报绿（'扫不到东西'必须是红的）",
        "scripts/check_file_refs.py",
        "    for pat in CARRIER_GLOBS:",
        "    for pat in ():",
        "一个都没扫到",
    ),
]


def run_check() -> tuple[int, str]:
    r = subprocess.run([PY, str(CHECK)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=str(REPO_ROOT))
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def data_side_case() -> str | None:
    """② 数据侧：把**被引用且存在**的文件临时改名，判据必须报"这个文件不存在"。

    ★ 这条才是判据存在的理由本身：文件被删/被改名，引用还留在文档里。
      载体侧的变异证明不了它 —— 那种情况文件还在，只是名字写错了。
    ★ 收尾放在 finally 里，且**不在 finally 里 return**
      （`return` 出现在 finally 会吞掉异常 —— 本项目记过"脚本崩在改到一半"那种事故）。
    """
    target = REPO_ROOT / "evalset" / "report-vector.md"
    backup = target.with_name(target.name + ".refcheck_bak")
    if not target.exists():
        return "找不到探针文件 evalset/report-vector.md —— 这条方向没测到"
    if backup.exists():
        return f"{backup.name} 已存在（上次没还原干净？）—— 先人工处理"

    err: str | None = None
    try:
        target.rename(backup)
        code, out = run_check()
        if code == 0:
            err = "被引用的文件改名后，判据仍然报绿 —— 数据侧这个方向没被覆盖"
        elif "evalset/report-vector.md" not in out:
            err = f"报红了，但没点出那个消失的路径。它报的是：{out.strip()[:200]}"
        else:
            print("✓ 检出：★★ 数据侧——被引用的文件改名/消失")
            print(f"    {next((ln.strip() for ln in out.splitlines() if '不存在' in ln), '')}")
    finally:
        if backup.exists():
            backup.rename(target)
    if not target.exists():
        return "✗✗ 还原失败：evalset/report-vector.md 不见了，手工恢复！"
    return err


def main() -> int:
    if not CHECK.exists():
        print(f"✗ 找不到 {CHECK.relative_to(REPO_ROOT)}")
        return 2

    # ---- 0. 基线必须绿 ----
    code, out = run_check()
    if code != 0:
        print("✗ 不注入任何变异时判据就报错 —— 先修路径本身：")
        print(out)
        return 2
    print(f"✓ 基线：{out.strip()}")

    missed: list[str] = []
    misdiagnosed: list[str] = []
    skipped: list[str] = []
    backups: dict[Path, bytes] = {}

    # ---- 1. 载体侧逐条注入 ----
    try:
        for desc, rel, old, new, expect in MUTATIONS:
            path = REPO_ROOT / rel
            original = path.read_bytes()
            backups[path] = original
            text = original.decode("utf-8")
            if old not in text:
                skipped.append(f"{desc}（{rel} 里找不到原文 {old!r}）")
                print(f"? 跳过：{desc}")
                continue
            path.write_bytes(text.replace(old, new, 1).encode("utf-8"))
            try:
                code, out = run_check()
                if code == 0:
                    missed.append(desc)
                    print(f"✗ 漏检：{desc}")
                elif expect not in out:
                    misdiagnosed.append(f"{desc}（期望提到 {expect!r}）")
                    print(f"✗ 检出但诊断不对：{desc}")
                    print(f"    期望提到：{expect}")
                    print(f"    实际报的是：{out.strip()[:200]}")
                else:
                    print(f"✓ 检出：{desc}")
            finally:
                path.write_bytes(original)

        # ---- 2. 数据侧：被引用的文件消失 ----
        err = data_side_case()
        if err:
            print(f"✗ {err}")
            missed.append("数据侧：被引用的文件消失")
    finally:
        for path, blob in backups.items():
            path.write_bytes(blob)

    # ---- 3. 全部还原后必须还绿 ----
    code, out = run_check()
    if code != 0:
        print("✗ 全部还原后仍报错 —— 有文件没还原干净：")
        print(out)
        return 2

    print()
    if missed or misdiagnosed:
        print(f"✗ 漏检 {len(missed)} 条、诊断不对 {len(misdiagnosed)} 条：")
        for m in missed + misdiagnosed:
            print(f"  - {m}")
        return 1
    if skipped:
        # ★ 注入失败必须算失败：报告/文案改了之后，变异原文串会失效，
        #   而"这条方向没测到"跟"全部测过"绝不能在输出上长得一样。
        print(f"✗ {len(skipped)} 条变异注入失败（退出码非 0）：这些方向没测到")
        for s in skipped:
            print(f"  - {s}")
        print("  ⇒ 修掉原文串，重跑。")
        return 1
    print(f"✓ 载体侧 {len(MUTATIONS)} 条 + 数据侧 1 条变异全部检出，诊断都指对了原因")
    return 0


if __name__ == "__main__":
    sys.exit(main())
