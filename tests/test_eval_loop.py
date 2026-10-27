# -*- coding: utf-8 -*-
"""评测闭环 v0 的判据：查询扩展（`kb/expand.py`）+ 门禁（`kb/loopgate.py`）。

## 盯的四条
  ① 扩展只加词、不加坏：没命中就原样返回；命中才追加，且**幂等**（调两次不会加两遍）；
  ② 门禁必须拒绝"有回退"的候选（哪怕净收益 > 0）；
  ③ 门禁必须拒绝"拒答题变作答"和"越权泄漏"（这两类比少答一题严重）；
  ④ 回滚能把 active 指回上一版（否则"闭环"只有上、没有退）。
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest                                   # noqa: E402

from kb.expand import Lexicon                   # noqa: E402
from kb.loopgate import decide                  # noqa: E402


# ---------------------------------------------------------------- expand

ENTRIES = [{"id": "e1", "match": ["二专业"], "add": ["第二学士学位"]},
           {"id": "e2", "match": ["处分", "种类"], "add": ["纪律处分", "开除学籍"]}]


def test_apply_appends_and_is_idempotent() -> None:
    lex = Lexicon(ENTRIES)
    once, ids = lex.apply("二专业怎么申请？")
    assert "第二学士学位" in once and ids == ["e1"]
    twice, _ = lex.apply(once)
    assert twice.count("第二学士学位") == 1, "重复调用不能把词加两遍"


def test_no_match_returns_original() -> None:
    lex = Lexicon(ENTRIES)
    q = "图书馆几点关门？"
    assert lex.apply(q) == (q, [])


def test_load_missing_file_is_none(tmp_path) -> None:
    assert Lexicon.load(tmp_path / "nope.json") is None
    p = tmp_path / "lex.json"
    p.write_text(json.dumps({"entries": ENTRIES}, ensure_ascii=False), encoding="utf-8")
    assert len(Lexicon.load(p).entries) == 2


# ---------------------------------------------------------------- gate

def _row(cid, bucket, typ="answer"):
    return {"id": cid, "type": typ, "bucket": bucket}


def test_gate_accepts_net_gain_without_loss() -> None:
    before = [_row("q1", "miss"), _row("q2", "ok"), _row("r1", "refuse_ok", "refuse")]
    after = [_row("q1", "ok"), _row("q2", "ok"), _row("r1", "refuse_ok", "refuse")]
    v = decide(before, after, min_gain=1)
    assert v["verdict"] == "accept", v
    assert v["gains"] == ["q1"]


def test_gate_rejects_any_regression() -> None:
    before = [_row("q1", "miss"), _row("q2", "ok")]
    after = [_row("q1", "ok"), _row("q2", "miss")]
    v = decide(before, after, min_gain=1)
    assert v["verdict"] == "reject"
    assert v["losses"] == ["q2"]


def test_gate_rejects_refuse_becoming_answer() -> None:
    before = [_row("q1", "miss"), _row("r1", "refuse_ok", "refuse")]
    after = [_row("q1", "ok"), _row("r1", "ok", "refuse")]
    v = decide(before, after, min_gain=1)
    assert v["verdict"] == "reject"
    assert v["refuse_losses"] == ["r1"]


def test_gate_rejects_acl_leak() -> None:
    before = [_row("q1", "miss"), _row("d1", "refuse_ok", "deny")]
    after = [_row("q1", "ok"), _row("d1", "acl_leak", "deny")]
    v = decide(before, after, min_gain=1)
    assert v["verdict"] == "reject"
    assert v["leaks"] == ["d1"]


def test_gate_rejects_when_gain_below_threshold() -> None:
    v = decide([_row("q1", "miss")], [_row("q1", "ok")], min_gain=2)
    assert v["verdict"] == "reject"


# ---------------------------------------------------------------- rollback

def test_rollback_returns_to_previous_active(tmp_path) -> None:
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                    "scripts"))
    import eval_loop

    reg = {"schema": "eval-loop/1", "active": None, "history": [
        {"id": "l1", "candidates": "a.json", "status": "active", "gate": {"verdict": "accept"}},
        {"id": "l2", "candidates": "b.json", "status": "active", "gate": {"verdict": "accept"}},
    ]}
    p = tmp_path / "registry.json"
    p.write_text(json.dumps(reg), encoding="utf-8")
    active = eval_loop.rollback(p)
    assert active is not None and active["id"] == "l1"
