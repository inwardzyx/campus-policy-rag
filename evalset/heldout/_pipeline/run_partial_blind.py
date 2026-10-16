# -*- coding: utf-8 -*-
r"""跑「部分可答」批次的盲出题：把出题包发给全新会话的模型，落盘原始返回。

★ 为什么必须落盘原始返回（照 `_blind_out/r1..r3.md` 的先例）：
  出题人的原文是**凭证** —— 之后有人说"这题是你自己编的"，能拿它出来对质。
  而且 JSON 解析失败时，原始返回是唯一的排查材料。

★ 为什么在脚本里现读 prompt 而不把它贴进我的上下文：
  41k 字符，读一遍就再也清不掉了（之后每轮都重发）。脚本读、脚本发，我一行都不看。

用法：
    python evalset/heldout/_pipeline/run_partial_blind.py
输出：
    evalset/heldout/_blind_out/partial.md       出题人原始返回
    evalset/heldout/questions-partial-raw.json  解析后的题（**未校验**，8 道全在）
    ⚠️ 校验后的版本是 questions-partial.json（只有缺口成立的题）——
       **本脚本不写那个文件**，免得重跑一次就把人工复核的成果冲掉。
       校验见 verify_partial.py + 人读，产物见 _partial_verify.md / _partial_baseline.md。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(r"D:\_review_rag-cs")
HELDOUT = REPO / "evalset" / "heldout"
BLIND_OUT = HELDOUT / "_blind_out" / "partial.md"
PROMPT = Path(r"D:\dsh-work\_task_partial.md")
OUT_JSON = HELDOUT / "questions-partial-raw.json"

# 读 .env 里的 key（不打印、不落盘）
env = REPO / ".env"
if env.exists():
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("DEEPSEEK_API_KEY=") and not os.environ.get("DEEPSEEK_API_KEY"):
            os.environ["DEEPSEEK_API_KEY"] = line.split("=", 1)[1].strip().strip('"').strip("'")

if not os.environ.get("DEEPSEEK_API_KEY"):
    print("✗ 没有 DEEPSEEK_API_KEY —— 出不了题。先配好再跑。")
    sys.exit(2)

sys.path.insert(0, str(REPO))
from langchain_deepseek import ChatDeepSeek          # noqa: E402
from langchain_core.messages import HumanMessage      # noqa: E402

prompt = PROMPT.read_text(encoding="utf-8")
print(f"出题包 {len(prompt)} 字符，发给 deepseek-chat（temperature=0）…")

llm = ChatDeepSeek(model="deepseek-chat", temperature=0, timeout=300, max_retries=1)
resp = llm.invoke([HumanMessage(content=prompt)])
raw = resp.content
usage = getattr(resp, "usage_metadata", None) or {}

BLIND_OUT.write_text(raw, encoding="utf-8")
print(f"✓ 原始返回已落盘：{BLIND_OUT}（{len(raw)} 字符，tokens={usage.get('total_tokens', '?')}）")

# 解析 JSON：出题人偶尔会加 ```json 围栏或前言，切出第一个 { 到最后一个 }
s, e = raw.find("{"), raw.rfind("}")
if s < 0 or e < 0:
    print("✗ 返回里找不到 JSON —— 去看原始返回，别猜。")
    sys.exit(1)
try:
    data = json.loads(raw[s:e + 1])
except json.JSONDecodeError as exc:
    print(f"✗ JSON 解析失败：{exc}\n  去看 {BLIND_OUT} 的原始返回。")
    sys.exit(1)

qs = data.get("questions", [])
OUT_JSON.write_text(json.dumps(qs, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"✓ 解析出 {len(qs)} 道题，已落盘：{OUT_JSON}")
for q in qs:
    print(f"  {q.get('id')}: {q.get('question', '')[:60]}")
    print(f"      答得上：{q.get('answerable_part', '')[:50]}")
    print(f"      答不了：{q.get('missing_part', '')[:50]}")
