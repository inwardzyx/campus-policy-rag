# -*- coding: utf-8 -*-
"""held-out 出题包补丁：让出题底本不再撒谎。

修三件事：
  ① ALREADY_COVERED 手抄 9 条 → 直接从 questions.json 取 gold（单一真源）。
     q18 从 refuse 改成 answer 后手抄那份没跟上，会把
     「学生资助工作实施办法.md｜第十五条」标成 [未覆盖]，
     出题人于是优先给它出题 —— 造出一道和现有题重复的题，held-out 当场贬值。
  ② README 模板里 5 处 2 篇/68 条时代的过期文字（含一个已经变假的反例）。
  ③ 底本加生成时间戳 + 语料构成，让"这是哪一版快照"看得见。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(r"D:\_review_rag-cs")
F = "evalset/heldout/make_pack.py"

pairs: list[tuple[str, str]] = []


def add(old: str, new: str) -> None:
    pairs.append((old, new))


# ---- ① imports：时间戳要用 datetime
add("import json\nimport re\nfrom pathlib import Path",
    "import json\nimport re\nfrom datetime import datetime\nfrom pathlib import Path")

# ---- ② docstring：两篇 → 全库
add("    1. source_policy.md——【给题目作者看的】两篇校规原文，按条款排版，",
    "    1. source_policy.md——【给题目作者看的】全库校规原文（docs/kb 下全部现行制度），按条款排版，")

# ---- ③ ALREADY_COVERED：手抄 → 从 questions.json 派生
add('''# 出题时要避开的条款：现有 20 题已经覆盖了这些，
# held-out 应该去打那59 块零覆盖的地方（README:639 记的 87%）。
ALREADY_COVERED = {
    ("学生纪律处分管理规定.md", "第二章-第四条"),
    ("学生纪律处分管理规定.md", "第二章-第六条"),
    ("学生纪律处分管理规定.md", "第二章-第七条"),
    ("学生纪律处分管理规定.md", "第三章-第二十四条"),
    ("学生纪律处分管理规定.md", "第三章-第九条"),
    ("学生纪律处分管理规定.md", "第三章-第十一条"),
    ("学生请销假制度.md", "总则-第三条"),
    ("学生请销假制度.md", "总则-第四条"),
    ("学生请销假制度.md", "总则-第六条"),
}''',
    '''# 出题时要避开的条款：**直接从现有 questions.json 取 gold**（单一真源）。
# ★ 这里以前是手抄的 9 条，q18 从 refuse 改成 answer 之后没跟上 ——
#   「学生资助工作实施办法.md｜第十五条」会被标成 [未覆盖]，
#   出题人于是优先给它出题，造出一道和现有题重复的题，这套 held-out 当场贬值。
#   ⇒ 判据与真源同源：改 questions.json，这里自动跟着改。
#     （这个仓库反复踩过"两处各写一份判据"的坑，见 loader 与体检脚本那对常量。）
def _already_covered() -> set[tuple[str, str]]:
    p = ROOT / "evalset" / "questions.json"
    qs = json.loads(p.read_text(encoding="utf-8"))
    got = {(q["gold"][0], q["gold"][1]) for q in qs if q.get("gold")}
    assert len(got) >= 10, f"只从 questions.json 读到 {len(got)} 条 gold，去看一眼格式"
    return got


ALREADY_COVERED = _already_covered()''')

# ---- ④ 底本表头：加生成时间戳
add('''    lines = [
        "# 校规原文（出题底本）",
        "",
        "> 这两份文件是**知识库的原始来源**。你的任务是假装你只懂学校规矩、",''',
    '''    lines = [
        "# 校规原文（出题底本）",
        "",
        f"> ★ 生成时间 {datetime.now():%Y-%m-%d %H:%M}"
        f"；来源 docs/kb 下 {len(docs)} 份现行制度，共 {len(all_clauses)} 条"
        f"；其中 {len(uncovered)} 条现有评测集没覆盖。",
        "",
        "> 这些文件是**知识库的原始来源**。你的任务是假装你只懂学校规矩、",''')

# ---- ⑤ 说明书：两篇 → 全库 N 份
add("`source_policy.md` 是两篇校规原文。**这是你唯一该看的材料。**",
    "`source_policy.md` 是全库 {len(docs)} 份现行制度的原文。**这是你唯一该看的材料。**")

# ---- ⑥ refuse 段：68 条 → 动态
add("光标`type: refuse` 没用——库里有 68 条校规条款，什么都能沾上边。",
    "光标`type: refuse` 没用——库里有 {len(all_clauses)} 条条款，什么都能沾上边。")

# ---- ⑦ refuse 例子：换掉过期反例（助学贷款已经不算 refuse 了）
add('''你要写的是**"库里哪一条都没提这件事"**，例如：
- 「图书馆几点关门」——两篇校规里没有校历/场馆信息
- 「学费多少钱」——两篇校规里没有收费标准
- 「怎么申请助学贷款」——两篇校规里没有资助体系''',
    '''你要写的是**"库里哪一条都没提这件事"**，例如：
- 「图书馆几点关门」——全部校规里没有校历/场馆开放信息
- 「学费多少钱」——校规里没有"学费标准"这个值。★ 注意：资助类条款里有
  "减免一学年学费 / 减免一半"这类**额度**，沾边 ≠ 答得出来
- 「宿舍晚上几点熄灯」——有条款要求"遵守学校作息时间"，但不给具体时刻

★ **反例（别学这个）**：「怎么申请助学贷款」**已经不算 refuse 题了**。
  语料扩容后库里真有答案（`学生资助工作实施办法`第十五条：生源地信用助学贷款、
  不超过 20000 元/生/年），它已被改成 answer 题。
  ⇒ **拒答题的边界会随语料变**。出题前请对着 `source_policy.md` 逐条确认
    "库里真的没有用户要的那个值"，而不是"感觉沾不上边"。''')

# ---- ⑧ 覆盖数：9 条/59 块/87% → 动态
add('''**优先从标`[未覆盖]` 的条款出题**——现有 20 题只覆盖了 9 条，
剩下的 59 块（87%）从来没被问过。''',
    '''**优先从标`[未覆盖]` 的条款出题**——现有 20 题（15 answer + 5 refuse）
只覆盖了 **{len(ALREADY_COVERED)} 个**条款，剩下 **{len(all_clauses) - len(ALREADY_COVERED)} 条**
从来没被问过。''')

# ================================================================ 校验 + 落盘
path = ROOT / F
text = path.read_text(encoding="utf-8")
print("=" * 74)
print("校验：" + F)
print("=" * 74)
ok = True
for old, _ in pairs:
    n = text.count(old)
    head = old.splitlines()[0][:56]
    if n != 1:
        ok = False
    print(f"  {'✔' if n == 1 else '✘'} 命中 {n} 次 ← {head}...")

if not ok:
    print("\n★ 校验失败 —— 文件未改动。")
    sys.exit(2)

for old, new in pairs:
    text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")
print(f"\n已写入 {F}（{len(pairs)} 处）")
