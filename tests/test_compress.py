# -*- coding: utf-8 -*-
"""块级规则压缩的守门用例。

## 盯的四条
  ① **近重复整块丢**：3-gram Jaccard ≥ 0.8 ⇒ 后来的块进 `excluded`（reason=dup），
     并且**保留先到的那块**（顺序可复现，不按"谁更长"选）；
  ② **噪声行只删整行**：页码/分隔线删掉，正文一个字不动（当前语料实测 0 处命中，
     这两条用例是给上传的 PDF/HTML 语料守的）；
  ③ **默认关**：`CONTEXT_COMPRESS` 没设时行为与压缩前**逐字节一致**；
  ④ **报告可核对**：`before_chars/after_chars/dedup_dropped` 都现算，不是手写常量。

★ 变异体在 `scripts/mutate_compress.py`：把去重阈值抬到永不触发 / 让噪声正则吞掉整段。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest                                   # noqa: E402

import service as svc                           # noqa: E402
from kb import compress as cmp                  # noqa: E402


def chunk(doc="d.md", clause="第四条", text="按第四条，材料齐全的三个工作日内办结。"):
    return {"doc": doc, "clause": clause, "text": text, "text_full": text,
            "acl": ["public"]}


# ---------------------------------------------------------------- ① 去重

def test_near_duplicate_is_dropped_and_reported() -> None:
    a = chunk(clause="第四条", text="按第四条，材料齐全的三个工作日内办结，逾期需说明理由。")
    b = chunk(clause="第五条", text="按第四条，材料齐全的三个工作日内办结，逾期需说明理由。")  # 只差编号
    out, rep = cmp.compress_chunks([a, b], mode=cmp.MODE_RULE)
    assert out[0].get("_drop_reason") is None
    assert out[1].get("_drop_reason") == "dup", "近重复块必须被标记，而不是静默消失"
    assert rep["dedup_dropped"] == 1
    assert rep["after_chars"] < rep["before_chars"]


def test_first_occurrence_wins_not_the_longer_one() -> None:
    """保留**先到**的块：它排序更靠前；按"更长"选会让同一语料两次请求留下不同块。

    ★ 构造上要注意：两块的 3-gram Jaccard 必须 ≥0.8 才会被判成近重复。
      所以用"同一段长文本 + 末尾多一句"，而不是"一短一长"（后者相似度不够，测不到）。
    """
    base = ("按第四条，材料齐全的三个工作日内办结，逾期需说明理由，由学院审核后报学生处备案，"
            "并在系统中留痕。材料不齐的，一次性告知需要补正的全部内容；补正后重新计算办理期限。"
            "对不符合条件的，书面说明理由并告知申请人享有依法申请复核的权利。")
    a = chunk(clause="第四条", text=base)
    b = chunk(clause="第五条", text=base + "另：自发布起施行。")
    out, rep = cmp.compress_chunks([a, b], mode=cmp.MODE_RULE)
    assert cmp.jaccard(a["text_full"], b["text_full"]) >= 0.8, "用例构造失效：两块不够像"
    assert out[0].get("_drop_reason") is None, "先到的块不该被后来的更长块挤掉"
    assert out[1].get("_drop_reason") == "dup"


def test_dissimilar_blocks_are_both_kept() -> None:
    out, rep = cmp.compress_chunks(
        [chunk(text="报销时限是三十日。"), chunk(text="宿舍熄灯时间由学校统一规定。")],
        mode=cmp.MODE_RULE)
    assert all(c.get("_drop_reason") is None for c in out)
    assert rep["dedup_dropped"] == 0


# ---------------------------------------------------------------- ② 噪声行

def test_noise_lines_removed_but_body_kept() -> None:
    text = "第 3 页\n第四条 材料齐全的三个工作日内办结。\n----\n17"
    out, rep = cmp.compress_chunks([chunk(text=text)], mode=cmp.MODE_RULE)
    got = out[0]["text_full"]
    assert "三个工作日内办结" in got, "正文被删了 —— 这是最危险的一种坏法"
    assert "第 3 页" not in got and "----" not in got
    assert rep["noise_lines_removed"] == 3


def test_digits_inside_a_sentence_are_not_noise() -> None:
    text = "第四条 金额不超过 20000 元，办理期限 30 日。"
    out, _ = cmp.compress_chunks([chunk(text=text)], mode=cmp.MODE_RULE)
    assert out[0]["text_full"] == text, "行内的数字不是噪声，不能删"


# ---------------------------------------------------------------- ③ 默认关

def test_mode_off_leaves_chunks_untouched() -> None:
    a = chunk()
    b = chunk(doc="e.md")
    out, rep = cmp.compress_chunks([a, b], mode=cmp.MODE_OFF)
    assert [c.get("_drop_reason") for c in out] == [None, None]
    assert out[0]["text_full"] == a["text_full"]
    assert rep["mode"] == "off" and rep["after_chars"] == rep["before_chars"]


def test_env_switch_only_accepts_rule() -> None:
    assert cmp.mode_from_env({}) == "off"
    assert cmp.mode_from_env({"CONTEXT_COMPRESS": "RULE"}) == "rule"
    assert cmp.mode_from_env({"CONTEXT_COMPRESS": "on"}) == "off", "只认 rule，别猜"


# ---------------------------------------------------------------- ④ 接线

@pytest.fixture
def rag(monkeypatch):
    monkeypatch.setattr(svc.rag, "ready", True)
    state = {"cands": []}
    monkeypatch.setattr(svc.rag, "search",
                        lambda q, k=5, namespace=None, acl=None:
                        [(c, 0.9) for c in state["cands"]])
    monkeypatch.setattr(svc.rag, "rerank",
                        lambda q, cands: [(c, s, 9, "假精排") for c, s in cands])
    monkeypatch.setattr(svc.rag, "answer", lambda q, chunks: "按第四条办结[1]。")
    return state


def test_compression_is_off_by_default_in_service(rag) -> None:
    rag["cands"] = [chunk(clause="第四条"), chunk(clause="第五条")]
    r = svc.handle_chat(svc.ChatRequest(question="q", namespace="hr"))
    assert r.context.compression is None, "默认关时不该出现压缩报告"


def test_compression_on_marks_duplicate_block_in_trace(rag, monkeypatch) -> None:
    monkeypatch.setattr(svc, "CONTEXT_COMPRESS_MODE", "rule")
    same = "按第四条，材料齐全的三个工作日内办结，逾期需说明理由。"
    rag["cands"] = [chunk(clause="第四条", text=same), chunk(clause="第五条", text=same)]
    r = svc.handle_chat(svc.ChatRequest(question="q", namespace="hr"))
    assert r.context.compression["dedup_dropped"] == 1
    assert [b.reason for b in r.context.excluded] == ["dup"]
    assert len(r.context.blocks) == 1
    assert len(r.sources) == 1, "被去重的块不该还出现在出处里"
