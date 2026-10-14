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

import json
import re
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
#
# ★★ 2026-10-13：下面 5 条的原文串全部按**当批数据**更新过一遍。
#   DSH 重跑 --runs 3 之后逐题数字全变了（h3 的直打 total 1248→1268 等），
#   而这些串是上一批的 —— 于是 5 条**同时注入失败**。
#   好在"注入失败必须算失败"这条规矩还在（skipped 直接 return 1），
#   所以它是当场报红而不是静悄悄放过。
#   ⇒ 教训：把原文串硬写死，就等于把"数据换了"变成"判据失效"。
#   下面每条都尽量用**只含要改那一格**的锚点，减少下次换批要改的地方。
#
# ★★★ 2026-10-14 再进一步：**逐题表那四条改成从报告里现取**。
#   上面那句"教训"写归写，逐题表的锚点还是硬写的 —— 结果重跑一次数据，
#   这四条又同时注入失败（h3 的直打 total 1268→1250 等）。
#   **同一个坑踩两次，说明"下次注意"不是办法，得把锚点也变成派生量。**
#   ⇒ `_report_row(qid)` 从 evalset/report-token-cost.md 里现抠那一行，
#     变异只负责改其中一个格子。数据换了锚点自己跟着换，不会再过期。
REPORT_PATH = REPO_ROOT / "evalset" / "report-token-cost.md"


def _report_row(qid: str) -> str:
    """从报告里现抠某个题号那一行（找不到就抛 —— 不许静默给个空串）。"""
    text = REPORT_PATH.read_text(encoding="utf-8")
    m = re.search(rf"^\| {qid} \|.*$", text, re.M)
    if not m:
        raise RuntimeError(f"报告里找不到 {qid} 那一行 —— 表格结构变了")
    return m.group(0)


def _patch_row(qid: str, index: int, new_value: str) -> tuple[str, str]:
    """返回（原文, 改后）：只把该行第 index 个单元格换掉（列定义见报告表头）。"""
    row = _report_row(qid)
    cells = [c for c in row.strip().strip("|").split("|")]
    old_cell = cells[index]
    cells[index] = f" {new_value} "
    return row, "|" + "|".join(cells) + "|"


_H3_D, _H3_D_NEW = _patch_row("h3", 3, "999")
_H7_OUT, _H7_OUT_NEW = _patch_row("h7", 6, "88")
_H4_HIT, _H4_HIT_NEW = _patch_row("h4", 8, "512")
_H1_COST, _H1_COST_NEW = _patch_row("h1", 9, "0.009999")

MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "逐题数字：h3 的直打 total 改成 999",
        _H3_D,
        _H3_D_NEW,
        "h3 直打 total",
    ),
    (
        "★ 题型标签与 n_calls 不自洽：把拒答 h15 写成「应答」",
        "| h15 | 拒答 | 2 |",
        "| h15 | 应答 | 2 |",
        "题型",
    ),
    (
        "agent 侧 output 均值与逐题表对不上（h7 的 output 改成 88）",
        _H7_OUT,
        _H7_OUT_NEW,
        "h7 agent output",
    ),
    (
        "缓存命中那一列改一格（h4 的命中改成 512）",
        _H4_HIT,
        _H4_HIT_NEW,
        "h4 缓存命中",
    ),
    (
        # ★ 这条第一版写成"| 1406 | 79.6% | 2 | 0.003425 |" —— 我把
        #   agent 侧明细表的列结构记混了，报告表里没有"命中率"那一列
        #   （命中率只出现在正文），于是注入失败、被"跳过"，
        #   而跳过时退出码仍是 0 ⇒ 【看起来测过了，其实这条方向根本没测】。
        #   这与"没被变异测试盯着的判据等于没有判据"同源。
        "成本列改一格（h1 的成本改成 0.009999）",
        _H1_COST,
        _H1_COST_NEW,
        "h1 成本",
    ),
    # ------------------------------------------------------------------
    # ★★ 2026-10-13 补：下面两组是【报告侧】与【数据侧】两个方向。
    #
    # 为什么分成两个方向、且数据侧要单独造坏 JSON：
    #   判据里有两类断言 —— 「报告写的数 vs JSON」与
    #   「纯结构不变式」（如 含工具倍率 必须 > 仅自身倍率、
    #   ratio_all 必须 == 账本重算值）。后者**不看报告**，
    #   所以只改报告永远测不到它，必须直接改 JSON 才测得到。
    #   实测教训：只测报告侧 ⇒ 数据侧那几条"看起来在、其实从没被触发过"。
    # ------------------------------------------------------------------
    ("★ 表格第 4 格被换回 total（表头与内容不符）",
     "| 硬短路拒答（没调生成） | 7 | 624 | 151 | 776 |",
     "| 硬短路拒答（没调生成） | 7 | 624 | 776 | 776 |",
     "拒答 output 均值"),
    ("★ 表格新增的 total 那一格算错（776→777）",
     "| 硬短路拒答（没调生成） | 7 | 624 | 151 | 776 |",
     "| 硬短路拒答（没调生成） | 7 | 624 | 151 | 777 |",
     "拒答 total 均值"),
    ("★ 〈①〉那段 output 均值错（236/39 → 242/47，上一批残留）",
     "output 均值 236 对 197，**只多 39 token**",
     "output 均值 242 对 197，**只多 47 token**",
     "①里 agent output 均值"),
    ("★ 〈③〉那段 output 占比错（12.4% → 12.7%）",
     "output 只占 12.4%（236/1900）",
     "output 只占 12.7%（242/1906）",
     "③里 output 占比"),
    ("★ input 倍率错（2.22 → 2.18）",
     "input 多 122%（2.22 倍）",
     "input 多 118%（2.18 倍）",
     "①里 input"),
    ("★ 整张三遍表被删",
     "> | 1 | 966 | 1906 | 972 | 1.97× | **2.98×** |",
     "> （三遍表已删除）",
     "runs"),
    ("★ 三遍表少写一遍（末尾那遍最容易丢）",
     "> | 3 | 946 | 1900 | 965 | 2.01× | **3.03×** |",
     "> |（第 3 遍未记录）",
     "runs"),
    ("★ 三遍表某一遍的直打值错",
     "> | 2 | 952 | 1894 | 988 | 1.99× | **3.03×** |",
     "> | 2 | 999 | 1894 | 988 | 1.99× | **3.03×** |",
     "第 2 遍"),
    # ★★ 2026-10-14 重写：这条原来注入的是「第 2 遍出现过…2 次没报上账」那句，
    #   而**本批数据 tool_usage_missing 合计是 0**，那句已经被报告删掉了
    #   （数据好了就不能留旧数，检查器两个方向都判）⇒ 原变异注入失败。
    #   现在改成打**另一个方向**：JSON 干净（0 次），报告却声称有缺口。
    #   这正是"低估被凭空造出来"的那种错，和原来那条互补。
    ("★ 「没报上账」被凭空写进报告（JSON 里是 0 次）",
     "> - 三遍**都没有**「没报上账」的调用",
     "> - 第 2 遍出现过「工具侧有 2 次没报上账」，本批三遍**都没有**「没报上账」的调用",
     "tool_usage_missing"),
]


# ★★ 数据侧变异：直接改 _token_cost.json 的 runs 块。
#   这些断言**不看报告**，只测报告永远测不到。
#   (说明, 改动函数, 期望诊断包含什么)
DATA_MUTATIONS: list[tuple[str, object, str]] = [
    (
        "★★ 口径缺失：含工具倍率被降到 = 仅自身倍率（工具侧那本账没并进来）",
        lambda d: d["runs"][1].update(ratio_all=d["runs"][1]["ratio_own"]),
        "口径缺失",
    ),
    (
        "★★ 派生量与账本不一致：tool_mean 归零，而 ratio_all 没跟着改",
        lambda d: d["runs"][1].update(tool_mean=0),
        "派生量与账本不一致",
    ),
    (
        "★★★ 派生量自己算错：ratio_all 被写成 = ratio_own（两本账合并了）",
        lambda d: d["runs"][0].update(ratio_all=d["runs"][0]["ratio_own"]),
        "派生量与账本不一致",
    ),
    (
        "★★ ratio_own 单独写错",
        lambda d: d["runs"][2].update(ratio_own=1.5),
        "派生量与账本不一致",
    ),
    (
        # ★★ 2026-10-14 重写：这条原来是把某遍的 tool_usage_missing 归零。
        #   但**本批数据本来就是 0**（工具侧账目完整）⇒ 归零 == 原值，注入等于没注入，
        #   当场报"漏检"。那不是判据退步，是**数据变了、这条变异的靶子没了**。
        #   ⇒ 换成"反方向"：JSON 里凭空多报 3 次缺口，而报告写的是 0 ⇒ 必须报红。
        #   （两个方向都要有：一个抓"低估被抹掉"，一个抓"缺口被造出来"。）
        "★ tool_usage_missing 在 JSON 里被凭空多报（报告写 0）",
        lambda d: d["runs"][1].update(tool_usage_missing=3),
        "tool_usage_missing",
    ),
    (
        "★ tool_usage_missing 多报（三遍都改成 5）",
        lambda d: [r.update(tool_usage_missing=5) for r in d["runs"]],
        "tool_usage_missing",
    ),
    (
        "runs 整块从 JSON 里删掉（单遍数据却画了三遍表）",
        lambda d: d.pop("runs"),
        "JSON 里没有 runs",
    ),
]


