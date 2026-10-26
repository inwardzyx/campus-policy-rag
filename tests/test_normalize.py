# -*- coding: utf-8 -*-
"""`第X条` 归一化（`kb/normalize.py`）：真实 PDF 能不能切出条款。

## 盯的四条
  ① pypdf 抽出来的纯文本（**没有任何 `##`**）→ 能补出条款标题，切块后 `clause` 非空；
  ② 已经有 `#` 标题的文本 → **原样返回**（幂等，不重复加）；
  ③ 完全没有条款的文本 → 原样返回，退回定长切块（`clause` 为空是**真实情况**，不是失败）；
  ④ 误命中要挡住：`第三条所述情形包括……` 这种正文行（后面跟一大段）不能被当成标题。

★ 变异体 `scripts/mutate_normalize.py`：去掉"补完标题就走 markdown 切块" / 去掉长度挡板。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kb import source as source_mod            # noqa: E402
from kb.source import DocumentMeta            # noqa: E402
from kb.normalize import ensure_headings      # noqa: E402


def _raw(text: str, suffix: str = ".pdf"):
    meta = DocumentMeta(namespace="x", doc_id="d", title="手册", source="s",
                        version="1", updated_at="", acl=("public",))
    return source_mod.RawDocument(meta=meta, text=text, suffix=suffix)


def test_pdf_text_without_headings_gets_clause_blocks() -> None:
    """pypdf 的真实形态：正文连续、没有 `##`。"""
    text = (
        "某某制度\n\n"
        "第一条 为规范管理，制定本办法。\n\n"
        "第二条 本办法适用于全体正式员工。\n\n"
        "第三条 由人力资源部负责解释。\n"
    )
    new, added = ensure_headings(text)
    assert added == 3
    assert "## 第一条" in new and "## 第三条" in new

    chunks = source_mod.chunk_document(_raw(text))
    clauses = [c.get("clause") for c in chunks]
    assert "第一条" in clauses and "第三条" in clauses
    assert all(clauses), f"有块没切出条款：{clauses}"


def test_existing_headings_are_left_alone() -> None:
    text = "## 第一条\n正文。\n\n## 第二条\n正文。\n"
    new, added = ensure_headings(text)
    assert added == 0 and new == text, "已有序号标题时不许再补（幂等）"


def test_text_without_clauses_falls_back_to_plain_chunks() -> None:
    text = "这是一段没有条款结构的说明文字。" * 20
    new, added = ensure_headings(text)
    assert added == 0 and new == text
    chunks = source_mod.chunk_document(_raw(text))
    clauses = [c.get("clause") for c in chunks]
    # 退回定长/分段切块后，clause 是**分段标签**（"第1段"），不是编出来的"第X条"
    assert chunks and all(c and not c.startswith("第") or c.endswith("段") for c in clauses), clauses
    assert not any(c and c.endswith("条") for c in clauses), "不该凭空造出条款号"


def test_long_tail_is_not_treated_as_heading() -> None:
    """`第三条所述情形包括……`（后跟长正文）是正文行，不是标题。"""
    line = "第三条所述情形包括员工严重违反公司规章制度、营私舞弊并造成重大损害等"
    new, added = ensure_headings(line + "\n")
    assert added == 0, "长尾正文被误当成条款标题了"
