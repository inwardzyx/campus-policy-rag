# -*- coding: utf-8 -*-
"""`env` 的**来源**不是系统状态（2026-10-28 修）。

## 事故
同一个 key 值，一次从进程环境读到、一次从 `.env` 读到：
`{'set': True, 'sha8': '8f4359f2', 'source': 'env'}` vs `{... 'source': 'dotenv'}`
—— `sha8` 完全相同，只是来源不同。
旧实现把整个 entry 拿去比：指纹不同、diff 报"配置变了"、门禁判"不是一个系统"。
真实场景必然撞上：从不同 shell 跑评测，有的 export 了 key，有的没有。

## 判据
**参与比较/指纹的是"有效值"**（set、value、sha8），`source` 只留作展示。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kb import runstate                            # noqa: E402


def test_source_不同但值相同_指纹相同(tmp_path) -> None:
    (tmp_path / ".env").write_text("DEEPSEEK_API_KEY=sk-test123456\n", encoding="utf-8")
    a = runstate.capture(tmp_path, env={})                                   # 仅 .env
    b = runstate.capture(tmp_path, env={"DEEPSEEK_API_KEY": "sk-test123456"})  # 进程环境同值
    assert a["env"]["DEEPSEEK_API_KEY"]["sha8"] == b["env"]["DEEPSEEK_API_KEY"]["sha8"]
    assert a["fingerprint"] == b["fingerprint"], "来源不同不该改变指纹"
    assert runstate.diff_states(a, b) == [], "来源不同不该算差异"


def test_值真的变了_仍然能检出(tmp_path) -> None:
    (tmp_path / ".env").write_text("DEEPSEEK_API_KEY=sk-test123456\n", encoding="utf-8")
    a = runstate.capture(tmp_path, env={})
    b = runstate.capture(tmp_path, env={"DEEPSEEK_API_KEY": "sk-other999"})
    assert a["fingerprint"] != b["fingerprint"]
    items = runstate.diff_state_items(a, b)
    assert [it["key"] for it in items] == ["DEEPSEEK_API_KEY"]