DATA_FILE = REPO_ROOT / "agent" / "_token_cost.json"


def run_check() -> tuple[int, str]:
    r = subprocess.run(
        [PY, "-W", "ignore", "-m", "scripts.check_token_report"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(REPO_ROOT),
    )
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def first_detail(out: str) -> str:
    """取第一行以两个空格开头的明细（不是以 ✗ 开头的总数行）。"""
    return next(
        (ln.strip() for ln in out.splitlines()
         if ln.startswith("  ") and ln.strip()),
        "",
    )


def main() -> int:
    if not REPORT.exists():
        print(f"✗ 找不到 {REPORT.relative_to(REPO_ROOT)}")
        return 2
    if not DATA_FILE.exists():
        print(f"✗ 找不到 {DATA_FILE.relative_to(REPO_ROOT)}")
        return 2
    original = REPORT.read_text(encoding="utf-8")
    data_original = DATA_FILE.read_text(encoding="utf-8")

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

    # ---- 1b. 数据侧注入（只改 _token_cost.json，不碰报告）----
    # ★ 为什么必须单独一轮：判据里有一批断言【不看报告】——
    #   「含工具倍率 > 仅自身倍率」「ratio_all == 账本重算值」
    #   「runs 整块没了但报告还画着表」。
    #   只改报告永远触发不到它们 ⇒ 只测报告侧 = 这批断言从没被验证过。
    data_missed: list[str] = []
    data_misdiagnosed: list[str] = []
    for desc, mutate, expect in DATA_MUTATIONS:
        try:
            d = json.loads(data_original)
            mutate(d)
            DATA_FILE.write_text(
                json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8", newline="")
            code, out = run_check()
            if code == 0:
                data_missed.append(desc)
                print(f"✗ 漏检：{desc}")
                continue
            body = "\n".join(ln.strip() for ln in out.splitlines())
            if expect not in body:
                data_misdiagnosed.append(f"{desc}（报的是 {first_detail(out)!r}，不是 {expect!r}）")
                print(f"✗ 检出但诊断不对：{desc}")
                print(f"    期望提到：{expect}")
                print(f"    实际报的是：{first_detail(out)}")
            else:
                rel = next(ln.strip() for ln in out.splitlines()
                           if ln.startswith("  ") and expect in ln)
                print(f"✓ 检出：{desc}")
                print(f"    {rel}")
        finally:
            DATA_FILE.write_text(data_original, encoding="utf-8", newline="")

    # ---- 2. 还原后必须还绿 ----
    code, out = run_check()
    if code != 0:
        print("✗ 全部恢复后仍报错 —— 报告可能没还原干净")
        print(out)
        return 2

    print()
    if missed or misdiagnosed or data_missed or data_misdiagnosed:
        print(f"✗ 漏检 {len(missed) + len(data_missed)} 条、"
              f"诊断不对 {len(misdiagnosed) + len(data_misdiagnosed)} 条：")
        for m in missed + data_missed:
            print(f"  漏检：{m}")
        for m in misdiagnosed + data_misdiagnosed:
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
    print(f"✓ 报告侧 {len(MUTATIONS)} 条 + 数据侧 {len(DATA_MUTATIONS)} 条变异"
          f"全部检出，且诊断都指对了原因")
    return 0


if __name__ == "__main__":
    sys.exit(main())