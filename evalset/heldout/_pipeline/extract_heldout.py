# -*- coding: utf-8 -*-
"""从盲出题人的原始输出里抽出 questions.json（不打印内容，只看统计）。"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RAW = Path(r"D:\dsh-work\_out_heldout_raw.md")
DST = Path(r"D:\_review_rag-cs\evalset\heldout\questions.json")

text = RAW.read_text(encoding="utf-8")
print(f"原始输出 {len(text):,} 字符")

# 去掉 CLI 噪音行（整行删掉 —— 那行形如：
#   [claude-code:unrecognized_model] {"model":"...","query_source":"sdk"}
# ★ 只剥前缀会把后面那个 {"model":...} 留下，于是下面 find("{") 抓到的
#   是它而不是题目 JSON —— 第一版就是这么错的：报 "Extra data: char 52"
text = "\n".join(l for l in text.splitlines() if not l.startswith("[claude-code:"))
fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
if fence:
    text = fence.group(1)
    print("  已在 markdown 代码围栏里找到内容")

# 取最外层 { ... }
start = text.find("{")
end = text.rfind("}")
if start < 0 or end < 0:
    print("✗ 没找到 JSON 对象")
    sys.exit(1)
blob = text[start:end + 1]

try:
    data = json.loads(blob)
except json.JSONDecodeError as e:
    print(f"✗ JSON 解析失败：{e}")
    Path(r"D:\dsh-work\_heldout_blob.txt").write_text(blob, encoding="utf-8")
    print("  已把原始片段存到 D:\\dsh-work\\_heldout_blob.txt 供人工看")
    sys.exit(1)

qs = data.get("questions") if isinstance(data, dict) else data
if not isinstance(qs, list):
    print("✗ 结构不对：既不是 {questions:[...]} 也不是数组")
    sys.exit(1)

DST.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

ans = [q for q in qs if q.get("type") == "answer"]
ref = [q for q in qs if q.get("type") == "refuse"]
mh = [q for q in qs if q.get("multi_hop")]
print(f"  已写入 {DST}")
print(f"  总数 {len(qs)}　answer {len(ans)}（multi_hop {len(mh)}）　refuse {len(ref)}")
print(f"  说明字段：{list(data.keys()) if isinstance(data, dict) else '(裸数组)'}")
print("\n前 6 道（只看题面，用来判断像不像学生问的）:")
for q in qs[:6]:
    print(f"  {q.get('id')}  [{q.get('type')}]  {q.get('question')}")
