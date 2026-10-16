# -*- coding: utf-8 -*-
r"""校验「部分可答」批次的每一道题：**"答不了的那半"是不是真的不在库里**。

为什么必须机器校验（2026-10-16 实测教训）
==========================================
盲出题人（新会话、只看底本）**写了假缺口**。逐条验的时候抓到：
  · p2 声称"底本没规定处分是否记入档案" —— 底本第 1155 行明写「归入…本人档案」；
  · p6 声称"只说次月10日前" —— 底本第 528 行还有「次月5日前…汇总发放」。
⇒ 出题人说"没有"，只是**它没找到**，不等于**库里没有**。
   不校验就跑基线，会得到一批"系统答对了却被记成错"的假失败。

★ 本脚本只做**机械取证**，不做最终判定：
  它把每道题的 missing_part 拆成关键词，在**真库**（kb loader 出来的 chunk）里逐个查，
  报出"疑似已覆盖"的疑点。**最终判"这道题留不留"由人看这份报告决定** ——
  因为"缺哪个值"是语义判断，纯关键词查会漏（同义词、换个说法）。
  本仓库对这类事的立场见 CLAUDE.md：「缺失 ≠ 零」「检出 ≠ 证明」。

用法：
    python evalset/heldout/_pipeline/verify_partial.py
输出：
    evalset/heldout/_partial_verify.md   逐题取证报告（含原文命中行）
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(r"D:\_review_rag-cs")
HELDOUT = REPO / "evalset" / "heldout"
QS = HELDOUT / "questions-partial.json"
SRC = HELDOUT / "source_policy.md"
OUT = HELDOUT / "_partial_verify.md"

# 每道题要查的"缺口关键词"。★ 人工读题后写的，不是自动抽的 ——
#   自动抽关键词会把整句拆成噪音，查不查得到都说明不了问题。
PROBES: dict[str, list[str]] = {
    "p1": [r"病假.{0,30}时限", r"请假.{0,20}(几|多少).{0,10}日", r"审批.{0,10}时限",
           r"病假.{0,30}(批|办结)"],
    "p2": [r"归入.{0,20}档案", r"本人档案", r"处分解除", r"记入.{0,10}档案"],
    "p3": [r"困难补助.{0,30}(时限|几|到账)", r"补助.{0,20}发放.{0,10}(时间|日)",
           r"审批.{0,10}时限"],
    "p4": [r"励志奖学金.{0,40}发放", r"一次性发放", r"分.{0,4}次.{0,10}发"],
    "p5": [r"外出.{0,30}责任", r"谁主管谁负责", r"责任划分", r"事故.{0,20}责任"],
    "p6": [r"酬金.{0,20}发放时间", r"次月\s*\d+\s*日", r"发放报酬", r"酬金发放"],
    "p7": [r"学费减免.{0,30}(时限|结果|几)", r"减免.{0,20}审批", r"办结"],
    "p8": [r"处分决定.{0,20}(时限|日|作出)", r"违纪.{0,30}时限", r"调查.{0,10}时限"],
}


def main() -> int:
    if not QS.exists():
        print(f"✗ 找不到 {QS} —— 先跑 run_partial_blind.py")
        return 2
    qs = json.loads(QS.read_text(encoding="utf-8"))
    src_lines = SRC.read_text(encoding="utf-8").splitlines()

    # ★ 还要查真库。这里用 loader 现读的**语料全文**当代理 ——
    #   ⚠️ 它不是"入库后的 263 块"（关卡会再拦掉 23 条），所以有**假阳性**风险：
    #     底本里有、但被关卡拦掉的条款会命中，看着像"缺口不成立"。
    #     这一层的判定交给人（本报告只报疑点），别把它当机械判据。
    sys.path.insert(0, str(REPO))
    try:
        from kb import loader
        docs, errors = loader.load_documents(REPO / "docs")
        kb_text = "\n".join(d.get("text", "") for d in docs)
        kb_note = (f"语料 {len(docs)} 篇 / {len(kb_text)} 字符"
                   f"（⚠️ 关卡前口径，不是入库后的 263 块）")
        if errors:
            kb_note += f"；loader 报了 {len(errors)} 条错"
    except Exception as exc:                                  # noqa: BLE001
        kb_text, kb_note = "", f"⚠️ 语料没读成（{type(exc).__name__}: {exc}）—— 只查底本"

    lines = ["# 「部分可答」批次逐题取证报告", "",
             f"> 底本：`source_policy.md`（{len(src_lines)} 行）；{kb_note}",
             "> ★ 本报告只报**疑点**。「疑似已覆盖」不代表这道题一定要删 —— 人工再读一遍原文定夺。",
             ""]
    suspicious = []
    for q in qs:
        qid = q.get("id", "?")
        lines += [f"## {qid}　{q.get('question','')}", "",
                  f"- **答得上那半**：{q.get('answerable_part','')}",
                  f"- **声称答不了**：{q.get('missing_part','')}",
                  f"- gold：`{q.get('gold')}`", ""]
        hits_src, hits_kb = [], []
        for pat in PROBES.get(qid, []):
            for m in re.finditer(pat, SRC.read_text(encoding="utf-8")):
                ln = SRC.read_text(encoding="utf-8")[:m.start()].count("\n") + 1
                hits_src.append((ln, src_lines[ln - 1].strip()[:110], pat))
            if kb_text:
                for m in re.finditer(pat, kb_text):
                    hits_kb.append((m.group(0)[:70], pat))
        if hits_src:
            lines.append("- ⚠️ **底本命中**（缺口可能不成立）：")
            for ln, txt, pat in hits_src[:4]:
                lines.append(f"    - L{ln}（`{pat}`）：{txt}")
        if hits_kb:
            lines.append("- ⚠️ **入库后仍命中**（系统可能答得出来）：")
            for txt, pat in hits_kb[:4]:
                lines.append(f"    - `{pat}` → …{txt}…")
        if not hits_src and not hits_kb:
            lines.append("- ✅ 底本与真库都没命中 ⇒ 缺口看起来成立")
        else:
            suspicious.append(qid)
        lines.append("")

    lines += ["---", "",
              f"## 结论：{len(suspicious)}/{len(qs)} 道有疑点",
              "",
              f"疑点题：{', '.join(suspicious) if suspicious else '（无）'}", "",
              "⇒ 有疑点的题**不要直接进基线**：先人读原文，再决定",
              "  （改题 / 改 missing_part 的表述 / 或降级成本就是一道 answer 题）。"]
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"✓ 报告已落盘：{OUT}")
    print(f"  {len(suspicious)}/{len(qs)} 道有疑点：{', '.join(suspicious) if suspicious else '（无）'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
