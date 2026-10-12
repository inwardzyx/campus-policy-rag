"""数字对账：README 里写的数字，必须和脚本落盘的真实产物一致。

为什么需要这个（2026-10-10）
--------------------------------
今天的盲审抓到 README 里一处"跨尺子配对"：同一格里
「8 块」配4195 字、「5241 字」配 11 块 —— 前者是入库后体检口径，
后者是清洗前源语料口径，两把尺子各取一半，读起来像同一个数。

这类错**没有任何工具会报**：
- 测试不会跑（文档不是被测对象）
- 审稿人会抓，但要花18K token 且不一定抓到
- 最危险的是它"读起来完全合理"

⇒ 所以把它变成一个**零 token 的机械检查**：断言写死，独立算，
两边对不上就非零退出。挂在 pre-commit 上。

★ 本脚本自己不发requests、不调LLM，纯读文件 + 正则，
  所以能在 pre-commit 里跑（几毫秒）。

用法：
    python scripts/check_readme_numbers.py# 提交前自检，有错非零退出
    python scripts/check_readme_numbers.py --show   # 顺便打印实测值

设计约束（照今天的教训写下来的）
--------------------------------
1. **每条断言必须带src**，即"这个数字该从哪个文件哪一行来"。
   没 src 的断言等于凭记忆写，正是今天出错的那类。
2. **实测值由脚本现算，不抄 README**。抄的话就成了自己核自己。
3. **允许"值对不上但口径不同"** —— 用note 字段声明，
   但 note 必须写清楚是哪把尺子。不写 note 的一律算失败。
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ★ 2026-10-11：stdout 必须显式转成 UTF-8 —— 这是被 pre-commit 抓到的一个**假错**。
#   症状：钩子里 stdout 是【管道 + GBK】（Windows 默认），脚本最后那句
#   `print("✓ 数字对账通过…")` 的 ✓ 编码不了 → UnicodeEncodeError → 非零退出。
#   ⇒ 钩子于是报出「数字对账失败 —— 数字与产物不一致」：
#     **检查逻辑全过了，报出来的却是一句假话**，而且会把人逼去 --no-verify。
#   （报假错的检查比没有更坏 —— 它教人不信检查。同源教训见 _check_stale_text。）
#   ★ 写法与 scripts/check_push_paths.py:48 一致：那边早就有这一行，这里漏了。
#   ★ 回归测试：scripts/mutate_check_numbers.py 的「基线2」——把它删掉，那条基线立刻红。
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Assertion:
    """一条「README 写了什么」↔「产物里真实是什么」的对应关系。

    ★★ 2026-10-10 修掉的严重缺陷：
    第一版所有断言的 src 都指向**产物**，没有一条指向 README.md ——
    于是变异测试把 README 里的 0.5968 改成 0.9999，脚本照样报"✓ 通过"。
    原因和 loader 清洗账塌成 0 是同一个：**探头只照了一面，
    却把"没发现问题"读成"没问题"**。

    ⇒ 现在两段都要查：readme_pattern 抠出 README 写的数，
      src/pattern 现算产物里的真值，两者对不上才报错。
    """

    name: str
    readme_value: str
    """README 里写的字面值。用于在 README 中定位。"""

    src: str
    """真实产物的路径（相对仓库根）。脚本从这里现算，不信任任何抄写。"""

    pattern: str
    """从 src 里提取真值的正则。第一个捕获组是要比对的数字。"""

    expect: str | None = None
    """期望值。None = 只做存在性检查（产物在不在、数字抽不抽得到）。"""

    note: str = ""
    """口径说明。★非空不代表豁免—— 见check() 的实现。"""

    readme_pattern: str | None = None
    """★ 从 README.md 抠出它写的那个数。None = 声明不查 README 侧。

    设成"" 也可以，但要理解那意味着**放弃了对文档的检查**。
    """

    readme_take_first: bool = False
    """★ 这条断言只关心第1 个捕获组时设True。

    默认两处都拼全部组（和 fetch_actual 同规则）。
    但若某个 readme_pattern 本来就抓好几个人关心的数
    （比如「应答最低 X vs 拒答最高 Y」），而产物侧只产出一个，
    拼起来就永远对不上—— 这时要显式说"只取第一个"，
    剩下的交给另一条断言。**不写这一项就等着报假错吧。**
    """

    _actual: str | None = field(default=None, repr=False)
    _readme: str | None = field(default=None, repr=False)
    _rest: list[str] = field(default_factory=list, repr=False)

    def fetch_readme(self) -> str | None:
        """★ README 里实际写的那个数。抽不到 ⇒ 报「文档里找不到这句话」。"""
        if self._readme is not None:
            return self._readme
        if self.readme_pattern is None:
            self._readme = ""
            return self._readme
        p = REPO_ROOT / "README.md"
        if not p.exists():
            return None
        m = re.search(self.readme_pattern, p.read_text(encoding="utf-8"))
        if not m:
            self._readme = None
            return None
        if self.readme_take_first:
            self._readme = m.group(1)
        else:
            # ★ 和 fetch_actual 保持同一条规则：多组时拼全部。
            #   实测踩过（cc 指出的不对称）：产物侧拼全部组、README 侧只取 group(1)，
            #   于是「第 2 组里那个数」在 README 侧被丢弃，只是恰好被别的断言兜住。
            self._readme = "/".join(g for g in m.groups() if g is not None)
        return self._readme

    def fetch_actual(self) -> str | None:
        if self._actual is not None:
            return self._actual
        p = REPO_ROOT / self.src
        if not p.exists():
            return None
        text = p.read_text(encoding="utf-8")
        hits = list(re.finditer(self.pattern, text))
        if not hits:
            self._actual = None
            return None
        # ★★ 扫【全部】出现处，不只取第一处。
        #   原因（实测撞到）：同一事实在文档里有多种写法时，
        #   re.search 只取第一处 → 改掉第一处还能从别处匹配上，
        #   于是"改了数字脚本却没反应"，看着像检查坏了。
        #   取第一处做比对、其余处必须一致 —— 不一致就是文档自相矛盾。
        vals = [
            ("/".join(g for g in m.groups() if g is not None))
            for m in hits
        ]
        self._actual = vals[0]
        self._rest = vals[1:]
        return self._actual


# ---------------------------------------------------------------
# ★ 断言清单：每一条都带 src 和算式
# ---------------------------------------------------------------
ASSERTIONS: list[Assertion] = [
    Assertion(
        name="离线层拒答准确率",
        readme_value="3/5",
        src="evalset/report-vector.md",
        pattern=r"拒答题数：(\d+)\s*拒答准确率：(\d+)/(\d+)",
        expect=None,  # ★不走通用路径，见 _check_refuse_rate（这里含"拒答题数"这个无关段）
        readme_pattern=None,
    ),
    Assertion(
        name="离线层阈值余量（应答题最低向量分）",
        readme_value="0.5968",
        src="evalset/report-vector.md",
        pattern=r"应答题最低向量分\s*([0-9.]+)",
        expect="0.5968",
        readme_pattern=r"应答最低\s*([0-9.]+)\s*vs\s*拒答最高\s*([0-9.]+)",
        readme_take_first=True,
        note="这条的 readme_pattern 会抓两个数（余量+上限），"
        "而 fetch_actual 只产出一个（0.5968）—— 刻意留下上限那一半，"
        "由下面「拒答题最高向量分」那条独立负责。",
    ),
    Assertion(
        name="离线层拒答题最高向量分",
        readme_value="0.6321",
        src="evalset/report-vector.md",
        pattern=r"拒答题最高向量分\s*([0-9.]+)",
        expect="0.6321",
        readme_pattern=r"应答最低\s*[0-9.]+\s*vs\s*拒答最高\s*([0-9.]+)",
    ),
    Assertion(
        name="应答题总数与 Recall@5",
        readme_value="15/15",
        src="evalset/report-vector.md",
        pattern=r"应答题数：(\d+)\s*Recall@5：(\d+)/(\d+)",
        expect=None,
        readme_pattern=None,  # ★ 故意留空：这题在 README 里多处出现，见专项检查
    ),
    Assertion(
        name="语料规模实验 gold 中位排名",
        readme_value="1 → 2 → 4",
        src="experiments/_语料规模与多查价值实验.md",
        pattern=r"中位排名\s*(\d)\s*→\s*(\d)\s*→\s*(\d)",
        expect=None,
        readme_pattern=r"gold中位排名\s*\*\*(.+?)\*\*",
    ),
    Assertion(
        name="源语料切分丢字数（清洗前口径）",
        readme_value="5241",
        src="experiments/clean_corpus.py",
        pattern=r"合计要丢\s*(\d+)\s*字",
        expect="5241",
        readme_pattern=r"合计要丢\s*(\d+)\s*字",
        note="清洗前源语料口径。与入库后体检的 4195 字不是同一个数，"
        "README 必须分开标注，不能在同一格里混用",
    ),
]


def _check_refuse_rate() -> list[str]:
    """拒答准确率要单独查：正则有三个捕获组，容易错位。

    ★ 两边都查（2026-10-10 补）：第一版只查了产物侧，
    变异测试把 README 的 3/5 改成 4/5 时脚本照样报"通过"。
    """
    p = REPO_ROOT / "evalset/report-vector.md"
    rd = REPO_ROOT / "README.md"
    if not p.exists():
        return ["✗ 离线层拒答准确率：找不到 evalset/report-vector.md"]
    m = re.search(r"拒答题数：(\d+)\s*拒答准确率：(\d+)/(\d+)", p.read_text(encoding="utf-8"))
    if not m:
        return ["✗ 离线层拒答准确率：正则没匹配到，产物格式可能变了"]
    total, ok, den = m.group(1), m.group(2), m.group(3)
    if den != total:
        return [f"⚠ 拒答准确率分母 {den} != 拒答题数 {total} —— 这两个不该不等"]
    if rd.exists():
        m2 = re.search(r"拒答准确率掉到\s*(\d+)/(\d+)", rd.read_text(encoding="utf-8"))
        if not m2:
            return [
                "✗ 离线层拒答准确率：README 里找不到「拒答准确率掉到 x/y」这句 —— "
                "可能改过说法（这本身就要看一眼），也可能真丢了"
            ]
        if f"{m2.group(1)}/{m2.group(2)}" != f"{ok}/{den}":
            return [
                f"✗ 离线层拒答准确率：README 写 {m2.group(1)}/{m2.group(2)}，"
                f"产物是 {ok}/{den}"
            ]
    return []


def _check_recall() -> list[str]:
    """★ 洞3 修复（cc + DSH 独立同时抓到）：原版只读产物侧，
    README 里的 15/15 改成 12/15 时脚本报"✓ 通过" —— 因为它
    拿产物和【硬编码字面量】比，从不打开 README.md。
    ⇒ 现在必须两边都查。
    """
    errs: list[str] = []
    p = REPO_ROOT / "evalset/report-vector.md"
    rd = REPO_ROOT / "README.md"
    if not p.exists():
        return ["✗ 应答题 Recall@5：找不到 evalset/report-vector.md"]
    m = re.search(r"应答题数：(\d+)\s*Recall@5：(\d+)/(\d+)", p.read_text(encoding="utf-8"))
    if not m:
        return ["✗ 应答题 Recall@5：正则没匹配到，产物格式可能变了"]
    total, ok, den = m.group(1), m.group(2), m.group(3)
    if f"{ok}/{den}" != "15/15":
        return [f"✗ 应答题 Recall@5：产物是 {ok}/{den}，文档里写 15/15"]
    if den != total:
        errs.append(f"⚠ Recall@5 分母 {den} != 应答题数 {total}")
    if rd.exists():
        hits = re.findall(r"(\d+)/(\d+)", rd.read_text(encoding="utf-8"))
        pairs = {f"{a}/{b}" for a, b in hits}
        if "15/15" not in pairs:
            errs.append(
                "✗ 应答题 Recall@5：README 里找不到任何 x/15 形式且等于 15/15 的表述"
                "（可能已被改成别的数）"
            )
    return errs


def _check_scale_ranks() -> list[str]:
    """★ 洞4 修复（DSH 指出的静默降级）：原版在文件不存在或正则不中时
    `return []`，于是删掉/改名实验记录、或把"中位排名"措辞改掉，
    都会得到 "✓ 通过"。⇒ 抽不到一律算错。

    ★★ 第二个坑（我自己撞上的）：同一事实在这个文件里有**多种写法**
       （「中位排名 1 → 2 → 4」「中位排名 1→2→4」「中位排名 1→4」），
       re.search 只取第一处 ⇒ 改掉第一处后还能从别处匹配上，
       变异测试看起来"漏检"，其实是文档本身有冗余表述。
       ⇒ 现在扫【全文所有】出现处，且要求【每一处都一致】。
    """
    p = REPO_ROOT / "experiments/_语料规模与多查价值实验.md"
    if not p.exists():
        return [
            "✗ gold 中位排名：找不到 experiments/_语料规模与多查价值实验.md —— "
            "★原版在这里静默返回空列表，等于这一格检查被悄悄关掉了"
        ]
    text = p.read_text(encoding="utf-8")
    seq_pat = re.compile(r"中位排名\s*(\d)\s*[→\-—]+\s*(\d)\s*[→\-—]+\s*(\d)")
    hits = seq_pat.findall(text)
    if not hits:
        return [
            "✗ gold 中位排名：实验记录里抽不到任何「中位排名 x → y → z」—— "
            "★措辞变了要说出来，不要静默当通过（原版就是这里坏的）"
        ]
    bad = ["/".join(h) for h in hits if "/".join(h) != "1/2/4"]
    if bad:
        return [
            f"✗ gold 中位排名：实验记录里有和 1/2/4 不一致的写法{sorted(set(bad))}"
            " —— 同一事实有多处表述且互相矛盾，以哪处为准？"
        ]
    return []


# ---------------------------------------------------------------
# ★ 待办陈述的过期检查（2026-10-10，同类问题第 4 次）
# ---------------------------------------------------------------
# 病根不是"忘了改"，是**没人告诉写文档的人它过期了**：
#   probe_ablation.py 的 docstring / CLAUDE.md 的虚构历史 / probe_threshold.py 的 268，
#   第四次是〈还没做的〉里"拒答集只有 5 条"，而那时其实已经补到 61 道 ——
#   留着它等于**把自己已经补的短板写成还没补**。
# ⇒ 原来的对账只管"数字对不对"，不管"这条待办还成不成立"。
#   这里补上：**每条待办配一个可现算的完成判据**，达标了却还挂着 → 报错。
#   ⚠️ 匹配式必须带"未完成"的语气词（只有/仅/需/要/尚未/待），
#     否则将来写"曾计划 n≥60，已于 X 完成"这种历史叙述会被误伤。
# ★ 待办登记表（第二版：从「点检查」升级成「面检查」）
#
# 第一版只查"登记过的待办有没有过期"——**它能查的只有我想到的**：
# 以后新写一条待办如果没登记，检查器看不见它。而"看不见"正是这一系列问题的源头
# （用户的原话：不是忘了改，是**没人告诉它过期了**）。
# ⇒ 现在〈还没做的〉表里**每一行都必须在这里登记**，否则报错 ——
#   "登记"从靠记性变成强制的。
#
# kind：
#   counted —— 现算一个数量，≥ done_at 算完成
#   file    —— 某个文件存在算完成
#   manual  —— 不可机械判定；**必须**写 why（为什么判不了）+ how（人工怎么确认），
#              且每次运行都把它们打成提示：数量涨了说明有人在拿 manual 逃避登记
TODO_REGISTRY = [
    {
        "row": r"拒答集",
        "label": "拒答集扩到 n≥60",
        "kind": "counted",
        "actual": lambda: _count_questions("refuse"),
        "done_at": 60,
        "pending_re": r"(?:只有|仅有?)\s*\d+\s*条|n\s*[≥>=]+\s*60",
        "fix": "把那条待办删掉，并在评测一节写上实测值（RESULTS.md 有全部数字）",
    },
    {
        "row": r"GIF|录一段",
        "label": "录一段 GIF 放 README 顶部",
        "kind": "file",
        "path": "docs/images/demo.gif",
        "done_at": 1,
        "pending_re": r"(?:要|需|应|尚未|待)[^\n]{0,20}录[^\n]{0,12}GIF",
        "fix": "动图已存在（docs/images/demo.gif），把那条待办标成已完成",
    },
    {
        "row": r"Dockerfile",
        "label": "补 Dockerfile",
        "kind": "file",
        "path": "Dockerfile",
        "done_at": 1,
        "pending_re": r"Dockerfile",
        "fix": "Dockerfile 已存在，把〈还没做的〉里那条删掉",
    },
    {
        "row": r"部署",
        "label": "部署到线上",
        "kind": "manual",
        "why": "部署状态不在本机可判 —— 「有没有线上地址」这件事本身要外部确认",
        "how": "README 里有没有一个**可点开**的线上地址",
    },
    {
        "row": r"粗捞池",
        "label": "粗捞池只有 5 条",
        "kind": "manual",
        "why": "这条不是待办而是**已决定不做**（见〈敏感性实验〉那节的对照表），没有「完成」态",
        "how": "确认那段决策记录还在，且与 `search(k=5)` 的现状一致",
    },
    {
        "row": r"超时",
        "label": "超时只能不等、不能掐断",
        "kind": "manual",
        "why": "异步取消是否实现，静态判不出来（要看运行时行为）",
        "how": "`service.py::_invoke_llm` 是否仍是「线程池 + future.result(timeout=)」",
    },
    {
        "row": r"CI 红了不拦合并",
        "label": "CI 红了不拦合并（知情取舍，等触发条件）",
        "kind": "manual",
        "why": "它不是「忘了做」，而是【决定暂时不做】—— 触发条件写在表里了："
               "「CI 红了却被人合进去」真的发生过一次。而那件事发生在 GitHub 上"
               "（branch rules 的配置 + 有没有人绕过红灯合并），本机静态判不出来。",
        "how": "看 GitHub 的 Settings → Rules（新版 rulesets）里 "
               "`Require status checks to pass` 是不是【仍未勾选】；"
               "并确认 README 那条触发条件还在、没被悄悄改掉或删掉。",
    },
]


def _todo_section() -> str | None:
    """抠出 README 的〈还没做的〉那一节。

    ★ 先剥掉 `~~删除线~~`：Markdown 里删除线就是「已作废」的记号，
      常被用来**留档旧说法**（「~~拒答集只有 5 条~~ → 已扩到 61 道」）。
      不剥的话，那句留档本身会被判成待办还在 —— 第一版就是这样，
      检查器报了它自己刚生成的留档。
    """
    rd = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    rd = re.sub(r"~~.*?~~", " ", rd, flags=re.S)
    m = re.search(r"^## 还没做的.*?(?=^## |\Z)", rd, re.S | re.M)
    return m.group(0) if m else None


def _todo_rows(seg: str) -> list[str]:
    """表里每一行的第一格（缺口名）。表头与 `|---|---|` 分隔行跳过。"""
    rows = []
    for line in seg.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not cells or set(cells[0]) <= set("-: "):
            continue
        if cells[0] == "缺口":
            continue
        rows.append(cells[0].replace("**", ""))
    return rows


def _check_todo_registry_complete() -> tuple[list[str], list[str]]:
    """★ 元检查：〈还没做的〉里**每一行**都必须在 TODO_REGISTRY 里登记。

    不登记 = 这台检查器看不见它，以后它过期了也没人知道 —— 而那正是这一系列问题的病根。
    """
    seg = _todo_section()
    if seg is None:
        return ([ "✗ 待办登记检查：README 里找不到「## 还没做的」这一节 —— "
                  "这一族检查失去作用对象，请同步更新本脚本"], [])
    errs: list[str] = []
    manual: list[str] = []
    for row in _todo_rows(seg):
        hit = [t for t in TODO_REGISTRY if re.search(t["row"], row)]
        if not hit:
            errs.append(
                f"✗ 待办没登记：「{row}」在〈还没做的〉表里，但 TODO_REGISTRY 里没有对应条目。"
                f"要么给它一个可现算的完成判据（kind=counted/file），"
                f"要么声明 kind=manual 并写清「为什么不可机械判定 / 人工怎么确认」。"
                f"—— 不登记 = 检查器看不见它，以后它过期了也没人知道")
        for t in hit:
            if t["kind"] == "manual":
                if not t.get("why", "").strip() or not t.get("how", "").strip():
                    errs.append(f"✗ 待办登记不完整：「{t['label']}」标成 manual，"
                                f"但 why / how 有空项 —— 那等于没登记")
                else:
                    manual.append(t["label"])
    warns = []
    if manual:
        warns.append(f"{len(manual)} 条待办是**人工判定**的（不可机械判定）："
                     + "、".join(manual)
                     + " —— ★ 这个数量涨了，通常说明有人在拿 manual 逃避登记")
    return errs, warns


def _count_questions(kind: str) -> int:
    """数 held-out 出题集里某种题型的条数（现算，不抄README）。"""
    import json
    p = REPO_ROOT / "evalset" / "heldout" / "questions.json"
    if not p.exists():
        return 0
    data = json.loads(p.read_text(encoding="utf-8"))
    qs = data.get("questions") if isinstance(data, dict) else data
    return sum(1 for q in qs if q.get("type") == kind)


# ---------------------------------------------------------------
# ★★ STALE_TEXT：文档说某样东西「不存在/还是空/没做」，但它其实已经存在了
# ---------------------------------------------------------------
# 这类过期陈述**前两代检查都抓不到**：
#   - 待办表检查只看〈还没做的〉那一节⇒ 〈自评局限〉里的"held-out 目前仍是
#     空壳"根本不在扫描范围内（它当时真的还是空壳，后来填到 105 题了）
#   - 数字对账只认数字⇒「仍是空壳」里没有一个数字
#   而这一类恰恰最危险：它出现在**自我批评**那一节，面试官最会盯那里，
#   写着"我的评测集还是空的"会让人以为你没做。
#
# 判据：**文档里的某个说法** + **一个能现算的事实** → 两者矛盾就报错。
STALE_TEXT_RULES: list[dict] = [
    {
        "label": "held-out 出题集",
        # ⚠️盲审实测这组写法都漏，所以扩了词表与锚点：
        #   原版只认"锚点在短语前"的顺句，且不认大小写/空格变体。
        #   ⇒ 锚点两侧都试（`held-?out` 前后可换位），
        #     字符类里加了空格与连字符变体（`held[\s-]?out`）。
        #   ⚠️ 仍然抓不到跨句号/换行的改写 —— 已知漏检，不假装能抓。
        "stale_re": r"held[\s-]?out[^。\n]{0,40}?(?:仍?是(?:一片)?(?:空壳|空白|空的)|尚未|还没(?:有题|动手)|暂无)"
                    r"|(?:仍?是(?:一片)?(?:空壳|空白|空的)|尚未|还没(?:有题|动手))[^\n]{0,40}?held[\s-]?out",
        # ★ 排除语气：只要句子里出现"已/后来/现在"这类**转折或完成**标记，
        #   那就是历史叙述或留档，不是"现在还是空的"。
        #   盲审实测 A 类（"held-out 在 2026-09 之前是空壳，现在已有 105 题"）
        #   会被误报—— 靠这一条排除。
        # ⚠️ 这仍不是"上一代检查那种严格语气词表"，只是最粗的一层。
        #   真正的解法是要求"这一句本身就是当前状态的断言"，
        #   但那需要语义判断 —— 机械做不到，所以只做到这层。
        "not_if_re": r"(?:已(?:经)?(?:补|扩|填|做完)|后来|现已|现在已有|→\s*已|已补到|已完成)",
        "why_false": "evalset/heldout/questions.json 里已经有题了",
        "actual": lambda: _count_questions("answer") + _count_questions("refuse"),
        "min": 1,
    },
    # ⚠️ 这里本来还有第二条（查 LLM 查询改写有没有被漏写），**已删**。
    #   删的理由：那条规则写成"必须有某关键词才不报"之后，就永远不会触发 ——
    #   一条永远不会响的检查比没有更坏，它只会在报告里制造"我查过了"的错觉。
    #   同源教训：loader 清洗账塌成 0、对账脚本第一版假绿，都是"探头只照一面"。
    #   ★ 要加这类检查，必须先配一条**能被检出**的变异测试；
    #     配不出来的，说明这个洞还没想清楚，别急着写检查。
]


def _check_stale_text() -> tuple[list[str], list[str]]:
    """★ 文档声称某样东西不存在/还是空的，但它其实已经在了。

    ⚠⚠ 2026-10-11 盲审后**降级为警告**，理由写在下面 —— 它抓不到漏的，
    却会报不假的错。一个爱报假错的检查会被当成噪音关掉，那比不检查更坏。

    盲审（cc 9 条 / DSH 8 条，5 处独立重合）实测出的问题：
      · **假阳性**：把那句话划删除线留档（仓库自己的标准做法）
        → 照报；写"以前是空壳，现在已补到 105 题"这种**正确**的历史叙述
        → 也照报。同脚本的 `_todo_section` 专门剥`~~…~~`，理由就是同一个。
      · **漏检**：短语在锚点前 / `Held-Out` 大写 / `held out` 空格 /
        跨句号 —— 四种写法一个都不抓。
      · **探针可静默关掉**：`questions.json` 一删，n=0 < min，检查变绿。
      · **报错信息里的数字是抄的**：题数变了它自己先过期。
      · ★★ **变异与规则同源**（DSH 第 8 条，最要命）：
        变异替换成的那句话，正是正则照着设计时用的那句话
        ⇒ "1/1 检出"几乎不携带信息量。这条检查当初是照着"能响"的标准建的，
        但那只是自证。

    ⇒ 所以现在它只**提示**、不拦提交。要变成拦提交的那类，
      必须先补上【负向变异】（"应当保持绿"的那几个：删除线留档、历史叙述）
      和【同源变异】（改写法而不是改回原句）。那两条配不出来之前，
      它不该有否决权。
    """
    rd_path = REPO_ROOT / "README.md"
    if not rd_path.exists():
        return [], []
    rd_raw = rd_path.read_text(encoding="utf-8")

    # ★ 剥删除线：与 _todo_section 同款处理。
    #   不剥的话，"把旧说法划掉留档"这个仓库自己的标准做法会被当成过期陈述。
    rd = re.sub(r"~~.*?~~", " ", rd_raw, flags=re.S)

    warns: list[str] = []
    for rule in STALE_TEXT_RULES:
        m = re.search(rule["stale_re"], rd, flags=re.I)   # ★ 大小写变体也算
        if not m:
            continue
        # ★ 语气排除：命中"已完成/后来/现在已有"→ 是历史叙述或留档，不是过期陈述
        #   拿整句（取命中所在那一行）去判，而不是只看命中片段 ——
        #   "现在已有 105 题"往往就在同句的后半段，片段里看不到。
        line_start = rd.rfind("\n", 0, m.start()) + 1
        line_end = rd.find("\n", m.end())
        line = rd[line_start : line_end if line_end != -1 else len(rd)]
        if "not_if_re" in rule and re.search(rule["not_if_re"], line, flags=re.I):
            continue
        try:
            n = rule["actual"]()
        except Exception as e:
            warns.append(f"⚠ {rule['label']}：判据执行失败（{e!r}），这条提示本轮没生效")
            continue
        if n >= rule["min"]:
            snippet = m.group(0)[:50].replace("\n", " ")
            # ★ 报错信息里的数字现算，不抄 —— 抄的话它自己会先过期
            warns.append(
                f"· 疑似过期陈述：README 写着「{snippet}」，"
                f"但现算实情是{rule['why_false']}（现算值 {n}）。"
                f"请看一眼是不是留档旧说法；若确实是过期陈述就改掉。"
            )
    return [], warns


def _check_todo_claims() -> list[str]:
    """登记过的待办：达标了却还挂在表里 → 报错。"""
    seg = _todo_section()
    if seg is None:
        return []            # 「那一节没了」由 _check_todo_registry_complete 负责报
    rows = _todo_rows(seg)
    errs = []
    for t in TODO_REGISTRY:
        if t["kind"] == "manual":
            continue
        n = (t["actual"]() if t["kind"] == "counted"
             else (1 if (REPO_ROOT / t["path"]).exists() else 0))
        if n < t.get("done_at", 1):
            continue
        for row in rows:
            if re.search(t["row"], row) and re.search(t["pending_re"], row):
                errs.append(
                    f"✗ 待办陈述过期：「{t['label']}」还挂在〈还没做的〉里，"
                    f"但实测已经 {n}（完成判据 ≥{t.get('done_at', 1)}）—— {t['fix']}")
    return errs


def _check_completed_list_metrics() -> list[str]:
    """★「下一步的顺序」里那些 ✅ 已完成 的条目，引用的必须是**当前**指标。

    实测起因：那条"20 题小评测集"写着"离线层 Recall@5 13/14、漏答 0/14、
    拒答准确率 5/6"—— 全是换语料前的旧值，而同一份 README 的评测表上写着 15/15。
    一份文档里两个自相矛盾的当前值，读者只会信那个好看的。
    ⇒ 凡是出现在这一段里的 x/y，都拿 report-vector.md 现算的值去对。
    """
    rd = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    rep = (REPO_ROOT / "evalset" / "report-vector.md").read_text(encoding="utf-8")
    m = re.search(r"^### 下一步的顺序(.*?)(?=^### |^## |\Z)", rd, re.S | re.M)
    if not m:
        # ★ 不静默返回 [] —— 那一节改名/删掉时，这条检查会悄悄失效，
        #   而"看起来通过"比"报错"危险得多（隔壁 _check_scale_ranks 同款教训）。
        return ["✗ 已完成清单的指标检查：README 里找不到「### 下一步的顺序」这一节"
                "（改名了？删了？）—— 这条检查失去作用对象，请同步更新本脚本"]
    seg = m.group(1)

    def cur(pat: str) -> str | None:
        mm = re.search(pat, rep)
        return f"{mm.group(1)}/{mm.group(2)}" if mm else None

    now = {
        "Recall@5": cur(r"应答题数：\d+\s*Recall@5：(\d+)/(\d+)"),
        "漏答": cur(r"漏答率：(\d+)/(\d+)"),
        "拒答准确率": cur(r"拒答题数：\d+\s*拒答准确率：(\d+)/(\d+)"),
    }
    errs = []
    # ★ 数字外面通常包着 Markdown 粗体（`Recall@5 **15/15**`），
    #   所以强调符必须允许 —— 第一版没允许，结果**一条都匹配不到**、检查恒绿，
    #   是变异测试（把 15/15 改回 13/14）把它抓出来的。
    for label, pat in (("Recall@5", r"Recall@5\s*\**\s*(\d+/\d+)"),
                       ("漏答", r"漏答\s*\**\s*(\d+/\d+)"),
                       ("拒答准确率", r"拒答准确率\s*\**\s*(\d+/\d+)")):
        for got in re.findall(pat, seg):
            if now[label] and got != now[label]:
                errs.append(
                    f"✗ 已完成清单里的 {label} 是旧值：写的是 {got}，"
                    f"report-vector.md 现在是 {now[label]} —— 改成当前值，"
                    f"或注明那是哪一轮的历史数字")
    return errs


def check() -> tuple[list[str], list[str]]:
    """返回 (errors, warns)。有 errors 就非零退出。"""
    errors: list[str] = []
    warns: list[str] = []

    errors += _check_refuse_rate()
    errors += _check_todo_claims()
    _e, _w = _check_todo_registry_complete()
    errors += _e
    warns += _w
    errors += _check_completed_list_metrics()
    errors += _check_recall()
    errors += _check_scale_ranks()
    # ★ 过期陈述（文档说某物不存在，但它其实在）——
    #   ⚠️ 只提示不拦提交，理由见 _check_stale_text 的 docstring（盲审后降级：
    #     变异与规则同源 ⇒"能响"是自证；且有实测假阳性）
    _e, _w = _check_stale_text()
    errors += _e
    warns += _w
    # ★★ 探头自检：规则列表为空 = 这一族整个没跑
    if not STALE_TEXT_RULES:
        errors.append("⚠⚠【探头失灵】STALE_TEXT_RULES 是空的 —— "
                      "「过期陈述」这一族本轮完全没执行")

    # 通用断言：产物侧现算
    for a in ASSERTIONS:
        actual = a.fetch_actual()
        if actual is None:
            msg = f"✗ {a.name}：从 {a.src} 里抽不到数字（文件不在？正则过期？）"
            (errors if a.expect else warns).append(msg)
            continue
        if a.expect and a.expect != actual:
            errors.append(f"✗ {a.name}：README 写 {a.expect}，{a.src} 里是 {actual}")
        # ★ 同一份产物里同一句话出现多次、值不一样 ⇒ 产物自相矛盾，报错
        if a._rest and any(v != actual for v in a._rest):
            errors.append(
                f"✗ {a.name}：{a.src} 里有多处匹配但值不一致 —— "
                f"首处 {actual}，其余 {sorted(set(a._rest))}。以哪处为准？"
            )

    # ★★ README 侧：把文档里写的那个数抠出来，和产物比
    #   （第一版缺这一段，变异测试把README 改错却报"通过"）
    readme_compared = 0
    for a in ASSERTIONS:
        if a.readme_pattern is None:
            continue
        got = a.fetch_readme()
        if got is None:
            errors.append(
                f"✗ {a.name}：README 里找不到对应文字"
                f"（正则 {a.readme_pattern!r} 抽不到）—— 可能是文档改了说法，"
                f"也可能正则过期了。两种都得看一眼。"
            )
            continue
        actual = a.fetch_actual()
        if actual is None:
            # ★ 原版在这里 continue，但计数已在上面 +1 ⇒ 那一格根本没比，
            #   却算作"已查"。移到这里之后才+1。
            errors.append(
                f"✗ {a.name}：README 侧找到了文字，但产物侧抽不到数"
                f"（{a.src}）—— 这一格没法比，不能算通过"
            )
            continue
        readme_compared += 1
        # ★ 按段比，不按整串比。
        #   实测踩过：README 写 "1 → 2 → 4"，产物拼出来是 "1/2/4"，
        #   直接整串比会报假错—— 而一个爱报假错的检查会被当成噪音关掉。
        #   分隔符差异（空格 / → / /）不算错，数字顺序和值才算。
        def norm(s: str) -> list[str]:
            return [x for x in re.split(r"\s*(?:→|/)\s*", s) if x]

        r_parts, a_parts = norm(got), norm(actual)
        if r_parts == a_parts:
            pass
        else:
            errors.append(
                f"✗ {a.name}：README 上写的是「{got}」，产物里是「{actual}」"
            )

    # ★★ 探头自检：README 侧到底【真比了】几条？
    #   ★修：原来在"找到 README 文本"时就 +1，产物侧抽不到的 continue 发生在它之后
    #     ⇒ 那一格根本没比，却算作"已查"。DSH 抓到的静默降级就是这么漏过去的。
    #   现在只在真正做过比较之后才 +1。
    if readme_compared == 0:
        errors.append(
            "⚠⚠ 【探头失灵】本次【没有真比对过】任何一条 README 侧断言，"
            "「对账通过」不能代表文档没问题。"
            "检查 readme_pattern 是不是被清空了，或产物侧是不是抽不到数。"
        )

    # ★★ 跨尺子配对专项（DSH 指出的洞：这个检查抓不到它声称要抓的东西）
    #   原始事故：同一格里「8 块」配 4195 字、「5241 字」配 11 块 ——
    #   **两个丢字数不相等**，所以旧版"相等才提示"完全零反应。
    #   ⇒ 改成：把两个数并排打出来，并且要求文档里凡出现丢字数处必须带口径标注。
    p_src = REPO_ROOT / "experiments/clean_corpus.py"
    p_now = REPO_ROOT / "README.md"
    if p_now.exists():
        rd = p_now.read_text(encoding="utf-8")
        pre_words = None
        if p_src.exists():
            m1 = re.search(r"合计要丢\s*(\d+)\s*字", p_src.read_text(encoding="utf-8"))
            pre_words = m1.group(1) if m1 else None
        m2 = re.search(r"截断丢字\s*(\d+)\s*块\s*(\d+)\s*字", rd)
        if pre_words and m2:
            post_blocks, post_words = m2.group(1), m2.group(2)
            warns.append(
                f"· 两把尺子并排：清洗前【{pre_words} 字】/ 入库后【{post_blocks} 块 {post_words} 字】"
                "—— 这是两次不同口径，引用时必须写清是哪把"
            )
            # 文档里出现"丢 N 字"却没有口径标注 ⇒ 报错（这才是原始事故的形状）
            for m3 in re.finditer(r"(\d+)\s*字", rd):
                if m3.group(1) != pre_words:
                    continue
                seg_start = max(0, m3.start() - 260)
                seg = rd[seg_start : m3.end() + 120]
                has_note = any(
                    k in seg
                    for k in ("清洗前", "源语料", "入库后", "口径", "clean_corpus", "体检")
                )
                if not has_note:
                    errors.append(
                        f"✗ 丢字数 {pre_words} 字在文档里出现，但【周围没有口径标注】。"
                        "原始事故就是这一格把清洗前和入库后两把尺子混在一起写了。"
                        "请补「清洗前源语料口径 / 入库后体检口径」这类说明。"
                    )

    return errors, warns


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", action="store_true", help="顺便打印实测值")
    args = ap.parse_args()

    errors, warns = check()

    if args.show:
        print("实测值（脚本现算，不抄 README）：")
        for a in ASSERTIONS:
            print(f"  {a.name:38s} ← {a.src}  ⇒{a.fetch_actual() or '抽不到'}")
        print()

    if warns:
        print("提示（不算错）：")
        for w in warns:
            print(f"  {w}")
        print()

    if errors:
        print("数字对账失败：")
        for e in errors:
            print(f"  {e}")
        print()
        print("★★ 一份骗人的体检报告比没有体检更坏 —— 别只改数字，")
        print("   同时问「这两把尺子是不是同一把」（参见 CLAUDE.md 协作黑板一节）。")
        return 1

    print("✓ 数字对账通过：README 的数字与落盘产物一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())