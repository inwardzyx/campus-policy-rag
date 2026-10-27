# -*- coding: utf-8 -*-
"""分组指标（`kb/categories.py`）+ 显著性门禁（`kb/loopgate.py`）的判据。

## 盯的四条
  ① 分类可被题面 `tags` 覆盖（boundary 题不能靠猜）；
  ② 分组指标按类目分别给 Wilson —— 小样本必须看得出区间宽；
  ③ McNemar 用**精确二项**：+1/0 不显著（p=1.0），+6/0 才显著（p<0.05）；
  ④ 严格模式下"不显著"必须把 accept 改成 reject（否则严格模式等于没开）。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kb.categories import category_of                    # noqa: E402
from kb.evalkit.metrics import aggregate                # noqa: E402
from kb.loopgate import apply_significance, mcnemar_exact  # noqa: E402


def test_category_tags_override() -> None:
    assert category_of({"type": "refuse", "question": "讲师课时费多少？",
                        "tags": ["boundary"]}) == "boundary"


def test_category_inference() -> None:
    assert category_of({"type": "refuse", "question": "食堂几点开门？"}) == "out_of_scope"
    assert category_of({"type": "deny", "question": "薪酬结构调整？"}) == "acl_deny"
    assert category_of({"type": "answer", "question": "每年多少学时？"}) == "numeric"
    assert category_of({"type": "answer", "question": "谁负责审批？"}) == "other"


def test_by_category_groups_rows() -> None:
    rows = [
        {"id": "a1", "type": "answer", "bucket": "ok", "category": "numeric", "answer_value": True},
        {"id": "a2", "type": "answer", "bucket": "retrieval_miss", "category": "numeric", "answer_value": False},
        {"id": "r1", "type": "refuse", "bucket": "refuse_ok", "category": "out_of_scope", "answer_value": None},
    ]
    out = aggregate(rows, [])
    assert out["by_category"]["numeric"]["n"] == 2
    assert out["by_category"]["numeric"]["answer"]["rate"] == 0.5
    assert out["by_category"]["numeric"]["answer_value"] == 0.5
    assert out["by_category"]["out_of_scope"]["n"] == 1


def test_mcnemar_exact_values() -> None:
    assert mcnemar_exact(0, 0) == 1.0
    assert mcnemar_exact(1, 0) == 1.0, "只修 1 题不显著"
    assert mcnemar_exact(5, 0) == 0.0625
    assert mcnemar_exact(6, 0) == 0.03125
    assert mcnemar_exact(2, 2) == 1.0


def test_apply_significance_turns_accept_into_reject() -> None:
    weak = {"verdict": "accept", "reasons": [], "mcnemar_p": 1.0}
    strong = {"verdict": "accept", "reasons": [], "mcnemar_p": 0.0312}
    assert apply_significance(weak, 0.05)["verdict"] == "reject"
    assert apply_significance(strong, 0.05)["verdict"] == "accept"
    assert apply_significance(weak, 0.05)["reasons"], "拒绝必须给出理由"
