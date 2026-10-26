# -*- coding: utf-8 -*-
"""部署验收脚本（`scripts/deploy_smoke.py`）的纯逻辑判据。

## 盯的两条
  ① `/search` 与 `/chat` 两种来源形状都能拆成 (doc, clause) —— 否则"带出处"永远判不中；
  ② 证据报告里的通过数按实际结果算（多算一个就是假绿）。
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

SPEC = importlib.util.spec_from_file_location("deploy_smoke", ROOT / "scripts" / "deploy_smoke.py")
smoke = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(smoke)


def test_pair_accepts_both_source_shapes() -> None:
    assert smoke._pair({"doc": "a.md", "clause": "第八条"}) == ("a.md", "第八条")
    assert smoke._pair({"doc": "a.md｜第八条"}) == ("a.md", "第八条")


def test_md_report_counts_only_passed_checks() -> None:
    checks = [{"check": "health", "ok": True, "detail": "ok"},
              {"check": "chat", "ok": False, "detail": "hit=False"}]
    md = smoke.md_report("http://x", "demo", checks)
    assert "通过 **1/2**" in md
    assert "| health | ✓ |" in md and "| chat | ✗ |" in md
