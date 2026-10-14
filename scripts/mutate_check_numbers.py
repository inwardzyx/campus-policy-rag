"""对账脚本的变异测试：注入已知错误，验证 check_readme_numbers 真的会报红。

为什么需要这个（2026-10-10）
--------------------------------
`check_readme_numbers.py` 第一版跑出来是 "✓ 通过"，
但变异测试把 README 的 0.5968 改成 0.9999 后，它**照样报通过** ——
因为第一版只查产物侧、没查 README 侧。

★ 这就是今天 loader 清洗账塌成 0 的同一个模式：
  **探头只照了一面，却把"没发现问题"读成"没问题"。**
  而"跑通了"这种自述型证据，恰恰是最不可信的那种。

⇒ 所以这个脚本自己必须有变异测试。规则：
  **任何自检类脚本，都要能证明它在坏情况下会响。**

用法：
    python scripts/mutate_check_numbers.py

判据（与 mutate_check.py 保持一致的写法）：
    - 全部变异都必须被检出（漏一个 = 自检有洞）
    -不注入变异时必须报绿（否则是假阳性满天飞）
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# ★ 与 check_readme_numbers.py 同一行修复，理由也是同一个：
#   本脚本通篇用 print("✓ …") / print("✗ …") 报结果，而这些字符在 GBK 管道下
#   编码不了 —— 一行都打不出来就直接崩，**看起来像"检查失败"，其实是打印失败**。
#   回归测试：本文件里的「基线2」。
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parent.parent
README = REPO_ROOT / "README.md"
CHECK = REPO_ROOT / "scripts" / "check_readme_numbers.py"
PY = sys.executable

# ★ 每个变异：(说明, 要替换的原文, 替换成什么)
#   ⚠️ 原文必须真的存在于 README 里 —— 否则"变异没生效"会被误读成"检出成功"
#   ★ 用 str.replace(old, new) 【不带次数】= 把全部出现处都改掉。
#     第一版是 replace(..., 1)（只改第一处），这本身就是假通过的来源：
#     同一句话在 README 里出现两次时，改第二处既不会被 checker 发现、
#     也不会被变异测试发现（cc 指出的洞2）。
MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "把阈值余量 0.5968 改错（README 侧）",
        "README.md",
        "应答最低 0.5968 vs拒答最高 0.6321",
        "应答最低 0.9999 vs拒答最高 0.6321",
    ),
    (
        "把拒答准确率 3/5 改成 4/5（README 侧）",
        "README.md",
        "拒答准确率掉到 3/5",
        "拒答准确率掉到 4/5",
    ),
    (
        "把拒答题最高向量分 0.6321 改错（README 侧）",
        "README.md",
        "应答最低 0.5968 vs拒答最高 0.6321",
        "应答最低 0.5968 vs拒答最高 0.7123",
    ),
    (
        "把 gold 中位排名 1→2→4 改错（README 侧）",
        "README.md",
        "gold中位排名 **1 → 2 → 4**",
        "gold中位排名 **1 → 2 → 9**",
    ),
    (
        "把源语料丢字数 5241 改错（README 侧）",
        "README.md",
        "合计要丢 5241 字",
        "合计要丢 9999 字",
    ),
    # ★ agent 侧三条（2026-10-12）。README 加了 agent 那一节之后，
    #   这个对账器**全绿** —— 但那是"没检查"不是"检查通过"
    #   （grep 过，一个 agent 相关的数都不查）。
    #   ⇒ 没给它们注入故障，等于新判据是「从没被验证过的绿」。
    (
        "agent 硬答数：第1 遍 6→9（产物里是 6）",
        "README.md",
        "| 1 | 6 / 38 |",
        "| 1 | 9 / 38 |",
    ),
    (
        "★ agent 合计硬答率：只改率不改分子分母（12.9%→18.2%）",
        "README.md",
        "| **合计** | **15 / 116** | **12.9%** |",
        "| **合计** | **15 / 116** | **18.2%** |",
    ),
    (
        "agent 直打拆解：三遍共 121 次拒答改成 99（★防「95%拒答率」被误读）",
        "README.md",
        "三遍共 121 次拒答",
        "三遍共 99 次拒答",
    ),
    # ★★ 这几条是【2026-10-10 盲审抓到后补的】—— 第一版清单里没有，
    #   所以「5/5 全检出」从来没照到这些洞。它们验证的是：
    #   修复之后，再注入这些错误，脚本报不报错。
    #   ⚠️ 要把【全部】出现处都改掉：_check_recall 的判据是
    #   「README 里能找到至少一个等于 15/15 的 x/15」，
    #   只改一处的话别处还留着 15/15，它照样通过
    #   —— 这是我第一版变异设计的错（第二个自己造的假通过）。
    (
        "把应答题 Recall@5 从 15/15 改成 12/15（cc+DSH 独立抓到的洞3，全部出现处）",
        "README.md",
        "15/15",
        "12/15",
    ),
    (
        # ⚠️⚠️ 2026-10-12 改过写法，原因见下 —— 旧写法在修复后成了【哑弹】。
        #★ 旧写法（单行表格行、不带表头）：
        #     "| **量子计算加速** | 还没做 | 为什么要做 |"
        #   它验的是"_todo_rows 能扫到散落在节里的表格行"。
        #   2026-10-12 `_todo_rows` 改成【只认表头是「缺口」的那张表】之后，
        #   这个没有表头的行不属于任何表⇒ 扫不到 ⇒ 注入后检查器仍报"对账通过"。
        #   实测过一次（先改检查器、再照抄旧变异跑一遍）—— **真的漏检了**。
        #   ⇒ 这就是"检出≠证明"的第四种形态：不是这条判据没在工作，
        #     而是【我的修复削弱了别处的检出能力】。改任何一条判据都得回头
        #     把喂它的变异也重跑一遍。
        #  ⇒ 新写法要贴着新判据的形状：**在真待办表内部插一行**，
        #     这才是"有人在〈还没做的〉里偷偷加了一条没登记的待办"这件事的真实形状。
        "往〈还没做的〉的真待办表里塞一条【没登记】的行（验证元检查 _check_todo_registry_complete）",
        "README.md",
        "| **CI 红了不拦合并**（知情取舍） |",
        "| **CI 红了不拦合并**（知情取舍） |\n"
        "| **量子计算加速** | 还没做 | 为什么要做 |",
    ),
    (
        # ⚠️ 同上一条：2026-10-12 `_todo_rows` 改成"只认表头是「缺口」的那张表"之后，
        #   旧写法（散行、不带表头）扫不到 ⇒ 这条变异在套件里报漏检（实测 1/10）。
        #   ⇒ 一并改成在真待办表内部插一行。匹配串要同时满足 `TODO_REGISTRY` 的
        #     两个条件：`row=拒答集` 与 pending_re「只有 N 条 / n≥60」。
        "把已完成的待办塞回〈还没做的〉的真待办表里（验证 _check_todo_claims）",
        "README.md",
        "| **CI 红了不拦合并**（知情取舍） |",
        "| **CI 红了不拦合并**（知情取舍） |\n"
        "| **拒答集只有 5 条** | 在线层 5/5，但 n=5 | 需按 6 类各扩到 ≥10 条（n≥60） |",
    ),
    (
        "把已完成清单里的 Recall@5 改回旧值 13/14（验证 _check_completed_list_metrics）",
        "README.md",
        "**当前**离线层 Recall@5 **15/15**",
        "**当前**离线层 Recall@5 **13/14**",
    ),
    (
        "把实验记录里所有三段排名都改掉（验证 DSH 指出的静默降级）",
        "experiments/_语料规模与多查价值实验.md",
        "中位排名",
        "排名中位数",
    ),
    #★★ 下面两条验的是【过期陈述】这一族（2026-10-11 新加）。
    # ⚠️ 这里原本有两条（held-out 空壳 / 查询改写漏写），**已删**（2026-10-11 盲审后）。
    #   ① stale 族已从"报错"降级为"提示"——它有实测假阳性，
    #      且变异与规则同源（DSH 第 8 条）⇒"1/1 检出"是自证，不携带信息量。
    #      所以"期望它红"的变异配不出来，也就不该有。
    #   ② 真要有否决权，得先补【负向变异】（"应当保持绿"的删除线留档 / 历史叙述）
    #      —— 那才是有信息量的方向。但那一步还没做。
    #   ★ 纪律：**配不出能被检出的变异，就说明这个洞还没想清楚，别急着写检查。**
    #   ★ 同源教训：loader 清洗账塌成 0、对账脚本第一版假绿，都是「探头只照一面」。
    # ★★★ 下面三条验的是【token 成本那一节】（2026-10-12 加）。
    #   起因：README 那张表在 2026-10-12 从「单路径三列均值」改成「双列对照」，
    #   对应的判据正则也跟着换了。换判据之后【必须】补变异 ——
    #   否则新正则可能压根不匹配任何东西，而通用路径会把"抽不到"报成
    #   "数字对不上"，看起来像检查在工作。
    #   ★★ 2026-10-13：下面三条的 old 原先是 **1906** —— README 那条在
    #      `--runs 3` 重测后改成了 **1900**，于是三条同时"原文不存在"。
    #      好在"注入失败算失败"这条规矩它自己就执行了（`not_applied` 非空即 return 1），
    #      所以它是当场报红、不是静悄悄放过。
    #      （但这套变异**没有任何闸门在跑**：pre-push 只跑对账器、CI 也不跑它
    #        ⇒ 它红了一整天没人知道。当天把它挂进 CI 快 job 了，见 ci.yml。）
    (
        "agent token 均值 total：只改 README 那一格（1900→1500）",
        "README.md",
        "token/题（均值） | **946** | **1900**",
        "token/题（均值） | **946** | **1500**",
    ),
    # ★ 2026-10-14 加：只检索端点那一节的成本数字（README 里「每次调用省 366.1 token」）。
    #   这条判据锚的产物**每跑一次都会变**（生成文本长度不同），所以它注定要跟着重跑更新
    #   —— 这正是它存在的意义：过期的成本数字比没有数字更坏（它看起来像实测）。
    #   ⚠️ 实测印证过一次：重跑同一份脚本，369.5 → 366.1（代码没动）。
    #      ⇒ 下面这两条变异的原文串**必须跟着现产物写**，否则注入失败（那算失败，不算通过）。
    #   ★ 两侧各打一条：只改 README / 只改产物。单打一侧就能检出，
    #     才说明这条判据真的在比"两个来源"，而不是单边存在性检查。
    (
        "只检索端点省下的 token：只改 README 那一格（366.1→400）",
        "README.md",
        "每次调用省 366.1 token",
        "每次调用省 400 token",
    ),
    (
        "只检索端点省下的 token：只改产物那一格（366.1→400）",
        "evalset/report-search-ab.json",
        '"tokens_saved_per_call": 366.1,',
        '"tokens_saved_per_call": 400.0,',
    ),
    (
        # ★ 通用路径只能取产物侧的 group(1)，所以判据只校了 agent 那一列；
        #   直打那一列由 scripts/check_token_report.py 逐格校。
        #   ⇒ 下面这两条里，"改直打那一格"期望由【那一个】判据报出不一致，
        #     但报的不是"946 那一格错了"，而是两列整体对不上 —— 精度上的已知让步。
        "agent token 均值 total：只改直打那一格（946→1000）",
        "README.md",
        "token/题（均值） | **946** | **1900**",
        "token/题（均值） | **1000** | **1900**",
    ),
    (
        "★ 把整张对照表的两列调换（验证判据确实在比同一行）",
        "README.md",
        "| token/题（均值） | **946** | **1900**（直打的 2.01 倍） |",
        "| token/题（均值） | **1900** | **946**（直打的 2.01 倍） |",
    ),
    (
        "把独立集在线层拒答准确率 39/61 改回 40/61（README 侧，2026-10-12 新加的断言）",
        "README.md",
        "在线层（产品默认）**39/61",
        "在线层（产品默认）**40/61",
    ),
    (
        # ★ 2026-10-13 补：缓存可行性那一行的数字，原先是"照脚本推断"出来的、没有产物；
        #   现在实验落盘了，README 必须与产物一致 ⇒ 这条盯的就是那根新接的线。
        "缓存可行性：把『同 gold 题对最低余弦』改掉（0.3136→0.3199，README 侧）",
        "README.md",
        "同 gold 题对最低余弦 **0.3136**",
        "同 gold 题对最低余弦 **0.3199**",
    ),
    (
        "缓存可行性：把『精确重复 0/20』的分子改成 3（产物里是 0 组）",
        "README.md",
        "精确重复 **0/20**",
        "精确重复 **3/20**",
    ),
]


def run_check(env_extra: dict | None = None) -> tuple[int, str]:
    """跑一次对账脚本。

    env_extra：额外/覆盖的环境变量。存在的理由是「基线2」——
    要模拟 pre-commit 的环境（stdout 是管道 + GBK），
    那跟手动开个终端跑完全是两回事。
    """
    env = {**os.environ, **env_extra} if env_extra else None
    r = subprocess.run(
        [PY, str(CHECK)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(REPO_ROOT),
        env=env,
    )
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def main() -> int:
    if not README.exists():
        print("✗ 找不到 README.md")
        return 2
    original = README.read_text(encoding="utf-8")

    # ---- 0. 干净时必须绿 ----
    code, out = run_check()
    if code != 0:
        print("✗ 未注入变异时对账就报错了 —— 先修好再测：")
        print(out)
        return 2
    print("✓ 基线：未注入变异时报绿")

    # ---- 0b. ★★ 负向基线：stdout 不是 UTF-8 时【也必须绿】----
    #   起因（2026-10-11，真实发生在这台机器上）：
    #     pre-commit 里跑对账脚本时 stdout 是【管道 + GBK】，脚本最后那句
    #     print("✓ 数字对账通过…") 的 ✓ 编码不了 → UnicodeEncodeError → 退出码 1。
    #     于是钩子报出的是「数字对账失败 —— 数字与产物不一致」：
    #     **逻辑全过了，报出来的却是一句假话**，还会把人逼去 --no-verify。
    #   修法：check_readme_numbers.py 开头加
    #     sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    #     （与 scripts/check_push_paths.py:48 同款写法，那边早就有）。
    #   ★ 这条就是那个修复的回归测试：谁把那行 reconfigure 删掉，这里立刻变红。
    #     —— 也正是本文件第 116 行说的那种「负向变异」：
    #       有信息量的不是"改坏了会响"，而是"不该响的时候确实不响"。
    code, out = run_check({"PYTHONIOENCODING": "gbk"})
    if code != 0:
        print("✗ 基线2失败：把 stdout 强制成 GBK（= pre-commit 的环境）就报错了。")
        print("  ⇒ 检查逻辑没错，是它【打印】时就崩了，于是报出一句假话。")
        print("  ⇒ 修法：check_readme_numbers.py 开头补 sys.stdout.reconfigure(encoding='utf-8')")
        print(out)
        return 2
    print("✓ 基线2：stdout 强制成 GBK（模拟 pre-commit 的管道）时仍然报绿")

    # ---- 逐个变异，每轮恢复原文 ----
    undetected: list[str] = []
    not_applied: list[str] = []
    applied: list[str] = []

    for desc, target_rel, old, new in MUTATIONS:
        target = REPO_ROOT / target_rel
        base = original if target_rel == "README.md" else target.read_text(encoding="utf-8")
        if old not in base:
            not_applied.append(desc)
            print(f"? 跳过（原文不存在，变异没生效）：{desc}")
            print(f"    找的是：{old!r}")
            continue

        mutated = base.replace(old, new)  # ★ 全量替换，不带次数
        assert mutated != base, "替换没生效"
        # ★ 换行必须原样写回。
        #   实测踩过（DSH 指出的读码推断，我确认了机制）：
        #   read_text 会把 CRLF 统一成 \n，write_text 又按 os.linesep 翻回去。
        #   ⇒ 若README 原本是 LF，跑一次这个脚本整份文件就变 CRLF，
        #     产生全文件级 diff，而且 git 会把"每一行都改了"。
        #
        # ⚠️ DSH 指出的原文说法："两个脚本纯读文件 + 正则"对 mutate 不成立 ——
        #   它一轮要写 6 次（5 注入 + 1 复原），跑的时候是在【改工作区】。
        #   这条已写进 CLAUDE.md 的协作黑板一节，别再宣称它是只读的。
        target.write_text(mutated, encoding="utf-8", newline="")
        applied.append(desc)
        try:
            code, out = run_check()
            if code == 0:
                undetected.append(desc)
                print(f"✗ 漏检：{desc}")
                print("    ⇒ 对账脚本对这一处没反应，说明它没在照 README")
            else:
                first = next(
                    (ln.strip() for ln in out.splitlines() if ln.strip().startswith(("✗", "⚠"))),
                    "(没打印错误行)",
                )
                print(f"✓ 检出：{desc}")
                print(f"    {first}")
        finally:
            target.write_text(base, encoding="utf-8", newline="")

    # ---- 复原后必须还绿 ----
    code, out = run_check()
    if code != 0:
        print("✗ 全部变异恢复后仍然报错 —— README 可能没还原干净")
        print(out)
        return 2

    print()
    # ★★ 洞1（cc 与 DSH 独立同时抓到）：注入失败必须算失败。
    #   原来 not_applied 只打印不 return，最后照样打
    #   "✓ 全部 {len(MUTATIONS)} 个变异都被检出" —— 那个 len 是【清单长度】，
    #   不是实际跑了几条。于是「2 个没注入 + 3 个检出」和「5 个都检出」
    #   输出同一句话、同一退出码。⇒ 现在非空即失败。
    if not_applied:
        print(f"✗ {len(not_applied)}/{len(MUTATIONS)} 个变异没注入成功（原文已变），"
              "这些位置没测到 —— 【不能算通过】：")
        for d in not_applied:
            print(f"    - {d}")
        print()
        print("  ⚠常见原因：README 措辞/空格变了（这里 old 是【逐字节精确】匹配，")
        print("    而 check 侧正则都是 \\s* 宽容型 —— 一处排版调整就能让变异全静默跳过）。")
        print("  ⇒ 修法：把 MUTATIONS 里的 old 改成跟着 README 一起更新，或改用正则。")
        return 1

    if undetected:
        print(f"✗ {len(undetected)}/{len(applied)} 个变异漏检 —— 自检有洞，别信它：")
        for d in undetected:
            print(f"    - {d}")
        return 1

    #★ 打实际跑出来的数，不打 len(MUTATIONS)
    print(f"✓ 实际注入 {len(applied)} 个变异，{len(applied) - len(undetected)} 个被检出，"
          "且恢复后仍报绿")
    print("  ⇒ 这一版对账脚本【真的会响】，可以挂pre-commit")
    return 0


if __name__ == "__main__":
    sys.exit(main())