"""token 成本报告对账器的变异测试：注入"报告数字与实测数据不符"的报告，验证判据会响。

为什么需要这个
----------------
`check_token_report.py` 盯的是「报告里的数字 ↔ _token_cost.json 逐格一致」。
★ 而这一版报告写出来的时候，我手工核对了 27 项，**在正文里抓到自己 3 处算错**：
    · 缓存命中率 67.1% → 实际 66.5%
    · output 差值 46→ 实际 47
    · "52 次 LLM 调用" → 实际 53
  三处都是"正文里手写算式"的必然产物。而"写完自己读一遍"这种核对方式
  恰恰最容易漏—— 它读的是自己刚写的句子，不是数据。
  ⇒ 所以必须有一条自动判据盯着这些数，而不是靠下次记得手算。

★ 第二层理由：**每条判据都可能"因为别的原因"而响**。
  变异测试要证明的是"它响，是因为我注入的那个错"，
  所以每条变异都要看它报出来的**第一行错误说的是不是那件事**。

用法：
    python scripts/mutate_check_token.py

判据：
  - 每条变异都必须被检出，且报出的第一行就是该变异注入的那件事
  - 不注入时必须全绿（否则满屏假阳性，迟早被当噪音关掉）
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# ★ 与两个兄弟脚本保持一致：✓✗ 在 cp936 里编不出来，
#   stdout 一旦被重定向就会崩，且崩得【像"变异没检出"】。
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parent.parent
CHECK = REPO_ROOT / "scripts" / "check_token_report.py"
REPORT = REPO_ROOT / "evalset" / "report-token-cost.md"
PY = sys.executable

# (说明, 报告里要替换的原文, 替换成什么, 期望报出来的第一行包含什么)
MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "逐题数字：h3 的直打 total 改成 999",
        "| h3 | 应答 | 3 | 1248 |",
        "| h3 | 应答 | 3 | 999 |",
        "h3 直打 total",
    ),
    (
        "★ 题型标签与 n_calls 不自洽：把拒答 h15 写成「应答」",
        "| h15 | 拒答 | **2** |",
        "| h15 | 应答 | **2** |",
        "题型",
    ),
    (
        "agent 侧 output 均值与逐题表对不上（h7 的 263→88）",
        "| h7 | 应答 | 3 | 859 | 2 | 1714 | 263 |",
        "| h7 | 应答 | 3 | 859 | 2 | 1714 | 88 |",
        "h7 agent output",
    ),
    (
        "缓存命中那一列改一格（h4 的 1024→512）",
        "| h4 | 应答 | 3 | 1128 | 2 | 1794 | 267 | 2061 | 1024 |",
        "| h4 | 应答 | 3 | 1128 | 2 | 1794 | 267 | 2061 | 512 |",
        "h4 缓存命中",
    ),
    (
        # ★ 这条第一版写成"| 1406 | 79.6% | 2 | 0.003425 |" —— 我把
        #   agent 侧明细表的列结构记混了，报告表里没有"命中率"那一列
        #   （命中率只出现在正文），于是注入失败、被"跳过"，
        #   而跳过时退出码仍是 0 ⇒ 【看起来测过了，其实这条方向根本没测】。
        #   这与"没被变异测试盯着的判据等于没有判据"同源。
        "成本列改一格（h1 的 0.003425→0.009999）",
        "| 2069 | 1406 | 0.003425 |",
        "| 2069 | 1406 | 0.009999 |",
        "h1 成本",
    ),
]


def run_check() -> tuple[int, str]:
    r = subprocess.run(
        [PY, "-m", "scripts.check_token_report"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(REPO_ROOT),
    )
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def main() -> int:
    if not REPORT.exists():
        print(f"✗ 找不到 {REPORT.relative_to(REPO_ROOT)}")
        return 2
    original = REPORT.read_text(encoding="utf-8")

    # ---- 0. 基线必须绿 ----
    code, out = run_check()
    if code != 0:
        print("✗ 不注入任何变异时判据就报错 —— 先修报告本身")
        print(out)
        return 2
    print(f"✓ 基线：{out.strip()}")

    # ---- 1. 逐条注入 ----
    missed: list[str] = []
    misdiagnosed: list[str] = []
    skipped: list[str] = []
    for desc, old, new, expect in MUTATIONS:
        if old not in original:
            skipped.append(f"{desc}（报告里找不到原文 {old!r}）")
            print(f"? 跳过：{desc}")
            continue
        REPORT.write_text(original.replace(old, new), encoding="utf-8", newline="")
        try:
            code, out = run_check()
            if code == 0:
                missed.append(desc)
                print(f"✗ 漏检：{desc}")
                continue
            first = next(
                # ★ 取【第一行以两个空格开头的明细】，不是第一行以 ✗ 开头的。
                #   判据自己会先打一行「✗ 5 处对不上：」，那是总数不是原因 ——
                #   取它的话每条变异的诊断都长得一样，等于没验诊断。
                (ln.strip() for ln in out.splitlines()
                 if ln.startswith("  ") and ln.strip()),
                "",
            )
            body = "\n".join(ln.strip() for ln in out.splitlines())
            if expect not in body:
                misdiagnosed.append(f"{desc}（报的是 {first!r}，不是 {expect!r}）")
                print(f"✗ 检出但诊断不对：{desc}")
                print(f"    期望提到：{expect}")
                print(f"    实际报的是：{first}")
            else:
                print(f"✓ 检出：{desc}")
                print(f"    {first}")
        finally:
            REPORT.write_text(original, encoding="utf-8", newline="")

    # ---- 2. 还原后必须还绿 ----
    code, out = run_check()
    if code != 0:
        print("✗ 全部恢复后仍报错 —— 报告可能没还原干净")
        print(out)
        return 2

    print()
    if missed or misdiagnosed:
        print(f"✗ {len(missed)} 条漏检、{len(misdiagnosed)} 条诊断不对：")
        for m in missed:
            print(f"  漏检：{m}")
        for m in misdiagnosed:
            print(f"  诊断：{m}")
        return 1
    if skipped:
        # ★ 注入失败必须【算失败】。
        #   原来 skipped 只打印一行警告、退出码仍是 0 ——
        #   于是"这条方向没测到"看起来跟"全部测过"长得一样。
        #   实测踩过：成本列那条把上一张表的列结构记错了，
        #   注入失败被跳过，而 CI/pre-push 全绿 ⇒ 这条判据实际上从没被验证过。
        print(f"✗ {len(skipped)} 条变异注入失败（退出码非 0）：这些方向没测到")
        for s in skipped:
            print(f"  - {s}")
        print("  ⇒ 修掉原文串（多半是报告表格结构变了），重跑。")
        return 1
    print(f"✓ {len(MUTATIONS)} 条变异全部检出，且诊断都指对了原因")
    return 0


if __name__ == "__main__":
    sys.exit(main())