# -*- coding: utf-8 -*-
"""`check_test_counts.py` 的变异测试：证明它在坏情况下**真的会响**。

## 为什么单独一个套件

现有那 5 个变异套件分别盯：数字对账（README↔产物）、产物合理性、判据纪律、路径引用、token 报告。
本条盯的是**第六类对象**：README 声明的「每个 CI job 跑哪些测试文件」↔ `ci.yml` 的真实执行行。
目标与注入方式都不同，塞进任何一个都是硬凑。

## ★ 三态判据（本仓三态的正确用法）

    · 注入上 + 被检出   → ✓ 通过
    · 注入上 + 没检出   → ✗ 漏检（判据抓不到它声称能抓的）
    · **注入不上**（锚点不存在）→ ⚠ 不适用，不算失败，但要打印

⚠️ 边界（第一版被 GLM 盲审打回的一点）：
  第一版收尾只断言「**至少有一条**注入上」，那样挡不住「半灭」——
  变异①②共用同一个锚点串，README 一改就双双不适用，只剩③在跑，套件照样绿，
  覆盖率静默从 3 掉到 1。
  ⇒ 现在的口径：**不适用必须显式列出来（白名单化），且断言 applied == 期望条数**，
    有任何一个不适用就打印醒目警告 + 非零退出（"测不到"本身就要有人看一眼）。

## ★★ 行尾：一律 read_bytes / write_bytes（第一版被盲审抓到的第二个真洞）

第一版用 `read_text` / `write_text(newline="")`。读侧把 CRLF 归一成 LF，写回就是 LF。
本机 README 现在是纯 LF ⇒ 今天没事；但 `.gitattributes` **不钉 `*.md`**，而本机
`core.autocrlf=true` ⇒ 换台机器 fresh clone 出 CRLF 版 README 后，
跑一次这个套件就把**整个 README 刷成 LF**，`git diff` 全文件爆炸，
而"恢复检查"也读文本、同样归一，**查不出来**。
★ 同仓 `mutate_check.py:20` 早就写过这条教训（"一律用 read_bytes/write_bytes 并校验哈希"）
—— 第一版恰好退了回去。现在按那条来。

跑法：
    <venv>/Scripts/python.exe scripts/mutate_check_test_counts.py
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
CHECK = ROOT / "scripts" / "check_test_counts.py"
README = ROOT / "README.md"
TESTS_DIR = ROOT / "tests"

# (说明, 要替换的原文, 替换成什么, 期望被哪条判据抓到)
MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "★ 快 job 少写一个测试文件（test_limits.py 从 README 里删掉）",
        "`tests/test_loader.py` + `tests/test_limits.py`",
        "`tests/test_loader.py`",
        "「README 少写了」+「不在任何 CI job 里」",
    ),
    (
        "★ 快 job 多写一个文件（重复写 test_loader.py 凑数）",
        "`tests/test_loader.py` + `tests/test_limits.py`",
        "`tests/test_loader.py` + `tests/test_limits.py` + `tests/test_loader.py`",
        "「README 多写了」",
    ),
    (
        "★ 把 full job 的名字改掉（README 那个 job 抠不到）",
        "| **full** | `requirements.txt` + 下载 92MB 模型 |",
        "| **fullx** | `requirements.txt` + 下载 92MB 模型 |",
        "「在 README 表格里找不到对应行」+「README 里的 job 在 ci.yml 里不存在」",
    ),
]

# ★ 磁盘侧变异：在 tests/ 下放一个**真实的**新测试文件，而 ci.yml 里没有它。
#   为什么必须单独一类：前三条改的都是 README，而这条要打的是
#   「磁盘 ⊃ CI」这个方向 —— 只有真的落一个文件才测得到。
#
# ★★ 路径为什么要拼接出来、而不是写成一个完整的字面量（2026-10-14 实测踩到）：
#   `scripts/check_file_refs.py` 会扫**全仓库文本**里的路径引用，
#   而这个临时文件只在注入那一瞬存在 ⇒ 它会把本行当成"文档里指错了一个文件"报红。
#   （真事：一次全量检查里它报了那个临时文件名）。判据之间不该靠"谁先跑"相处。
#   ⇒ 拆成三段，任何一段单独都不是合法路径；打印时也只打 basename。
_D = "tests"
_M = "test_zz_temp_probe"
_P = ".py"
DISK_MUTATION = (
    "★★ 磁盘上新增一个测试文件，但 CI 没跑它（并集覆盖必须响）",
    f"{_D}/{_M}{_P}",
    "def test_placeholder():\n    assert True\n",
    "「这些测试文件不在任何 CI job 里」",
)

# ★ 允许"不适用"的白名单：目前是空的 —— 期望三条**全部**能注入上。
#   将来 README 大改结构时，若某条天然不再适用，**加进这里并写明理由**，
#   而不是把断言放松成"至少一条"。
ALLOW_NOT_APPLIED: set[str] = set()


def run_check() -> tuple[int, str]:
    r = subprocess.run([sys.executable, str(CHECK)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=str(ROOT))
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    original = README.read_bytes()
    original_sha = sha(README)
    disk_target = ROOT / DISK_MUTATION[1]
    # ★★ 打印时只写**文件名**，不写带目录的完整路径（2026-10-14 实测踩到两次）：
    #   `scripts/check_file_refs.py` 会扫全仓库文本里的路径引用，而这条临时文件
    #   只在注入瞬间存在 ⇒ 它会把我们**自己的日志/注释**当成"文档里指错了一个文件"而报红。
    #   （真事：一次全量检查里它报了那个临时文件名不存在；连这段注释里举例用的
    #     `tests/` 加一个泛指文件名 也被它当成真路径报了一次。）
    #   ⇒ 判据之间不该靠"谁先跑"来相处；本套件不制造那种假引用。
    disk_base = Path(DISK_MUTATION[1]).name
    # ★ 还原失败不能在 finally 里 return —— Python 会警告「return in finally」，
    #   而且那会**吞掉真正的异常**（如果注入那步抛了，掩盖掉原始故障）。
    #   ⇒ 改成"标记 + 循环后统一处理"。
    restore_broken = False

    code, out = run_check()
    if code != 0:
        print("✗ 未注入变异时检查就报错了 —— 先修好再测：")
        print(out)
        return 2
    print("✓ 基线：未注入变异时报绿")

    undetected: list[str] = []
    not_applied: list[str] = []
    applied = 0

    # ---- README 侧 ----
    text = original.decode("utf-8")
    for desc, old, new, expect in MUTATIONS:
        if old not in text:
            not_applied.append(desc)
            print(f"⚠ 不适用（锚点不存在）：{desc}")
            continue
        README.write_bytes(text.replace(old, new, 1).encode("utf-8"))
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
            README.write_bytes(original)
            if sha(README) != original_sha:
                restore_broken = True

    # ---- 磁盘侧：加一个真实文件 ----
    desc, rel, content, expect = DISK_MUTATION
    if disk_target.exists():
        not_applied.append(desc)
        print(f"⚠ 不适用（{disk_base} 已存在）：{desc}")
    else:
        disk_target.write_text(content, encoding="utf-8", newline="")
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
            disk_target.unlink(missing_ok=True)
            if disk_target.exists():
                restore_broken = True

    # ---- 收尾：先处理"没还原干净"（最严重），再判结论 ----
    if restore_broken:
        print("✗ 还原不干净（README 哈希不一致 或 临时文件没删）—— 立刻停，先手工清")
        return 2

    # ---- 收尾：恢复后必须还绿 ----
    code, out = run_check()
    if code != 0:
        print("✗ 全部恢复后仍报错 —— README 可能没还原干净")
        print(out)
        return 2
    if sha(README) != original_sha:
        print("✗ README 哈希与开始时不一致 —— 本套件改坏了工作区")
        return 2

    expected = len(MUTATIONS) + 1
    print()
    if not_applied:
        print(f"⚠ {len(not_applied)} 条不适用（期望 {expected} 条全部能注入）：")
        for d in not_applied:
            print(f"    - {d}")
    if undetected:
        print(f"✗ {len(undetected)} 条漏检 —— 判据抓不到它声称能抓的东西：")
        for d in undetected:
            print(f"    - {d}")
        return 1

    # ★★ 关键：不许"半灭"。不适用必须白名单化，否则覆盖率静默下降。
    unaccounted = [d for d in not_applied if d not in ALLOW_NOT_APPLIED]
    if unaccounted:
        print(f"✗ {len(unaccounted)} 条变异不适用且不在白名单里 —— "
              f"覆盖率静默下降了（从 {expected} 掉到 {applied}），不许算通过。")
        print("   ⇒ 要么修锚点，要么把它加进 ALLOW_NOT_APPLIED 并写明理由。")
        return 1
    if applied != expected - len(ALLOW_NOT_APPLIED):
        print(f"✗ 注入上 {applied} 条，但期望 {expected - len(ALLOW_NOT_APPLIED)} 条")
        return 1

    print(f"✓ {applied} 条变异全部检出，恢复后仍报绿（README 哈希一致、临时文件已删）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
