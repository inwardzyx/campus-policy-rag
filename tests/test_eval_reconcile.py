# -*- coding: utf-8 -*-
"""把「两把尺子」的分叉变成一条会红的判据。

## 为什么要有这个文件

`eval/case_server.py` 复用了 `evalset/run_eval.py` 的判分函数，所以两边**现在**是一致的 ——
但"现在一致"是一次性证据：哪天有人（包括我）在 `case_server.py` 里顺手改一句
`k=5` → `k=4`、或者把 `(doc, clause)` 的比对写成只比 `doc`，
`summarize.py --compare` 那条**手工**对账不会自动跑，于是两把尺子悄悄分叉，
而报告上的数看起来依然很合理。

本文件就是那条常驻判据，两层：

- `test_payload_gold_hit_is_consistent`：用 payload **自己**的候选集独立重算一遍 Recall@5
  （顺带钉住"粗捞必须是 top-5"），不需要再花模型调用。
- `test_payload_matches_run_eval_on_sample`：真·跨实现比较 —— `case_server._payload` 的结论
  对上 `run_eval.recall_gold` / `run_eval.knowledge_hit`。

★ 抽样是**确定性**的（固定 stride），不是随机 —— 随机抽样会让"这条用例今天红明天绿"。
  recall5 那一维**不抽样**（44 道应答题全比），多花约 4 秒换"改了命中定义就一定红"。

★★ 反向验证矩阵（2026-11-11 实测，三种变异各跑一遍；每条都是**跑出来的**，不是推的）：

| 变异 | 第一条（自洽） | 第二条（跨实现） |
|---|---|---|
| `k=5 → k=4` | ❌ 红（"候选只有 4 条（应 ≥5）"） | ✅ 绿 |
| gold 比对只留 `doc`、丢掉 `clause` | ❌ 红（`gold_rank` 与 `gold_hit` 不自洽） | ✅ 绿 |
| `gold_hit = True`（逻辑写反） | ❌ 红 | ❌ 红 |

为什么第二条只对最后一种敏感：`gold_hit` 本来就是 `R.recall_gold()` 算出来的，
前两种改的是它**周边**（k、rank 的比较），只有把那个调用本身换掉才会出现"跨实现分歧"。
⇒ 两条分工：第一条守"公式与形状"，第二条守"与 run_eval 的电路没被换掉"。**第一条是主判据。**
"""

import os
import sys

os.environ["LANGSMITH_TRACING"] = "false"
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "evalset"))
sys.path.insert(0, os.path.join(REPO_ROOT, "eval"))

import pytest                                      # noqa: E402
import service as svc                              # noqa: E402
from conftest import _FakeLLM                      # noqa: E402

import run_eval as R                               # noqa: E402  ← 被复用的那一份判分实现
import case_server as cs                           # noqa: E402  ← 被测的接线层


@pytest.fixture(scope="module")
def rag():
    """别的文件已经建过库就直接用，不再花十几秒重来一次。"""
    if not getattr(svc.rag, "ready", False):
        svc.rag.startup()
    return svc.rag


@pytest.fixture(scope="module")
def prepared(rag):
    cs._load_index()
    return cs


@pytest.fixture(scope="module")
def payloads(prepared, monkeypatch_module):
    """离线层跑一遍全部题目，返回 {qid: payload}。

    ★ 必须是 fixture（module 作用域）：写成普通函数会被两个用例各调一次，
      105 题就白跑两遍（实测 25.8s → 13s 的差别）。
    """
    monkeypatch_module.setattr(svc.rag, "_llm", _FakeLLM("（假模型返回，未真实调用）"))
    monkeypatch_module.setattr(cs, "LAYER", "offline")   # ★ 钉住层：环境变量不该影响判据结论
    out = {}
    for qid, q in prepared._BY_ID.items():
        out[qid] = prepared._payload({"qid": qid, "question": q["question"]})
    return out


@pytest.fixture(scope="module")
def monkeypatch_module():
    """module 作用域的 monkeypatch（pytest 自带的是 function 作用域）。"""
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


def test_payload_gold_hit_is_consistent(prepared, payloads):
    """Recall@5 必须能从 payload 自己的候选集重算出来 —— 这才叫"两把尺子比过"。"""
    bad = []
    for qid, p in payloads.items():
        if p["type"] != "answer":
            continue
        gold = tuple(prepared._BY_ID[qid]["gold"])
        cands = p["cands"]
        # ① 粗捞必须是 top-5：候选条数不对，说明 k 被改过（k 一改 Recall@5 的含义就变了）
        if len(cands) < 5:
            bad.append(f"{qid}: 候选只有 {len(cands)} 条（应 ≥5）")
            continue
        # ② 独立重算：gold 是否在这 5 条里（用 (doc, clause) 而不是只比 doc）
        hit = any((c["doc"], c["clause"]) == gold for c in cands[:5])
        if hit != p["gold_hit"]:
            bad.append(f"{qid}: payload.gold_hit={p['gold_hit']}，但按候选重算={hit}")
        # ③ gold_rank 与 gold_hit 必须自洽（rank 有值 ⇔ hit 为真）
        if (p["gold_rank"] is not None) != bool(p["gold_hit"]):
            bad.append(f"{qid}: gold_rank={p['gold_rank']} 与 gold_hit={p['gold_hit']} 不自洽")
    assert not bad, "case_server 的 Recall@5 与它自己的候选集对不上：\n" + "\n".join(bad[:10])


def test_payload_matches_run_eval_on_sample(prepared, payloads):
    """跨实现抽样比较：payload 的结论必须等于 run_eval 自己算出来的结论。

    ★ 抽样是**确定性**的（`sorted(...)[::10]`），不是随机 —— 随机会让"今天红明天绿"。
    ★ recall5 这一维**不抽样**（44 道应答题全比）：多花 44 次检索（约 4 秒），
      换来"改了命中定义就一定红"。实测（2026-11-11）：把 gold 的比对从 `(doc, clause)`
      改成"只比 doc"时，**抽样版本抓不到**（抽到的那几题恰好没有同文档不同条款），
      全比版本才红 —— 抽样抽的是"看起来均匀"，不是"判据的敏感点"。
    ★ 反例边界：把 `k=5` 改成 `k=4` 时**任何版本都抓不到结论差异**，因为当前这版 105 题里
      没有一道"gold 恰好排第 5"；那种改法由**第一条**用例（候选必须 ≥5 条）守。
    """
    qids = sorted(payloads)[::10]                    # knowledge_hit 的确定性抽样
    qids += [q for q, p in payloads.items() if p["type"] == "answer" and q not in qids]
    bad = []
    for qid in qids:
        q, p = prepared._BY_ID[qid], payloads[qid]
        if q["type"] == "answer":
            if bool(p["gold_hit"]) != bool(R.recall_gold(q)):
                bad.append(f"{qid}: gold_hit payload={p['gold_hit']} run_eval={R.recall_gold(q)}")
        if bool(p["knowledge_hit"]) != bool(R.knowledge_hit(q, with_llm=False)):
            bad.append(f"{qid}: knowledge_hit payload={p['knowledge_hit']} "
                       f"run_eval={R.knowledge_hit(q, with_llm=False)}")
    assert not bad, ("case_server 与 run_eval 的判分结论不一致（两把尺子分叉）：\n"
                     + "\n".join(bad[:10]))
