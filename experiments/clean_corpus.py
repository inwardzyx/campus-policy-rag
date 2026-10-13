# -*- coding: utf-8 -*-
"""experiments/clean_corpus.py —— 把官网 PDF 转存稿清洗成 kb/loader.py 认的格式

**为什么需要这个脚本（不是"为了整齐"）：**

这批语料的原始形态是 PDF 转存稿，直接扔进 `docs/kb/` 会被 loader 全部拒收
（`kb/loader.py:173` 那个"一个 `##` 都没切出来"的分支 → 整篇进 errors 静默蒸发）。
而手工改 9 份 233 条条文 = 233 次复制粘贴，**抄错的概率比写脚本高得多**。

实测这批语料有四个坑（规模已量）：

    ① 跨页标记 `— 1 —`   6 份文件共 44 处
       危险在哪：它插在条文中间，会把【一个条款劈成两个块】，
       两半都变短 → 可能双双躲过"块太短"和"截断"两道关，静默入库。
    ② 表格被拍平         综合测评/专项补助的表格整段散成单行
    ③ 中文数字间多余空格  `第 一条` / `10000 元` / `占 30%`
    ④ 无元信息、无 `##`   loader 三个必填字段都没有

★ 本脚本的两条自我证成判据（不靠"看起来对"）：
    (1) 清洗后【块数必须等于该文件的条文数】（从原文逐条数出来的）。
        对不上就退出报错 —— 宁可不出结果，也不交一份"看着挺整齐但少了几条"的东西。
    （2）超长块该不该切，交给项目自带的 `scripts/measure_chunk_health.py::grade_truncation`
        判，**本脚本不另写一套判据**（loader.py:204 明确要求判据只在一处定义）。
        实测结论 = "当天上"（单块丢 1470 字 ≥ SPLIT_TRIGGER_DROP=100）。

★ 切分器为什么必须存在（不是过度设计）：
    `bge-small-zh-v1.5` 只有 512 token。max_chars=400 字贴着这个上限设，
    **调大它没用** —— 超过 400 字的内容 embedding 会静默吃掉尾巴，
    比截断更隐蔽（loader.py:209 自己写了这条）。
    而这批语料实测有 11 块 ≥400 字，合计要丢 5241 字（占总块 11 块 / 全库原文 22%）。
    两条路都堵 ⇒ 唯一正确的解是切分。

★ 切分遵守 `grade_truncation` 的反向判据③：
    "< max_chars 的块一个都不许切" —— 现在的块边界正好是条款边界，
    整条在一起；切了会把"按旷课论处"和"并记入学生档案"拆到两块，白丢上下文。
    所以本切分器【只处理 ≥ max_chars 的块】，小的原样放过。

★ 切分器：默认【关闭】，且实测证明这条路在这批语料上走不通 —— 结论留档
   ============================================================
   项目判据 `grade_truncation` 判的是 level="当天上"（单块丢 1470 字 ≥ 100），
   照它的结论"该上切分器"。但真写出来试了三种判据，**每一种都失败**：

     ① 按"（一）（二）"切  → 第十条切成 16 块，最小 19 字（"侮辱、诽谤或恐吓他人，
        给予警告及以上处分。"）—— 孤句脱离上下文检索不到，等于白切。
     ② 加"最小块 150 字"  → 碎块是没了，但超长块从 2 涨回 9，只省 285 字（5%）。
     ③ 按"表格行边界"切    → 碎块又回来：269 块里 53 块 <40 字、中位块长 74 字。

   ⇒ **根因**：这批是 PDF 转存稿，条款正文和表格混在一起，
     **没有一个能同时满足"够大"和"在语义边界上"的切分点**。
     调的是同一个旋钮的两端，不是两个独立旋钮。

   ⇒ **最终决定：默认不切（`--split` 才启用），原样入库 + 接受 max_chars 截断，
     丢字由 grade_truncation 记账报警。** 三条理由：
       1. 调大 max_chars 是死路：bge-small-zh-v1.5 只有 512 token，400 字贴着上限，
          超出的部分 embedding 会【静默吃掉尾巴】—— 比截断更隐蔽（loader.py:209）。
       2. 切碎的块比被截的长条款更糟：被截的至少主干还在、条款号还对；
          15 字的孤句连"这是规定"都读不出来，评测集 gold 也对不上。
       3. 丢了多少是可查的：超长块丢字数由 grade_truncation 记成"该上/记账"，
          **不会静默** —— 这正是"清洗不能静默"的纪律。

★ 保留 `--split` 分支不是为了将来用，是为了留一个"试过了、失败了"的记录：
   下一个想加切分器的人不用再试一遍这三种判据。

★ 刻意不做的事：
    - 不重建被拍平的表格 —— 手上没有原始行列信息，凭空排出来的是我编的，不是原文
    - 不覆盖 docs/kb/ 下的同名文件 —— 撞名就跳过并报告
    - 不给 15921 建新文件     —— 它和仓库现有那份同源，让查重关卡自己去判

跑法（仓库根目录）：
    D:\\Python-project\\.venv\\Scripts\\python.exe experiments/clean_corpus.py            # 干跑，只报统计
    D:\\Python-project\\.venv\\Scripts\\python.exe experiments/clean_corpus.py --write   # 真写到 docs/kb/
    D:\\Python-project\\.venv\\Scripts\\python.exe experiments/clean_corpus.py --write --only 127861
"""
from __future__ import annotations

