# -*- coding: utf-8 -*-
"""门禁的状态判据：**只允许"候选文件"这一个差异**。

## 为什么单独加这组
原判据只校验 `EXPAND_LEXICON` "变了"，不校验"变成了候选文件"。
填一个不相干的词表也能通过 ⇒ 增益会被记到候选头上（假归因）。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import eval_loop                                   # noqa: E402
from kb import runstate                            # noqa: E402


def _state(root: Path, value: str) -> dict:
    return {"root": str(root), "fingerprint": "a" if not value else "b",
            "git": {"sha": "s", "diff_sha256": ""},
            "env": {"EXPAND_LEXICON": {"value": value, "source": "env"}},
            "corpus": {"namespaces": {}}, "docs": {"dirs": {}}}


def test_state_match_accepts_relative_and_absolute(tmp_path: Path) -> None:
    cand = tmp_path / "candidates.json"
    cand.write_text("[]", encoding="utf-8")
    base = _state(tmp_path, "")
    rel = _state(tmp_path, "candidates.json")
    r = runstate.state_match(base, rel, allow_env=("EXPAND_LEXICON",),
                             expect_env={"EXPAND_LEXICON": str(cand)})
    assert r["ok"], r
    absd = _state(tmp_path, str(cand))
    r2 = runstate.state_match(base, absd, allow_env=("EXPAND_LEXICON",),
                              expect_env={"EXPAND_LEXICON": "candidates.json"})
    assert r2["ok"], r2


def test_state_match_rejects_other_lexicon(tmp_path: Path) -> None:
    cand = tmp_path / "candidates.json"
    cand.write_text("[]", encoding="utf-8")
    other = tmp_path / "other.json"
    other.write_text("[]", encoding="utf-8")
    r = runstate.state_match(_state(tmp_path, ""), _state(tmp_path, str(other)),
                             allow_env=("EXPAND_LEXICON",),
                             expect_env={"EXPAND_LEXICON": str(cand)})
    assert not r["ok"]
    assert any("没指向候选文件" in x for x in r["reasons"]), r["reasons"]


def test_gate_rejects_unrelated_lexicon(tmp_path: Path, monkeypatch) -> None:
    """门禁本身必须用上这个判据（否则判据只在单测里活着）。"""
    cand = tmp_path / "candidates.json"
    cand.write_text("[]", encoding="utf-8")
    before = tmp_path / "before.json"
    after = tmp_path / "after.json"
    row = {"id": "q1", "type": "answer", "bucket": "ok"}
    before.write_text(json.dumps({"rows": [row], "meta": {}}), encoding="utf-8")
    after.write_text(json.dumps({"rows": [row], "meta": {}}), encoding="utf-8")

    # 要验的：after 的 EXPAND_LEXICON 指向 other.json（不是候选）⇒ 必须 reject
    other = tmp_path / "other.json"
    other.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(eval_loop, "_state_of",
                        lambda p: _state(tmp_path, "" if "before" in str(p) else str(other)))
    entry = eval_loop.gate_reports(before, after, cand, tmp_path / "reg2.json",
                                   allow_env=("EXPAND_LEXICON",), require_state=True)
    assert entry["state"]["ok"] is False, entry["state"]
    assert any("没指向候选文件" in x for x in entry["state"]["reasons"]), entry["state"]
