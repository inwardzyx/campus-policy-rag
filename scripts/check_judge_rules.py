# -*- coding: utf-8 -*-
r"""核 CLAUDE.md 里「假绿的三种形态」那一节写的规矩，代码里到底有没有在遵守。

★ 为什么要给**规矩文档**也配一条判据：
  这一族教训（假绿）全部来自"我自己写完自己读一遍"。
  而"写完自己读一遍"恰恰最容易漏 —— 它读的是自己刚写的句子，不是数据。
  文档同理：规矩写得再狠，没有锚点，下一轮照样会犯。

  更要紧的一条：**规矩文档会漂**。
  今天这一节就是照着两次真实踩坑写的，但如果半年后没人再看代码，
  "当时已经修好了"这句话会碎，而没人知道它碎了。
  ⇒ 所以规矩必须有一条**只读代码就能判**的锚点。

★ 本脚本只做机械可判的那几条。判断题（"这个数字该不该是 155"）不做。

跑法（仓库根目录）：
    python -m scripts.check_judge_rules

★ 它故意只钉"能在代码里查出来"的三件事：
    ① 对账脚本的声称值【不许硬编码常量】—— 必须从被检查对象解析
    ② 变异测试必须同时有【文档侧】与【数据侧】两轮
    ③ 变异测试的收尾必须【以 EXPECTED 对账】—— 用例缺失算失败
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

# ★ 与同族脚本保持一致：✓✗ 在 cp936 里编不出来。
#   pre-push 里跑本脚本时 stdout 是管道 + GBK，
#   崩在打印成功消息上 ⇒ 退出码 1 ⇒ 钩子报一句**假话**（"数字对不上"）。
#   这个病在本项目已是第 5 次（check_readme_numbers / mutate_check_numbers /
#   check_artifact_sanity / check_token_report / 本脚本）。
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
RULES = ROOT / "CLAUDE.md"
# ★★ 本脚本自己也要被这套规矩管着，所以放在同一个列表里。
SELF = "scripts/check_judge_rules.py"

# ★ ① 确实误伤的脚本放这里，**每一行都要写明理由**（没有理由的白名单就是"关掉判据"）。
CLAIM_SCAN_SKIP: dict[str, str] = {}


def _judge_files() -> list[str]:
    """列出所有受这套规矩管的判据脚本（对账脚本 + 变异脚本）。

    ★★ 为什么用 glob 显式枚举、而不是在列表里写 `scripts/check_*.py`：
      第一版把通配符**原样**放进列表、然后直接 `ROOT / rel` 去读 ——
      结果去读一个名字里带 `*` 的文件，`OSError: Invalid argument` 直接崩。
      崩在这里还算好（不是静悄悄放过），但它说明一件事：
      **靠列表里写通配符来"覆盖全部文件"是假的** ——
      不展开就没有文件被检查，而外面看着像"检查了 5 个脚本"。
      ⇒ 规矩：涉及"全部文件"的判据，必须显式枚举 + 报出实际数量，
        否则读者会以为覆盖面比实际大。
    """
    out: list[str] = []
    for pat in ("scripts/check_*.py", "scripts/mutate_check_*.py"):
        for f in sorted((ROOT).glob(pat)):
            rel = f.relative_to(ROOT).as_posix()
            if rel == SELF:
                continue          # 本脚本的检查项就是"规矩本身有没有被改写"
            out.append(rel)
    return out


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def check_rules_section(text: str) -> list[str]:
    """规矩那一节还在不在。

    ★ 为什么必须查它自己：认这一节靠的是标题。
      哪天它被改名/挪位而本脚本没跟着改，
      下面所有检查都会"因为找不到对象而零匹配"⇒ 全绿。
      **一个永远绿的检查器比没有检查器更坏**（这条本项目已记过两次）。
    """
    bad: list[str] = []
    anchor = "假绿的三种形态"
    if anchor not in text:
        bad.append(f"✗ CLAUDE.md 里找不到「{anchor}」这一节 —— "
                   f"本脚本靠它认对象。改了标题请同步改这里。")
    return bad


def _claim_shaped_tuples(src: str) -> list[tuple[int, str]]:
    """找『三元组 + 首项字符串 + 次项数字字面量』—— 声称值的形状本身。

    ★ 为什么用 ast 而不是正则：形状是结构，正则只能认风格（见下面的三版教训）。
      这一条不会误伤 `t.get("done_at", 1)`（不是元组）、
      也不会误伤 MUTATIONS 的 `(说明, 原文, 替换, 期望)`（四项）。
    """
    out: list[tuple[int, str]] = []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if not isinstance(node, ast.Tuple) or len(node.elts) != 3:
            continue
        a, b = node.elts[0], node.elts[1]
        if not (isinstance(a, ast.Constant) and isinstance(a.value, str)):
            continue
        if not (isinstance(b, ast.Constant) and isinstance(b.value, (int, float))
                and not isinstance(b.value, bool)):
            continue
        label = a.value if len(a.value) <= 24 else a.value[:24] + "…"
        out.append((node.lineno, f'("{label}", {b.value!r}, …)'))
    return out


def check_no_hardcoded_claims() -> list[str]:
    r"""① 对账脚本里不许出现"声称值 = 抄来的常量"。

    ★★ 这条改到第三版了，三版的教训都留着（因为**同一类错最容易原样再犯**）：

      第一版（line 正则）**全是误伤**：它用 `\(\s*["'][^"']+["']\s*,\s*-?\d` 去抓
      `("名字", 数字` 这个形状，实际抓到的 5 条里 3 条是函数调用的默认参数
      （`rd.rfind("\n", 0, m.start())`、`t.get("done_at", 1)`、`x.get("missing_calls", 0)`）。
      ⇒ **判据自己报假错，比没有判据更坏**（CLAUDE.md 已记过这条）。

      第二版（同一行还得出现 `round(`/`len(`/`*`）把误伤压下去了，但**漏掉真货**：
      声称值完全可以不带 round —— 2026-10-13 实测，往 claims 列表里插一行
      `("伪造的声称值", 155, 1.0)`，第二版照样报绿。
      ⇒ "看这一行有没有 round(" 是**认风格**，不是认结构。

      第三版（本版）**认结构**：ast 找『三元组 + 首项字符串 + 次项数字字面量』。
      声称值的形状就是它，与后面跟 `round(...)` 还是变量无关；
      而函数调用默认参数不是元组、`MUTATIONS` 的元组是四项 ⇒ 都不误伤。

    ★ 仍然判不了的：**语义** —— "这个 155 到底是不是抄来的"，只有人知道。
      所以本判据只钉形状；确认是误伤就登记进 CLAIM_SCAN_SKIP 并写明理由。
    ★ 并且**只对含 claims/grab 的脚本生效** ——
      没在维护"文档 ↔ 数据"断言的脚本不该被这条扫。
    """
    bad: list[str] = []
    scanned = 0
    for rel in _judge_files():
        f = ROOT / rel
        if not f.exists():
            continue
        src = _read(f)
        # ★ 只有"维护文档↔数据断言"的脚本才谈得上声称值。
        #   对它们之外的脚本扫这条，纯属制造噪音。
        if "claims" not in src and "grab(" not in src:
            continue
        if rel in CLAIM_SCAN_SKIP:
            continue
        scanned += 1
        for lineno, shape in _claim_shaped_tuples(src):
            bad.append(
                f"  {rel}:{lineno} 疑似把声称值硬编码成常量：{shape}\n"
                f"    ⇒ 若这确实是「文档里写的数」，请改成从被检查对象解析（grab(pat)）。\n"
                f"      判据本身有误伤可能 —— 确认是误伤就把 {rel} 登记进 CLAIM_SCAN_SKIP 并写明理由。")

    # ★★★ 自检（反向验证逼出来的）：扫了 0 个脚本 ⇒ 报红，不许安静放过。
    #   实测把 `src = _read(f)` 改成 `src = ""` 之后，本函数一行都不扫，
    #   而 main() 照样打「✓ ① 声称值不许硬编码常量」⇒ **判据自己瞎了也是绿的**。
    #   这正是本项目记录过的那条：一个永远绿的检查器比没有检查器更坏。
    #   ⇒ 凡是"扫一批文件"的检查，都必须有"扫到几个"的计数与下限断言。
    if scanned == 0:
        bad.append(
            "  ① 这一条扫了 **0 个脚本** —— 要么没有脚本维护『文档 ↔ 数据』断言，\n"
            "    要么读文件的代码本身出不来（比如 src 被写成空串）。\n"
            f"    （扫到的脚本数是 {scanned}，下限应为 1）\n"
            "    ★ 一个扫不到东西的检查器一定在报绿，那是假绿，不是通过。")
    return bad


def check_mutate_two_sides() -> tuple[list[str], list[str]]:
    """② 变异脚本应当也改产物侧（纯结构不变式才被触发了）。

    ★★ 这一条【只提示、不拦提交】，而且是有理由的 —— 记下来免得下一个人
      又把它当成"漏做的活"去补：
      实测报红了 `mutate_check_numbers.py` 与 `mutate_check_artifact.py`，
      但读它们的判据（`check_readme_numbers.py` 第 789 行附近），
      那两处不变式**已经写明是知情取舍**：

        · **变异与规则同源**（DSH 第 8 条）：变异替换成的那句话，
          正是正则照着设计时用的那句话 ⇒ "1/1 检出"几乎不携带信息量。
        · **探针可静默关掉**：questions.json 一删，n=0 < min，检查变绿。
        ⇒ "要变成拦提交的那类，必须先补上负向变异和同源变异"，
          在那之前"它只提示、不拦提交"是**结论**，不是漏洞。

      而今天的 `mutate_check_token.py` 能真的做数据侧，
      是因为那批 ratio 不变式是**纯算术**的（能重算、能对拍），
      与文档措辞无关 —— 换批数据也照样有效。
      两者不是同一类东西，不能一刀切要求"都必须有数据侧"。

    ★ 教训（这条比判据本身重要）：
      **判据发现"疑似违规"时，先去核实那是不是一个有意的取舍。**
      我第一反应是"那我去把两个脚本补上" —— 那样会做出一堆
      "变异同源、携带不了信息量"的假测试，比不做更糟。
    """
    hint: list[str] = []
    bad: list[str] = []
    scanned = 0
    # MUTATIONS 的元组里若出现 `.json` / `.py` 这类**产物路径**，就算数据侧存在
    for rel in _judge_files():
        if "mutate_" not in rel:
            continue
        f = ROOT / rel
        if not f.exists():
            continue
        src = _read(f)
        scanned += 1
        m = re.search(r"^MUTATIONS[^\n]*\n(.*?)(?=\n[A-Za-z_#\n]|\Z)",
                      src, re.M | re.S)
        seg = m.group(1) if m else ""
        has_product_side = bool(
            re.search(r"""["'][^"']*\.(json|py|jsonl)["']""", seg))
        if "DATA_MUTATIONS" in src or has_product_side:
            continue
        hint.append(
            f"  {rel}：所有变异都只改【文档】，没有一条改产物。\n"
            f"    · 若该判据里有**不读文档的纯结构不变式**，它在这个脚本里从没被触发过。\n"
            f"    · 但这可能是有意的取舍（如 check_readme_numbers 那两处注明\n"
            f"      『变异与规则同源、不携带信息量』⇒ 只提示不拦提交）。\n"
            f"    · 先核实是取舍还是遗漏，再决定补不补 —— 不确定的别硬补。")
    # ★ 与 ①③ 同一条纪律：扫了 0 个变异脚本 ⇒ 报红。
    if scanned == 0:
        bad.append(
            "  ② 这一条扫了 **0 个变异脚本**（下限应为 1）——\n"
            "    ★ 扫不到东西的检查器一定在报绿，那是假绿。")
    return bad, hint


def check_missing_case_is_failure() -> list[str]:
    """③ 变异脚本必须能对"【用例/变异自己消失了】"报错。

    ★★ 第一版查 `for x in EXPECTED` 这个写法，报红了 `mutate_check_token.py`。
      但读代码发现它用的是另一条等价路径：收尾打印 `len(MUTATIONS)`，
      而**注入失败（skipped）已经 return 1** ——
      也就是说"这条变异没跑到"它本来就当失败。
      ⇒ 查变量名同样是查风格。

    ★ 改成查【行为】：脚本必须存在"把 skipped / 漏检 变成非零退出"的路径。
      这才是今天真正修掉的那个洞的机制。

    ★★ 反向验证又逮到一个真洞（第一版写成这样）：
        has_fail = re.search(r"\\b(skipped|missed)\\b[\\s\\S]{0,2000}?return 1", src)
      —— 把 `mutate_check_token.py` 里那条 `print(✗ {len(skipped)} 条注入失败)`
        整行删掉之后，本判据**照样报绿**。
      原因：那 2000 字符的窗口跨得太远，扫到了文件**收尾处另一个 return 1**。
      ⇒ 「后面有 return 1」不等于「这个列表会导致失败」。

    ★ 改法：先在**同一个 if 块**里找。
      判据 = 存在 `if skipped:` / `if missed:` 之类分支，
      且该分支内（到下一个同级 `if` 为止）出现 `return 1`。
    """
    bad: list[str] = []
    scanned = 0
    for rel in _judge_files():
        if "mutate_" not in rel:
            continue
        f = ROOT / rel
        if not f.exists():
            continue
        src = _read(f)
        scanned += 1
        lines = src.splitlines()
        has_list = re.search(r"\b(skipped|missed)\b", src) is not None
        has_fail = False
        for i, line in enumerate(lines):
            if not re.match(r"\s*if\s+\w*(skipped|missed)\w*\s*:", line):
                continue
            # ★ 只看这个 if 块【内部】：缩进更深，直到遇到同级或更浅的语句
            indent = len(line) - len(line.lstrip())
            for j in range(i + 1, min(i + 40, len(lines))):
                nxt = lines[j]
                if not nxt.strip():
                    continue
                cur = len(nxt) - len(nxt.lstrip())
                if cur <= indent:
                    break          # 出块了
                if "return 1" in nxt or "return 2" in nxt:
                    has_fail = True
                    break
        if has_list and not has_fail:
            bad.append(
                f"  {rel}：收集了 skipped/missed，但**那个 if 分支里没有非零退出**。\n"
                f"    ⇒ 用例/变异自己消失了也会报'全部通过'。\n"
                f"      在该分支内加 return 1。")
    if scanned == 0:
        bad.append(
            "  ③ 这一条扫了 **0 个变异脚本**（下限应为 1）——\n"
            "    ★ 扫不到东西的检查器一定在报绿，那是假绿。")
    return bad


def main() -> int:
    if not RULES.exists():
        print(f"✗ 缺 {RULES.relative_to(ROOT)}")
        return 1
    text = _read(RULES)

    # ★ 分两级：【拦提交】与【只提示】。
    #   本项目已记过两次"报假错的检查会被当成噪音关掉"，
    #   而更要命的是"常开的警告等于没有警告"。
    #   ⇒ 真正违规要拦；"可能是有意的取舍"只提示，且提示里写清怎么核实。
    bad: list[str] = []
    hints: list[str] = []

    r = check_rules_section(text)
    if r:
        # ★ 这句原来写的是「✗ 规矩那一节还在」—— 报红的时候说"还在"，
        #   读的人（半年后的我）会以为是别的东西出错了，方向被这句话带偏。
        #   ★ 教训：报错文案**要说"哪里不对"**，不是"哪个对象存在"。
        bad.extend(["✗ 认不出 CLAUDE.md 里那一节规矩（它被改名/删了？）"] + r)
    else:
        print("✓ 规矩那一节还在")

    r = check_no_hardcoded_claims()
    if r:
        bad.extend(["✗ ① 声称值不许硬编码常量"] + r)
    else:
        print("✓ ① 声称值不许硬编码常量")

    b, h = check_mutate_two_sides()
    bad.extend(["✗ ② " + x for x in b])
    hints.extend(h)

    r = check_missing_case_is_failure()
    if r:
        bad.extend(["✗ ③ 用例缺失必须算失败"] + r)
    else:
        print("✓ ③ 用例缺失必须算失败")

    n_files = len(_judge_files())
    if hints:
        print()
        print("─" * 60)
        print("⚠ 以下是【提示】，不拦提交 —— 它们可能是有意的取舍，先核实再动手：")
        print("\n".join(hints))
        print("─" * 60)

    if bad:
        print()
        print("\n".join(bad))
        print()
        print("  这些是 CLAUDE.md〈假绿的三种形态〉那一节写下的规矩，代码里还没遵守。")
        print("  ⇒ 要么改代码，要么改规矩（规矩过期了就改规矩，别让文档说空话）。")
        return 1
    print()
    print(f"✓ CLAUDE.md 的 3 条硬规矩在 {n_files} 个判据脚本里都有对应实现"
          + (f"（另有 {len(hints)} 条提示）" if hints else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