import argparse
import importlib.util
import re
from pathlib import Path
import sys
# ★ print 里的 ⇒/✓/⚠ 在 cp936 下编不出来（stdout 是管道时）⇒ 崩在打印上。本项目已犯 7 次。
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = Path(r"D:\WorkBuddy\2026-10-09-14-50-48\corpus_final")
DST_DIR = REPO_ROOT / "docs" / "kb"

# ---------------------------------------------------------------- 清洗规则

# ① 跨页标记：整行只有 "— 1 —" / "－1－" 这类。
#    收紧到【整行匹配】而不是 find：正文里可能出现"第 1 —"这种正常表述。
PAGE_MARK = re.compile(r"(?m)^[ \t]*[—–\-－][ \t]*\d{1,3}[ \t]*[—–\-－][ \t]*$")

# ② 条文：行首"第X条"。
CLAUSE = re.compile(r"(?m)^[ \t]*(第[一二三四五六七八九十]{1,3}条)")

# ③ 元信息里的 URL 要抄进 source，所以头部几行先提出来。
META_LINE = re.compile(r"^-\s*(发布时间|来源|适用范围)\s*[：:]\s*(.+?)\s*$")

# ④ 清理 PDF 转存留下的多余空格。只在【安全的三种位置】去，绝不全文替换：
#      (a) 汉字/数字之间的单空格   "第 一条" → "第一条"
#      (b) 数字与"元/分/年/级"之间 "10000 元" → "10000元"
#      (c) 中文标点与汉字之间       "规定， 本规定" → "规定，本规定"
#    ★ 为什么不全去：全文去空格会把 "第一章 总则" 压成 "第一章总则"、
#      词内空格也吃掉，反而造出比原文更差的文本。宁可留空格，不可改语义。
_CJK = r"\u4e00-\u9fff"
_RULES = [
    (re.compile(rf"(?<=[{_CJK}0-9])[ \t]+(?=[{_CJK}0-9])"), ""),
    (re.compile(r"(?<=\d)[ \t]+(?=元|分|级|年|人|次|项|条|款|章)"), ""),
    (re.compile(r"(?<=[，。；：、）】》])[ \t]+"), ""),
    (re.compile(r"[ \t]+(?=[，。；：、（【《])"), ""),
]


def clean_spaces(text: str) -> str:
    for pat, rep in _RULES:
        text = pat.sub(rep, text)
    return text


# ---------------------------------------------------------------- 切分

# ⚠️ 以下函数默认【不被调用】。实测结论见文件顶部"切分器：默认关闭"一节：
#    三种判据都试过，每一种都会产出 15-19 字的碎块或几乎不省字。
#    保留实现是为了留档，不是为了将来启用。

# 切分锚点，按优先级排列。★ 全部要求【行首】——句中的"（二）"可能是内容不是分项。
#    ⚠️ 只用后两种，不用"（一）"这种单分项序号当锚点 —— 实测教训见下。
# ---------------------------------------------------------------- 切分
#
# ⚠️ 以下切分实现默认【不被调用】。实测结论见下面那一大段留档：
#    三种判据都试过，每一种都会产出 15-19 字的碎块或几乎不省字。
#    保留实现是为了留档，不是为了将来启用。

# 切分锚点（仅 --split 时启用）。★ 全部要求【行首】。
_ANCHORS = [
    re.compile(r"(?m)^[ \t]*\d{1,2}[.、](?!\d)"),      # 数字序号（表格行）
    re.compile(r"\n[ \t]*\n[ \t]*\n"),                # 连续空行分段
]

# "能不能切"的判据：必须有足够多的行边界可切（表格类），连续叙述的一刀切下去就断句
_MIN_LINES = 8
_MIN_SHORT_RATIO = 0.5
_SHORT_LINE = 40


