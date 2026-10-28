# -*- coding: utf-8 -*-
"""闭环验收协议（`scripts/paired_eval.py`）的判据。

## 盯的四条
  ① 证据不足必须报"无证据"（+1/0 不能算变好）；
  ② 指纹不一致必须报"不可比"（两个系统不能配对）；
  ③ 拒答回退 / 越权泄漏 / p95 超 1.5× 都必须否掉结论；
  ④ 达到门槛（净修 6、p<0.05）才给"有证据"。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import paired_eval                                        # noqa: E402


def _report(fp: str, rows: list[tuple[str, str, str]], p95: int = 3000) -> dict:
    """rows: (id, type, bucket)"""
    return {
        "meta": {"state_fingerprint": fp},
        "aggregate": {"p95_ms": p95},
        "rows": [{"id": i, "type": t, "bucket": b} for i, t, b in rows],
    }


def _pair(n_ok: int = 10, n_fixed: int = 6, n_broke: int = 0) -> tuple[dict, dict]:
    before, after = [], []
    for i in range(n_ok):
        before.append((f"q{i}", "answer", "ok"))
        after.append((f"q{i}", "answer", "ok"))
    for i in range(n_fixed):
        before.append((f"f{i}", "answer", "retrieval_miss"))
        after.append((f"f{i}", "answer", "ok"))
    for i in range(n_broke):
        before.append((f"b{i}", "answer", "ok"))
        after.append((f"b{i}", "answer", "retrieval_miss"))
    return _report("abc123", before), _report("abc123", after)


def test_six_fixes_is_evidence() -> None:
    a, b = _pair(n_fixed=6)
    r = paired_eval.evaluate([_dump(a)], [_dump(b)])
    assert r["verdict"] == "有证据", r
    assert r["p"] < 0.05 and r["ci95"][0] > 0


def test_one_fix_is_not_evidence() -> None:
    a, b = _pair(n_fixed=1)
    r = paired_eval.evaluate([_dump(a)], [_dump(b)])
    assert r["verdict"] == "无证据"
    assert any("p=" in x for x in r["reasons"])


def test_fingerprint_mismatch_is_not_comparable() -> None:
    a = _report("aaa111", [("q1", "answer", "ok")])
    b = _report("bbb222", [("q1", "answer", "ok")])
    r = paired_eval.evaluate([_dump(a)], [_dump(b)])
    assert r["verdict"] == "不可比"


def test_refuse_regression_blocks_evidence() -> None:
    a, b = _pair(n_fixed=6)
    a["rows"].append({"id": "r1", "type": "refuse", "bucket": "refuse_ok"})
    b["rows"].append({"id": "r1", "type": "refuse", "bucket": "ok"})
    r = paired_eval.evaluate([_dump(a)], [_dump(b)])
    assert r["verdict"] == "无证据"
    assert any("拒答" in x for x in r["reasons"])


def _dump(report: dict) -> str:
    import json
    import tempfile
    import pathlib

    p = pathlib.Path(tempfile.mkdtemp()) / "r.json"
    p.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    return str(p)
