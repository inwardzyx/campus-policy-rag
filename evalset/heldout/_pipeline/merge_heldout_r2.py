# -*- coding: utf-8 -*-
"""抽出第二轮题目 → 校验 → 与第一轮合并（第一轮存档，保证旧数字可复现）。"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RAW = Path(r"D:\dsh-work\_out_heldout_r2_raw.md")
HD = Path(r"D:\_review_rag-cs\evalset\heldout")
R1 = HD / "questions.json"          # 合并前 = 第一轮
ARCHIVE = HD / "questions-r1.json"  # 第一轮存档（25 题）
R2ONLY = HD / "questions-r2.json"   # 第二轮单独（便于分开量）

text = "\n".join(l for l in RAW.read_text(encoding="utf-8").splitlines()
                 if not l.startswith("[claude-code:"))
fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
if fence:
    text = fence.group(1)
blob = text[text.find("{"):text.rfind("}") + 1]
data = json.loads(blob)
qs2 = data.get("questions") if isinstance(data, dict) else data
print(f"第二轮抽出 {len(qs2)} 题")

r1 = json.loads(R1.read_text(encoding="utf-8"))
qs1 = r1.get("questions") if isinstance(r1, dict) else r1


def norm(s):
    return re.sub(r"[\s，。？！、,.?!：:；;\"'“”‘’（）()《》]", "", s or "")


seen = {norm(q.get("question")): q.get("id") for q in qs1}
dup = [q.get("id") for q in qs2 if norm(q.get("question")) in seen]
if dup:
    print(f"⚠ 与第一轮重合 {len(dup)} 道：{dup}（会被剔除）")
    qs2 = [q for q in qs2 if norm(q.get("question")) not in seen]

ans1 = sum(1 for q in qs1 if q.get("type") == "answer")
ref1 = sum(1 for q in qs1 if q.get("type") == "refuse")
ans2 = sum(1 for q in qs2 if q.get("type") == "answer")
ref2 = sum(1 for q in qs2 if q.get("type") == "refuse")
print(f"  第一轮 answer {ans1} / refuse {ref1}")
print(f"  第二轮 answer {ans2} / refuse {ref2}")

# 存档第一轮 + 单独存第二轮
ARCHIVE.write_text(json.dumps(r1, ensure_ascii=False, indent=2), encoding="utf-8")
R2ONLY.write_text(json.dumps({"_说明": data.get("_说明"), "questions": qs2},
                             ensure_ascii=False, indent=2), encoding="utf-8")

merged = {
    "_说明": [
        "两轮盲出题合并。第一轮 25 题（answer 19 + refuse 6），第二轮追加；"
        "第一轮原文存档在 questions-r1.json，第二轮在 questions-r2.json（便于分开量）。",
        "出题条件：全新会话、零工具、空工作目录，只看到 source_policy.md（校规原文）+ 出题说明书；"
        "没有看过主评测集、docs/ 的 chunk、README 或任何检索信息。",
        "⚠️ 出题人与项目作者同模型（deepseek-flash）：盲 = 没看过数据，不等于不同的先验。",
    ] + list(r1.get("_说明", []))[:2],
    "questions": qs1 + qs2,
}
R1.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
tot = len(qs1) + len(qs2)
print(f"\n合并后 {tot} 题（answer {ans1+ans2} + refuse {ref1+ref2}）")
print(f"  {R1.name} = 全量；{ARCHIVE.name} = 第一轮；{R2ONLY.name} = 第二轮")