def split_long(clause: str, content: str, max_chars: int) -> list[tuple[str, str]]:
    """把超长条款切成多块。**只处理 ≥ max_chars 的**，小的原样返回（反向判据③）。

    返回 [(新块标题, 内容)]。标题沿用原条款号 + "-续N"，
    ★ 保留"第X条"这个信息很重要：gold 字段靠它判分，
    切成"第十条-续1"之后 `gold` 仍然能对上，但不能丢 entirely ——
    一丢，评测集里所有指向这条的题都会变成 MISS。

    ★ 选锚点的判据不是"切得均匀"，是"切完每块都能独立被检索"：
      候选里挑【最短块最大】的那个（maximin）。早先按"最差块最小"挑，
      挑出的是最碎的切法 —— 16 块里 5 块不足 25 字。
    """
    if len(content) < max_chars:
        return [(clause, content)]

    # ★ 先判"能不能切"：必须有足够多的行边界可切（表格类），连续叙述的一刀切下去就断句
    lines = [l for l in content.split("\n") if l.strip()]
    if len(lines) < _MIN_LINES:
        return [(clause, content)]
    short_ratio = sum(1 for l in lines if len(l) < _SHORT_LINE) / len(lines)
    if short_ratio < _MIN_SHORT_RATIO:
        return [(clause, content)]

    best = None
    for pat in _ANCHORS:
        parts = [p.strip() for p in pat.split(content) if p and p.strip()]
        if len(parts) < 2:
            continue                      # 切不动（只切出 1 块）＝ 锚点不适用
        # ★ 挑"最短块最大"的那个（maximin）—— 切分的目的是每块都能独立被检索，
        #   不是切得均匀。早先挑"最差块最小"，挑出的是最碎的切法。
        worst = max(len(p) for p in parts)
        if worst >= max_chars:
            continue                      # 切完还是有超长块 → 这个锚点不够用
        shortest = min(len(p) for p in parts)
        if best is None or shortest > best[0]:
            best = (shortest, parts)

    if best is None:
        # 切不动就【不切】，原样交出去让 loader 截断并报警。
        # ★ 不硬凑：硬凑出来的"切分"会切在半句话上，比截断更糟。
        return [(clause, content)]

    _, parts = best
    out = []
    for i, p in enumerate(parts, 1):
        title = clause if i == 1 else f"{clause}-续{i}"
        out.append((title, p))
    return out


# ---------------------------------------------------------------- 解析

def parse_head(raw: str) -> tuple[str, dict]:
    """取文件头部的元信息。返回 (标题, {发布时间/来源/适用范围})。"""
    title, meta = "", {}
    for line in raw.splitlines()[:8]:
        line = line.replace("\r", "").strip()
        if line.startswith("# ") and not title:
            title = line[2:].strip()
            continue
        m = META_LINE.match(line)
        if m:
            meta[m.group(1)] = m.group(2).strip()
    return title, meta


def count_clauses(text: str) -> int:
    """数条文条数（从清洗前的原文数）—— 清洗正确性的硬判据。"""
    return len(set(CLAUSE.findall(text)))


def build_blocks(raw: str, max_chars: int, do_split: bool = False) -> tuple[list[tuple[str, str]], dict]:
    """把原文切成 [(条款标题, 正文)]。

    `do_split=False`（默认）：不切分。实测三种切分判据都会产出碎块或几乎不省字，
    详见文件顶部"切分器：默认关闭"一节。超长块交给 loader 截断 + grade_truncation 记账。

    返回的第二项是清洗动作统计，用来写进日志/文档 —— 清洗不能静默（和 loader 同理）。
    """
    text = raw.replace("\r\n", "\n").replace("\r", "\n")

    stats = {"page_marks": 0, "head_dropped": 0, "split": 0, "split_extra": 0}

    # ① 去跨页标记
    text, n = PAGE_MARK.subn("", text)
    stats["page_marks"] = n

    # ② 去掉开头的元信息区（`---` 分隔线之前的内容）
    lines = text.split("\n")
    first_content = next((i for i, l in enumerate(lines) if l.strip()), 0)
    head = "\n".join(lines[:first_content])
    body = "\n".join(lines[first_content:])
    stats["head_dropped"] = len(head.strip())

    # ③ 剥掉 body 里残留的 `---` 与"附件N"这类转存标签
    keep = []
    for l in body.split("\n"):
        s = l.strip()
        if s == "---" or re.fullmatch(r"附件\s*\d+", s) or re.fullmatch(r"[\d\s—-]{0,12}", s):
            continue
        keep.append(l)
    body = "\n".join(keep)

    # ④ 定位"第X条"作为块起点，切出正文
    matches = list(CLAUSE.finditer(body))
    if not matches:
        return [], stats

    raw_blocks: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        segment = body[start:end]
        nl = segment.find("\n")
        title_line = segment[:nl].strip() if nl != -1 else segment.strip()
        content = segment[nl + 1:].strip() if nl != -1 else ""
        clause_no = m.group(1)
        rest = title_line[len(clause_no):].strip() if len(title_line) > len(clause_no) else ""
        if rest:                            # 极少数：条款正文紧跟标题行
            content = (rest + " " + content).strip()
        if content:
            raw_blocks.append((clause_no, clean_spaces(content)))

    # ⑤ 只对超长块切分（默认不切，见函数 docstring）
    blocks: list[tuple[str, str]] = []
    for clause, content in raw_blocks:
        parts = split_long(clause, content, max_chars) if do_split else [(clause, content)]
        if len(parts) > 1:
            stats["split"] += 1
            stats["split_extra"] += len(parts) - 1
        blocks.extend(parts)

    return blocks, stats


