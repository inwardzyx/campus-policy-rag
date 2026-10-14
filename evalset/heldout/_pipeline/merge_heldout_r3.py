# -*- coding: utf-8 -*-
"""抽出第三轮 → 查重（文本 + 语义）→ 合并。第三轮存档。

为什么要"语义查重"：盲出题人第三轮看不到前两轮的**题面**（只给了已覆盖的条款名），
所以它很可能换个说法问同一件事（"图书馆几点开" vs "图书馆开放时间"）。
文本查重抓不到这种，用向量相似度抓：新题与任一已有同型题余弦 > 0.93 就丢掉。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(r"D:\_review_rag-cs")
HD = REPO / "evalset" / "heldout"
RAW = Path(r"D:\dsh-work\_out_heldout_r3_raw.md")
CUR = HD / "questions.json"
ARCHIVE = HD / "questions-r3.json"
SIM_MAX = 0.93

sys.path.insert(0, str(REPO))
import numpy as np  # noqa: E402
import env_compat  # noqa: E402

env_compat.ensure_mmh3()
env_compat.ensure_uuid_utils()
import service as svc  # noqa: E402


def norm(s):
    return re.sub(r"[\s，。？！、,.?!：:；;\"'“”‘’（）()《》]", "", s or "")


text = "\n".join(l for l in RAW.read_text(encoding="utf-8").splitlines()
                 if not l.startswith("[claude-code:"))
fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
if fence:
    text = fence.group(1)
data = json.loads(text[text.find("{"):text.rfind("}") + 1])
new = data.get("questions") if isinstance(data, dict) else data
print(f"第三轮抽出 {len(new)} 题"
      f"（answer {sum(1 for q in new if q.get('type')=='answer')} / "
      f"refuse {sum(1 for q in new if q.get('type')=='refuse')}）")

cur = json.loads(CUR.read_text(encoding="utf-8"))
old = cur.get("questions") if isinstance(cur, dict) else cur

# ---- ① 文本查重 ----
seen = {norm(q.get("question")): q.get("id") for q in old}
dup = [q.get("id") for q in new if norm(q.get("question")) in seen]
if dup:
    print(f"  文本重合（剔除）：{dup}")
new = [q for q in new if norm(q.get("question")) not in seen]

# ---- ② 语义查重（同型题之间）----
svc.rag.startup()
model = svc.rag.model


def embed(texts):
    return np.array(list(model.query_embed(texts)), dtype="float32")


def cos_matrix(a, b):
    a = a / (np.linalg.norm(a, axis=1, keepdims=True) + 1e-9)
    b = b / (np.linalg.norm(b, axis=1, keepdims=True) + 1e-9)
    return a @ b.T


kept, dropped = [], []
for typ in ("answer", "refuse"):
    olds = [q for q in old if q.get("type") == typ]
    news = [q for q in new if q.get("type") == typ]
    if not olds or not news:
        kept.extend(news)
        continue
    ov, nv = embed([q["question"] for q in olds]), embed([q["question"] for q in news])
    sims = cos_matrix(nv, ov)
    for i, q in enumerate(news):
        j = int(np.argmax(sims[i]))
        if sims[i][j] > SIM_MAX:
            dropped.append((q["id"], olds[j]["id"], float(sims[i][j]), q["question"][:26]))
        else:
            kept.append(q)

if dropped:
    print(f"  语义重合（>{SIM_MAX}，剔除）：")
    for qid, oid, s, t in dropped:
        print(f"    {qid} ≈ {oid}（cos={s:.3f}） {t}")
print(f"  实际并入 {len(kept)} 题")

ARCHIVE.write_text(json.dumps({"_说明": data.get("_说明"), "questions": new},
                              ensure_ascii=False, indent=2), encoding="utf-8")
merged = {
    "_说明": list(cur.get("_说明", []))[:2] + [
        "第三轮追加：重点补拒答题（按 5 类各 5 道出），并做了语义查重（cos>0.93 丢弃）。",
        "各轮存档：questions-r1.json / questions-r2.json / questions-r3.json。",
    ],
    "questions": old + kept,
}
CUR.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
a = sum(1 for q in merged["questions"] if q.get("type") == "answer")
r = sum(1 for q in merged["questions"] if q.get("type") == "refuse")
print(f"\n合并后 {len(merged['questions'])} 题（answer {a} + refuse {r}）")
