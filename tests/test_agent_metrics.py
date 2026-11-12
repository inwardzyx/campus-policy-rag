# -*- coding: utf-8 -*-
"""`eval/agent_metrics.py` 的判据：gold 匹配必须**只认同一份文件 + 同一条款**。

★ 为什么这条要单独测：`gold_in_sources` 是 agent 报告里唯一一个"看起来像 recall"的数。
  它错了不会报错，只会**偏高** —— 而偏高的分数比没有分数更坏（看起来像进步）。
  这里用真 gold 的两种写法（带章节前缀 / 不带）和三种错配（换文件、换条款、空来源）钉住。
"""
import os
import sys

os.environ["LANGSMITH_TRACING"] = "false"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from eval.agent_metrics import _split_doc, gold_hit          # noqa: E402


def _q(gold):
    return {"id": "t1", "type": "answer", "question": "q", "gold": gold}


def test_hit_when_doc_and_clause_match_with_prefix_variant():
    """gold 写 `第三章-第十九条（三）`，来源写 `第十九条（三）` ⇒ 算命中（互相包含）。"""
    q = _q(["学生纪律处分管理规定.md", "第三章-第十九条（三）"])
    srcs = [{"doc": "学生纪律处分管理规定.md｜第十九条（三）", "text": "…", "score": 0.8}]
    assert gold_hit(q, srcs) is True


def test_miss_when_doc_differs():
    """条款字面一样、**文件不同** ⇒ 不许算命中（跨文件的同号条款是两件事）。"""
    q = _q(["学生纪律处分管理规定.md", "第三章-第十九条（三）"])
    srcs = [{"doc": "学生请假管理办法.md｜第十九条（三）", "text": "…", "score": 0.8}]
    assert gold_hit(q, srcs) is False


def test_miss_when_clause_differs():
    q = _q(["学生纪律处分管理规定.md", "第三章-第十九条（三）"])
    srcs = [{"doc": "学生纪律处分管理规定.md｜第二十条", "text": "…", "score": 0.8}]
    assert gold_hit(q, srcs) is False


def test_no_gold_or_empty_sources_is_false_not_error():
    assert gold_hit(_q(None), [{"doc": "a.md｜第一条"}]) is False
    assert gold_hit(_q([]), [{"doc": "a.md｜第一条"}]) is False
    assert gold_hit(_q(["a.md", "第一条"]), []) is False


def test_split_doc_without_separator():
    assert _split_doc("只有文件名.md") == ("只有文件名.md", "")
    assert _split_doc("文件.md｜条款") == ("文件.md", "条款")