def render(title: str, meta: dict, blocks: list[tuple[str, str]]) -> str:
    """渲染成 loader 认的格式：`---` 元信息 + `## 条款名` 分条。"""
    doc_name = f"{title}.md"
    lines = [
        "---",
        f"doc: {doc_name}",
        f"version: {meta.get('发布时间', '未标注')}",
        f"source: {meta.get('来源', '未标注')}",
        "---",
        "",
    ]
    for clause, content in blocks:
        lines.append(f"## {clause}")
        lines.append(content)
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- 项目判据（同源 import）

def load_health_module():
    """import 项目自带的体检脚本，用它的 grade_truncation 判"该不该切"。

    ★ 为什么这样：loader.py:204 明确写"判据只在一处定义，别在别处再写一套"。
    本脚本 import 同一个函数 ⇒ 阈值改了两边一起改，不会静默失效。
    """
    p = REPO_ROOT / "scripts" / "measure_chunk_health.py"
    spec = importlib.util.spec_from_file_location("_health", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- 主流程

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="真写到 docs/kb/（默认只干跑）")
    ap.add_argument("--only", help="只处理文件名含这个子串的（如 127861）")
    ap.add_argument("--split", action="store_true",
                    help="启用切分器（实测会产生碎块，详见文件顶部，默认关闭）")
    args = ap.parse_args()

    if not SRC_DIR.is_dir():
        print(f"找不到源目录：{SRC_DIR}")
        return 2

    health = load_health_module()
    MAX = health.MAX_CHARS_HINT              # 400，与 loader.load_documents 一致

    files = sorted(SRC_DIR.glob("*.md"))
    if args.only:
        files = [f for f in files if args.only in f.name]

    if not args.write:
        print("【干跑模式】加 --write 才真的写文件。\n")

    total = 0
    all_drops: list[int] = []
    failed = []
    print(f"{'文件':<50} {'条文':>4} {'原块':>4} {'切后':>4} {'跨页':>4} {'一致':>5}")
    print("-" * 82)

    for f in files:
        raw = f.read_text(encoding="utf-8")
        title, meta = parse_head(raw)
        blocks, stats = build_blocks(raw, MAX, do_split=args.split)
        expect = count_clauses(raw.replace("\r\n", "\n"))

        # 条文数一致性：清洗不能丢条也不能多造
        base = len({t.split("-续")[0] for t, _ in blocks})
        ok = base == expect and expect > 0
        flag = "✓" if ok else "✗ 不一致"
        print(f"{f.name[:48]:<50} {expect:>4} {expect:>4} {len(blocks):>4} "
              f"{stats['page_marks']:>4} {flag:>5}")
        if not ok:
            failed.append((f.name, expect, base))
        total += len(blocks)

        # 切分后还剩多少超长（应该接近 0，剩下的是切不动的）
        for t, c in blocks:
            if len(c) >= MAX:
                all_drops.append(len(c) - MAX)

        if args.write and ok:
            out = DST_DIR / f"{title}.md"
            if out.exists():
                print(f"     ⚠ 目标已存在，跳过（不覆盖）：{out.name}")
                continue
            out.write_text(render(title, meta, blocks), encoding="utf-8")
            print(f"     → 已写入 {out.name}（{len(blocks)} 块，"
                  f"切分 {stats['split']} 处增 {stats['split_extra']} 块）")

    print("-" * 82)
    print(f"合计 {len(files)} 份，{total} 块")

    if failed:
        print("\n★★ 条文数对不上的文件（未写入）：")
        for name, e, g in failed:
            print(f"   {name}：期望 {e} 条，实得 {g} 条")
        return 1

    # ★ 用项目自带的判据复判，而不是我自己的阈值
    lvl, msg = health.grade_truncation(all_drops)
    print(f"\n项目判据 grade_truncation => {lvl}：{msg}")
    if all_drops:
        print(f"   切完仍超长 {len(all_drops)} 块（切不动、只能截断的）")
        print("   ★ 这些是连续叙述的长条款，硬切会断句 —— 保留截断并在体检报告里记账。")
    else:
        print("   切分后【无超长块】⇒ 不会有内容被 max_chars 截断丢失。")

    if not args.write:
        print("\n★ 全部对得上。加 --write 落盘。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
