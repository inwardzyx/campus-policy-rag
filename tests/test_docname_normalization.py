# -*- coding: utf-8 -*-
"""文档名口径归一（`kb/attribution.py::_norm_source`）的判据。

## 背景（真实公司制度套件踩到的）
默认语料用文件名做 doc（`企业规章制度（50条）.md`），企业存储里 doc 不带后缀
（`企业规章制度（50条）`）。两边不归一，就出现：

- 检索**明明命中**，归因却记 `retrieval_miss`；
- `context_recall` 0.0；
- nDCG 0.0（qrels 键和 run 键对不上）。

一次修三处（归因 / recall / nDCG），所以这条测试盯的是"**同一个 key 口径**"。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kb import attribution                                  # noqa: E402
from kb.evalkit.metrics import _source_key, score_row      # noqa: E402


def test_norm_source_strips_known_suffixes_only() -> None:
    assert attribution._norm_source("a.md") == "a"
    assert attribution._norm_source("a.MD") == "a"
    assert attribution._norm_source("a.pdf") == "a"
    assert attribution._norm_source("a.md.md") == "a.md", "只去一层"
    assert attribution._norm_source("a.bin") == "a.bin"


def test_classify_ok_when_storage_doc_has_no_suffix() -> None:
    case = {"id": "c1", "type": "answer", "gold": ["制度.md", "第一条"]}
    resp = {"answer": "正文[1]", "knowledge_hit": True,
            "context": {"blocks": [{"source": "制度", "clause": "第一条"}],
                        "citation": {"used_ids": [1]}}}
    assert attribution.classify(case, resp)["bucket"] == "ok"


def test_score_row_recall_uses_same_key() -> None:
    case = {"id": "c1", "type": "answer", "gold": ["制度.md", "第一条"]}
    resp = {"answer": "正文[1]", "knowledge_hit": True,
            "sources": [{"doc": "制度｜第一条", "clause": None}],
            "context": {"blocks": [{"source": "制度", "clause": "第一条"}],
                        "citation": {"used_ids": [1]}}}
    row = score_row(case, resp)
    assert row["context_recall"] == 1.0
    assert row["source_keys"][0] == "制度｜第一条"


def test_source_key_parses_combined_display_string() -> None:
    """chat 的展示 sources 是 `doc｜clause` 拼好的；要能解析回 key。"""
    assert _source_key({"doc": "制度｜第一条", "clause": None}) == "制度｜第一条"
    assert _source_key({"doc": "制度.md", "clause": "第一条"}) == "制度｜第一条"
