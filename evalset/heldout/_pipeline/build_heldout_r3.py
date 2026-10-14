# -*- coding: utf-8 -*-
"""拼第三轮出题包：拒答题是主角（25 道），再补 5 道 answer 收尾。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(r"D:\_review_rag-cs")
HD = REPO / "evalset" / "heldout"
OUT = Path(r"D:\dsh-work\_task_heldout_r3.md")

covered = set()
for p in (REPO / "evalset" / "questions.json", HD / "questions.json"):
    d = json.loads(p.read_text(encoding="utf-8"))
    qs = d.get("questions") if isinstance(d, dict) else d
    for q in qs:
        if q.get("gold"):
            covered.add((q["gold"][0], q["gold"][1]))
covered_lines = "\n".join(f"- {a}｜{b}" for a, b in sorted(covered))

head = f"""你是一位**独立的出题人**。这是**第三轮**，前两轮已经出了 75 道题。

# 硬约束
1. **你只能看这份 prompt 里的文字**，没有工具、不能读文件。
2. 题面要**像学生说的**，别抄条款标题。刻意制造用词差异（条款"请假"→你"请个假"；
   条款"开除学籍"→你"直接开除"；条款"擅自"→你"没打招呼就"）。
3. 下面这些条款**已经被问过了**，answer 题请避开：

{covered_lines}

# 这一轮：**25 道 refuse + 5 道 answer**

## refuse 题（25 道）★ 重点

前两轮已经把最容易想到的"学校没提的事"问完了（开放时间、收费标准、办证流程那类）。
这一轮请**往难里出** —— 目标是**把检索分顶上去、再让系统判断"有没有那个值"**。
按下面 5 类各出 5 道：

| 类 | 意思 | 你要自己找的场景 |
|---|---|---|
| ③ 提到主题未给取值 | 库里沾了这个主题，但**没有用户要的那个值** | 问"某类补助能拿多少"，库里只写了"按上级标准执行" |
| ⑤ 错误前提 | 问题本身建立在**错误假设**上 | "听说挂科三门就退学，对吧？"（制度里没这条） |
| ⑥ 多跳但库里缺一环 | 要跨条款答，而**其中一条根本不存在** | 问"休学期间还能评奖学金吗"（两边各有规定，但无交叉条款） |
| ⑦ 制度给了权但要问细节 | 库里说"由某部门另定"，细节不在库里 | "家庭经济困难认定具体看什么材料"（库里只写"按认定办法"） |
| ⑧ 时间/主体错配 | 问的是已废止版本，或不属于本校主体 | 按 2019 年那份已废止的考核办法算 |

**③⑤⑥ 最有价值**（字面很近，会真的把分数顶上去）。每道都要在
`why_forced_to_refuse` 里写：**属于哪一类 + 库里到底缺哪个值**，
不能只写"没相关规定"。

## answer 题（5 道）
从上面**没列出**的条款里出，其中至少 2 道 multi-hop。
`gold` 填 `[文件名, 条款名]`，逐字抄底本。

# 输出格式（**只输出 JSON，不要解释、不要 markdown 代码围栏**）

{{
  "_说明": ["第三轮；谁出的、依据什么"],
  "questions": [
    {{"id": "s1", "type": "refuse", "question": "...", "gold": null,
      "why_forced_to_refuse": "【类⑤】……"}},
    {{"id": "s2", "type": "answer", "question": "...", "gold": ["文件名.md", "章-条"], "note": ""}}
  ]
}}

`id` 用 s1、s2、s3……按顺序编。

================================================================================
下面是出题人说明书（更细的规则与反例）
================================================================================

"""

tail = """

================================================================================
下面是校规原文底本（你唯一的材料）
================================================================================

"""

OUT.write_text(head + (HD / "README.md").read_text(encoding="utf-8") + tail
               + (HD / "source_policy.md").read_text(encoding="utf-8"), encoding="utf-8")
print(f"已写出 {OUT}（{OUT.stat().st_size:,} 字节）；已覆盖条款 {len(covered)} 条")
print("目标：refuse 25（按 5 类各 5 道）+ answer 5（≥2 multi-hop）")
