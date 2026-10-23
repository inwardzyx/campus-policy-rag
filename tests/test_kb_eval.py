# -*- coding: utf-8 -*-
"""kb/eval.py 的 smoke 质检测试（不加载模型）。"""

from __future__ import annotations

from kb.eval import evaluate_namespace

CHUNKS = [
    {"doc_id": "d1", "doc": "报销制度", "clause": "第三条", "text": "费用发生后 30 日内提交报销。",
     "namespace": "demo", "acl": ["public"]},
    {"doc_id": "d2", "doc": "薪酬保密", "clause": "第三条", "text": "员工不得向同事透露个人薪酬。",
     "namespace": "demo", "acl": ["hr"]},
]


def _search_factory(leak: bool = False):
    def search(q, k=5, namespace=None, acl=None):
        if any(x in q for x in ("图书馆", "天气", "护照")):
            return []
        if "薪酬" in q:
            if leak or (acl and "hr" in acl):
                return [(CHUNKS[1], 0.9)]
            return []
        return [(CHUNKS[0], 0.9)]
    return search


def test_eval_reports_self_hit_refusal_and_no_leak():
    r = evaluate_namespace(CHUNKS, _search_factory(), "demo", acl=None)
    assert r["documents"] == 2          # ns 下全部文档；self-hit 只抽公开块
    assert r["chunks"] == 2
    assert r["sampled"] == 1            # 公开块才做 self-hit
    assert r["self_hit"]["passed"] == 1
    assert r["refusal"]["passed"] == 3
    assert r["acl_leak"] is False
    assert "不是独立评测集" in r["note"]


def test_eval_detects_acl_leak():
    r = evaluate_namespace(CHUNKS, _search_factory(leak=True), "demo", acl=None)
    assert r["acl_leak"] is True
