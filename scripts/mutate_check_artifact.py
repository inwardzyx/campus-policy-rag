"""产物合理性检查的变异测试：注入"像坏环境跑出来的"产物，验证脚本会响。

为什么需要这个
----------------
`check_artifact_sanity.py` 的存在理由就是"2026-10-11 那次坏环境产物被提交"。
★ 所以它的第一条变异必须用**那次事故的真实数据**（Recall 0/15、两个极值都是 0.4240），
  而不是我自己编的数 —— 编的数可能恰好不符合当时真正的失效机制。

★ 另一个更一般的理由：这个脚本里的判据全是"我推导出来的下界"，
  而我推导的时候**没想过它什么时候不该报**。负向变异（应当保持绿的那些）
  是唯一能测出"假阳性"的方向，而假阳性会把检查变成噪音。

用法：
    python scripts/mutate_check_artifact.py

判据：
  - 每条变异都必须被检出（漏一个 = 这个判据抓不到它声称能抓的东西）
  - 不注入时必须全绿（否则满屏假阳性，迟早被当噪音关掉）
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# ★ 与两个兄弟（mutate_check.py:40-42 / mutate_check_numbers.py:35）保持一致：
#   本脚本通篇用 print("✓ …") / print("✗ …") 报结果，而 ✓✗ 在 cp936 里**编不出来**
#   —— 一旦 stdout 被重定向或被别的进程捕获（_tasks/dispatch.sh 派活时就是写文件），
#   它会一行都打不出来就崩，**看起来像"变异没检出"，其实是打印失败**。
#   （实锤：2026-10-11 同款病让 pre-push 报出「产物不合理」这句假话。）
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parent.parent
CHECK = REPO_ROOT / "scripts" / "check_artifact_sanity.py"
PY = sys.executable

TARGET = "evalset/report-vector-heldout.md"

# ★★ 删掉了原来那个 ACCIDENT 常量（2026-10-11 盲审后）。
#   它是**死代码**：全文只在自己定义那一行出现，从没被任何变异引用 ——
#   而它偏偏叫"事故原始数据"，读代码的人会以为变异用的就是它。
#   事实是第1 条变异硬编码的是 heldout 那份的 `0/44`（数字取自它自己的产物），
#   不是 2026-10-11 那次事故的 `0/15` + 两极值 0.4240（那份是 report-vector.md）。
#   ⇒ 教训：**"看起来像证据的注释/常量"比没有更坏** ——
#     它让人以为那条变异是"照着事故写的"，其实不是。
#     真要保留事故原貌，就让变异真的用它（TARGET 改成 report-vector.md），
#     或者就别叫它"事故"。
#
# (说明, 要替换的原文, 替换成什么, 期望被哪条判据抓到)
MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "★ 事故复现：Recall@5 全 0",
        "- 应答题数：44　Recall@5：35/44",
        "- 应答题数：44　Recall@5：0/44",
        "判据1（Recall 不能为 0）",
    ),
    (
        # ★★ 这条原来是【不纯的】变异（2026-10-11 盲审实测发现）：
        #   旧版只把「应答题数 44→45」，于是**判据 6 也顺带响了**
        #   （漏答率分母 44 ≠ 应答题数 45）⇒ 关掉判据 3 它照样"检出"。
        #   ⇒ 它证明不了判据 3 有效 —— **检出 ≠ 证明**。
        #   纯化：把漏答率的分母**一起**改成 45，判据 6 就不会误响，
        #   剩下的唯一触发点才是判据 3。
        #   （与"变异与规则同源"同族：检出本身不携带信息量。）
        "分母与应答题数不一致（★已纯化：同步改漏答率分母以隔离判据 6）",
        "- 应答题数：44　Recall@5：35/44\n- 漏答率：2/44",
        "- 应答题数：45　Recall@5：35/44\n- 漏答率：2/45",
        "判据3（分母 = 题目集合大小）",
    ),
    (
        "分子超过分母（字段读串位）",
        "- 应答题数：44　Recall@5：35/44",
        "- 应答题数：44　Recall@5：99/44",
        "判据2（分子 ≤ 分母）",
    ),
    (
        "拒答准确率分母 ≠ 拒答题数",
        "- 拒答题数：61　拒答准确率：9/61",
        "- 拒答题数：60　拒答准确率：9/61",
        "判据3",
    ),
    (
        "漏答分母 ≠ 应答题数",
        "- 漏答率：2/44",
        "- 漏答率：2/45",
        "判据6（漏答分母 = 应答题数）",
    ),
]

# ★★★ 成对变异：两条判据要**同时**注入才构成事故现场。
#   判据 5 是"两个不同题集的极值相等" —— 单独改最低分不会让它触发
#   （另一个极值没变，仍然不等）。我第一版把它当单条变异写，
#   脚本报"漏检"，**其实是我的变异设计错了**，不是检查有洞。
#   ⇒ 这类判据必须成对注入。这本身是个元教训：
#     **"漏检"有两种可能 —— 检查有洞，或变异设计错了。判之前先分清。**
PAIR_MUTATIONS: list[tuple[str, tuple[tuple[str, str], ...], str]] = [
    (
        "★ 事故复现（成对）：应答题最低分与拒答题最高分撞成同一个常数",
        (
            ("应答题最低向量分 0.5224", "应答题最低向量分 0.4240"),
            ("拒答题最高向量分 0.782", "拒答题最高向量分 0.4240"),
        ),
        "判据5（两个极值不该相等）—— 这是 2026-10-11 事故最强的信号",
    ),
]


# ★★ 新增产物类型的变异（2026-10-12 push 被拦时加的）。
#
#   为什么必须单独一组：上面的变异全部打在 report-vector-heldout.md 上，
#   而拒答回归报告 / token 成本报告走的是**另外两套判据**。
#   如果不给它们做变异，新判据就是"从没被验证过的绿"——
#   而本项目的规矩是：**没被变异测试盯着的判据等于没有判据**。
#
#   结构与上面一致：(说明, 目标文件, 要替换的原文, 替换成什么, 期望判据)
PER_ARTIFACT_MUTATIONS: list[tuple[str, str, str, str, str]] = [
    (
        "★ 拒答报告：硬答率列的值与硬答题数个数对不上（字段读串位）",
        "evalset/report-agent-refusal-regression.md",
        "| **硬答率** | **10.5%~15.8%（3 次：15.8% / 12.5% / 10.5%）**",
        "| **硬答率** | **10.5%~15.8%（2 次：15.8% / 12.5%）**",
        "拒答判据①（两行不是同一组数）",
    ),
    (
        "★ 拒答报告：区间端点与括号里列的值对不上",
        "evalset/report-agent-refusal-regression.md",
        "| **硬答率** | **10.5%~15.8%（3 次：15.8% / 12.5% / 10.5%）**",
        "| **硬答率** | **10.5%~45.8%（3 次：15.8% / 12.5% / 10.5%）**",
        "拒答判据①（区间对不上）",
    ),
    (
        "★ 拒答报告：信号解析失败数超过总题数（分子大于分母）",
        "evalset/report-agent-refusal-regression.md",
        "| 信号解析失败 | 9 |",
        "| 信号解析失败 | 9999 |",
        "拒答判据③（分子不可能大于分母）",
    ),
    (
        "★★ token 报告：missing_usage 不是 0（有数字是补0/估算的）",
        "evalset/report-token-cost.md",
        "`missing_usage = 0`",
        "`missing_usage = 7`",
        "token 判据（补0的均值比没有均值更坏）",
    ),
    (
        "★ token 报告：truncated 不是 0（这批数据有截断）",
        "evalset/report-token-cost.md",
        "`truncated = 0`",
        "`truncated = 3`",
        "token 判据（截断均值不可信）",
    ),
    # ---- 只检索端点 A/B 对照（2026-10-14 加）----
    #   ★ 这一组的变异**必须能证明"判据抓得到"**，否则新判据就是"从没被验证过的绿"。
    #     尤其第一条：这条 A/B 的全部价值就是"答案没人读 ⇒ 省下来"，而
    #     JSON 里那个 `search_total_tokens: 0` 是最容易被下一次改动悄悄变成非 0 的数。
    #   ⚠️ 有几条是**不纯的**（一次改动同时踩中两条判据）—— 我把它写进说明里，
    #     而不是假装它证明了单独某一条：本仓库的规矩是"检出 ≠ 证明"。
    (
        "★ A/B：结论里写着 /search 恒 0 token，但 JSON 里那个汇总数被改成非 0"
        "（纯化不了：它同时踩中「汇总必须能从明细重算」那条）",
        "evalset/report-search-ab.json",
        '"search_total_tokens": 0,',
        '"search_total_tokens": 7,',
        "A/B 判据④（这条路上恒 0 次调用）或判据②（汇总要能重算）",
    ),
    (
        "★ A/B：md 里的「/search 合计 token」与 json 对不上（有人手改了 md）",
        "evalset/report-search-ab.md",
        "- /search 合计 token：0",
        "- /search 合计 token：4",
        "A/B 判据⑦（md 与 json 同源）",
    ),
    (
        "★ A/B：md 声称「渲染文本逐字节一致」但分母不是这次跑的题数",
        "evalset/report-search-ab.md",
        "- 渲染文本逐字节一致：20/20",
        "- 渲染文本逐字节一致：20/19",
        "A/B 判据⑦（一致性分母 = 配对题数）",
    ),
    (
        "★ A/B：题数被截断（n_pairs 与逐题明细对不上）",
        "evalset/report-search-ab.json",
        '"n_pairs": 20,',
        '"n_pairs": 3,',
        "A/B 判据①（分母自洽 / 题集没被截断）",
    ),
]


# ★★★ 负向变异：这些**必须保持绿**。它们是测假阳性的唯一方向。
# 实测踩过的假阳性：「漏答率与 Recall 反推值不同」—— 我一度想把它报成错，
#     但那是【真实的口径差】（漏答算 knowledge_hit，Recall 算 gold 命中），
#     报出来就是假阳性。现在它只提示。
NEGATIVE_CASES: list[tuple[str, str, str]] = [
    (
        "漏答率与 Recall 反推值不同（已知口径差，不是 bug）",
        "- 漏答率：2/44",
        "- 漏答率：9/44",
    ),
]


# ★★★ 判据 0（清单反查）的变异：改的是**检查器自己的清单**，不是产物。
#   为什么单独一类：前七条全都在动产物，只有这一条动的是"检查器管不管这个文件"。
#   如果不测它，就等于默认"清单没漏"—— 而上一版清单**真的漏了**
#   （report-rerank-main-norw.md 从未被检查），且那个洞是在我写完检查器之后才被发现的。
#
#   ⚠️ 这条变异**不注入任何坏产物**：如果只有"清单漏登记 + 产物本身健康"，
#     期望的行为是**必须报错** —— 因为"没人检查它"本身就是缺陷，
#     不管它现在看起来干不干净。
LIST_MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "★ 清单漏登记：把 report-rerank-main-norw.md 从 ARTIFACTS 里去掉"
        "（产物本身健康，但从此不再被任何检查碰过）",
        '"evalset/report-rerank-main-norw.md",\n    ',
        "",
        "判据0（清单反查）",
    ),
]


# ★★ 诱饵行变异（盲审 DSH 实测发现的洞，2026-10-11）：
#   旧版 `_extract` 用 `re.search` 只取**第一个**匹配。
#   在真数据前面插一行长得一样的、真数据改成事故值⇒ 旧版 exit 0 全绿。
#   这条变异必须**追加一行**而不是替换，故单列（PAIR_MUTATIONS 是"替换"语义）。
DECOY_MUTATIONS: list[tuple[str, str, str]] = [
    (
        "★ 诱饵行：在真数据前插一行相同格式的汇总行，真数据改成 Recall 0/44",
        # ⚠️ 这两条串必须取自 TARGET（heldout 那份），不是 report-vector.md
        "- 应答题数：44\u3000Recall@5：35/44",
        "> 应答题数：44\u3000Recall@5：35/44\n- 应答题数：44\u3000Recall@5：0/44",
    ),
]



def run_check() -> tuple[int, str]:
    r = subprocess.run(
        [PY, str(CHECK)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(REPO_ROOT),
    )
    return r.returncode, (r.stdout or "") + (r.stderr or "")


# ==================================================================
# ★ 工具返回体积裁剪（evalset/report-payload-trim.json）的变异
# ==================================================================
# 这一族的判据与前七条不同：它盯的是"模型可见的文本有没有被悄悄改动"，
# 所以**必须分两轮打**（本仓 2026-10-13 记过这条）：
#   · 文档侧（md）—— 改汇总行
#   · 数据侧（json）—— 直接改逐次明细
# 而这一族的判据**不从 md 抽数**，只读 json ⇒ 只打 md 是打不到它的。
# ★ 每条用**不同的形状**（同源变异不携带信息量）：
#   ① 改 after（把删掉的字段塞回去）→ 判据"after 不许有账目字段"
#   ② 改 before（把字段删掉）→ 判据"before 必须真有这些字段，否则是空转"
#   ③ 改 body 成空串 → 判据"正文非空"（防两个空串比出假一致）
#   ④ 改汇总字数 → 判据"汇总能从明细重算"
#   ⑤ 删 sig_expected_after → 判据"必须证明 after 来自代码"
JSON_TARGET = "evalset/report-payload-trim.json"

JSON_MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "① 把账目字段塞回 after（模型上下文里又多了钱数）",
        "n_sources=3 llm_calls=0",
        "n_sources=3 tokens_total=0 tokens_calls=0",
        "判据「after 不许出现账目字段」",
    ),
    (
        "② 把 before 里的账目字段抹掉（这次比较变成空转）",
        "tokens_total=0 tokens_calls=0 tokens_input=0 tokens_output=0 "
        "tokens_cache_hit=0",
        "tokens_total=0",
        "判据「before 必须真有这些字段」",
    ),
    (
        "③ 改明细里的 before 文本长度来源（汇总与明细分家）",
        '"signal_chars_before": 2743',
        '"signal_chars_before": 1',
        "判据「汇总能从明细重算」",
    ),
    (
        "④ 改汇总字数（不重算就发现不了）",
        '"signal_chars_saved": 1447',
        '"signal_chars_saved": 9999',
        "判据「汇总能从明细重算」",
    ),
    (
        "⑤ 删掉 sig_expected_after（无法证明 after 是代码吐出来的）",
        '"sig_expected_after": "\\n\\n[检索信号]',
        '"sig_expected_after_removed": "\\n\\n[检索信号]',
        "判据「必须存在代码派生值」",
    ),
]


def run_json_mutations() -> tuple[list[str], list[str], int]:
    """数据侧变异：直接改 JSON（md 侧改不到这些判据）。返回（漏检, 未注入, 通过数）。"""
    path = REPO_ROOT / JSON_TARGET
    if not path.exists():
        print(f"? 跳过 JSON 变异：找不到 {JSON_TARGET}")
        return [], [f"（整个 {JSON_TARGET} 不在）"], 0
    original = path.read_text(encoding="utf-8")
    undetected: list[str] = []
    not_applied: list[str] = []
    applied = 0
    for desc, old, new, expect in JSON_MUTATIONS:
        if old not in original:
            not_applied.append(f"{desc}（原文不存在：{old[:40]!r}）")
            print(f"? 跳过：{desc}")
            continue
        path.write_text(original.replace(old, new), encoding="utf-8", newline="")
        try:
            code, out = run_check()
            if code == 0:
                undetected.append(desc)
                print(f"✗ 漏检：{desc}（期望由{expect} 抓到）")
            else:
                first = next(
                    (ln.strip() for ln in out.splitlines() if ln.strip().startswith("✗")),
                    "(没打印错误行)",
                )
                print(f"✓ 检出：{desc}")
                print(f"    {first}")
                applied += 1
        finally:
            _restore(path, original)
    return undetected, not_applied, applied


def _restore(path: Path, original: str) -> None:
    path.write_text(original, encoding="utf-8", newline="")


def main() -> int:
    target = REPO_ROOT / TARGET
    if not target.exists():
        print(f"✗ 找不到 {TARGET}")
        return 2
    original = target.read_text(encoding="utf-8")

    # ---- 0. 基线必须绿 ----
    code, out = run_check()
    if code != 0:
        print("✗ 未注入变异时检查就报错了 —— 先修好再测：")
        print(out)
        return 2
    print("✓ 基线：未注入变异时报绿")

    undetected: list[str] = []
    not_applied: list[str] = []
    applied = 0

    # ---- 正向变异：每一条都必须被检出 ----
    for desc, old, new, expect in MUTATIONS:
        if old not in original:
            not_applied.append(f"{desc}（原文不存在：{old!r}）")
            print(f"? 跳过：{desc}")
            continue
        target.write_text(original.replace(old, new), encoding="utf-8", newline="")
        try:
            code, out = run_check()
            if code == 0:
                undetected.append(desc)
                print(f"✗ 漏检：{desc}（期望由{expect} 抓到）")
            else:
                first = next(
                    (ln.strip() for ln in out.splitlines() if ln.strip().startswith("✗")),
                    "(没打印错误行)",
                )
                print(f"✓ 检出：{desc}")
                print(f"    {first}")
                applied += 1
        finally:
            _restore(target, original)

    # ---- 成对变异：两处一起改，才构成事故现场 ----
    for desc, edits, expect in PAIR_MUTATIONS:
        missing = [old for old, _ in edits if old not in original]
        if missing:
            not_applied.append(f"{desc}（原文不存在：{missing}）")
            print(f"? 跳过：{desc}")
            continue
        mutated = original
        for old, new in edits:
            mutated = mutated.replace(old, new)
        target.write_text(mutated, encoding="utf-8", newline="")
        try:
            code, out = run_check()
            if code == 0:
                undetected.append(desc)
                print(f"✗ 漏检：{desc}（期望由{expect} 抓到）")
            else:
                first = next(
                    (ln.strip() for ln in out.splitlines() if ln.strip().startswith("✗")),
                    "(没打印错误行)",
                )
                print(f"✓ 检出：{desc}")
                print(f"    {first}")
                applied += 1
        finally:
            _restore(target, original)

    # ---- 负向变异：必须保持绿 ----
    false_positives: list[str] = []
    for desc, old, new in NEGATIVE_CASES:
        if old not in original:
            not_applied.append(f"{desc}（原文不存在）")
            continue
        target.write_text(original.replace(old, new), encoding="utf-8", newline="")
        try:
            code, out = run_check()
            if code != 0:
                false_positives.append(desc)
                print(f"✗ 假阳性：{desc} —— {desc}**应当保持绿**，却报错了")
                first = next(
                    (ln.strip() for ln in out.splitlines() if ln.strip().startswith("✗")),
                    "",
                )
                print(f"    {first}")
            else:
                print(f"✓ 保持绿（负向用例）：{desc}")
                applied += 1
        finally:
            _restore(target, original)

    # ---- 诱饵变异：插一行假的汇总行，让真数据被"藏在后面" ----
    #   这条正是 DSH 盲审实测出来的洞（旧的 re.search 只认第一处匹配）
    for desc, old, new in DECOY_MUTATIONS:
        if old not in original:
            not_applied.append(f"{desc}（原文不存在：{old!r}）")
            print(f"? 跳过：{desc}")
            continue
        target.write_text(original.replace(old, new), encoding="utf-8", newline="")
        try:
            code, out = run_check()
            if code == 0:
                undetected.append(desc)
                print(f"✗ 漏检：{desc}")
            else:
                first = next(
                    (ln.strip() for ln in out.splitlines() if ln.strip().startswith("✗")),
                    "(没打印错误行)",
                )
                print(f"✓ 检出：{desc}")
                print(f"    {first}")
                applied += 1
        finally:
            _restore(target, original)

    # ---- 清单变异：改的是检查器自己的 ARTIFACTS ----
    check_src = CHECK.read_text(encoding="utf-8")
    for desc, old, new, expect in LIST_MUTATIONS:
        if old not in check_src:
            not_applied.append(f"{desc}（检查器源码里找不到：{old!r}）")
            print(f"? 跳过：{desc}")
            continue
        CHECK.write_text(check_src.replace(old, new, 1), encoding="utf-8", newline="")
        try:
            code, out = run_check()
            if code == 0:
                undetected.append(desc)
                print(f"✗ 漏检：{desc}（期望由{expect} 抓到）")
            else:
                first = next(
                    (ln.strip() for ln in out.splitlines() if ln.strip().startswith("✗")),
                    "(没打印错误行)",
                )
                print(f"✓ 检出：{desc}")
                print(f"    {first}")
                applied += 1
        finally:
            CHECK.write_text(check_src, encoding="utf-8", newline="")

    # ---- 分类产物变异：每份产物打自己的那套判据 ----
    for desc, rel, old, new, expect in PER_ARTIFACT_MUTATIONS:
        p = REPO_ROOT / rel
        if not p.exists():
            not_applied.append(f"{desc}（找不到 {rel}）")
            continue
        orig = p.read_text(encoding="utf-8")
        if old not in orig:
            not_applied.append(f"{desc}（原文不存在：{old!r}）")
            print(f"? 跳过：{desc}")
            continue
        p.write_text(orig.replace(old, new), encoding="utf-8", newline="")
        try:
            code, out = run_check()
            if code == 0:
                undetected.append(desc)
                print(f"✗ 漏检：{desc}（期望由{expect} 抓到）")
            else:
                first = next(
                    (ln.strip() for ln in out.splitlines() if ln.strip().startswith("✗")),
                    "(没打印错误行)",
                )
                print(f"✓ 检出：{desc}")
                print(f"    {first}")
                applied += 1
        finally:
            p.write_text(orig, encoding="utf-8", newline="")

    # ---- 数据侧变异：改 JSON（md 侧改不到这些判据，见 JSON_MUTATIONS 的说明）----
    j_undetected, j_not_applied, j_applied = run_json_mutations()
    undetected += j_undetected
    not_applied += j_not_applied
    applied += j_applied

    # ---- 还原后必须还绿 ----
    code, out = run_check()
    if code != 0:
        print("✗ 全部恢复后仍报错 —— 产物文件可能没还原干净")
        print(out)
        return 2

    print()
    if not_applied:
        print(f"⚠ {len(not_applied)} 条变异没注入成功，这些方向没测到：")
        for d in not_applied:
            print(f"    - {d}")
    if undetected:
        print(f"✗ {len(undetected)} 条正向变异漏检 —— 这些判据抓不到它们声称能抓的东西：")
        for d in undetected:
            print(f"    - {d}")
    if false_positives:
        print(f"✗ {len(false_positives)} 条负向变异被误报 —— 假阳性会把检查变成噪音：")
        for d in false_positives:
            print(f"    - {d}")
    if undetected or false_positives:
        return 1
    if not_applied:
        return 2
    print(f"✓ {applied} 条变异全部符合预期（正向全检出 + 负向保持绿），恢复后仍报绿")
    print("  ⇒ 这一版产物检查【真的会响，且不会乱响】")
    return 0


if __name__ == "__main__":
    sys.exit(main())
