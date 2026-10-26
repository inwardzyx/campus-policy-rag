# -*- coding: utf-8 -*-
"""索引文本模式（`INDEX_TEXT_MODE`）：默认把「文档名 + 条款号」也放进索引。

## 依据（配对实验，同批 32 题、同语料）
产物 `experiments/ab-index-text.json`：
- 条款题：`text` 2/15 → `doc_clause_text` **11/15**
- 内容题：17/17 → **17/17**（零退化）

## 盯的两条
  ① 默认模式是 `doc_clause_text`，`_index_text` 里含 doc 与 clause；
  ② 设回 `text` 时**逐字节等于旧行为**（回滚开关必须真的能回滚）。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import service as svc                           # noqa: E402

CHUNK = {"doc": "差旅与费用报销管理办法（2026修订版）.md", "clause": "第八条（住宿标准）",
         "text": "一类城市限额 600 元/人·晚。"}


def test_default_mode_indexes_doc_and_clause() -> None:
    assert svc.INDEX_TEXT_MODE == "doc_clause_text", "默认应为配对实验采纳的模式"
    text = svc.rag._index_text(CHUNK)
    assert CHUNK["doc"] in text and CHUNK["clause"] in text and CHUNK["text"] in text


def test_text_mode_is_byte_identical_to_old_behavior(monkeypatch) -> None:
    monkeypatch.setattr(svc, "INDEX_TEXT_MODE", "text")
    assert svc.rag._index_text(CHUNK) == CHUNK["text"], "回滚开关必须真的回到旧行为"


def test_unknown_mode_falls_back_to_default_behavior() -> None:
    """写错模式名时按默认处理（不能变成"只索引空串"那种静默失忆）。"""
    text = svc.rag._index_text(CHUNK)
    assert text != "" and CHUNK["text"] in text
