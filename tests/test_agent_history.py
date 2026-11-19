# -*- coding: utf-8 -*-
"""多轮会话（history）的判据：进了上下文、压得对、不传时零影响。

★ 为什么要有这三类断言：
  1. **不传 history 时消息序列必须与改动前一致** —— 否则 105 题基线全变，
     "加个功能"变成"重跑一切"。这条是安全网。
  2. **压缩规则要能说清**：最近 3 轮原文、更早的截 40 字；顺序是旧→新。
  3. **提示词里必须写"不要复述"** —— 否则模型会拿历史当答案素材。
"""
import os
import sys

os.environ["LANGSMITH_TRACING"] = "false"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from agent.loop import HISTORY_ITEM_CHARS, _compress_history, _history_note, run_loop  # noqa: E402


class _Capture:
    """假 llm：记录收到的消息，立即返回最终答案（不调工具）。"""

    def __init__(self):
        self.seen = []

    def invoke(self, msgs):
        self.seen = list(msgs)
        return _Resp("答完了")


class _Resp:
    def __init__(self, content):
        self.content = content
        self.tool_calls = []
        self.usage_metadata = {"total_tokens": 10}


def test_no_history_keeps_messages_identical():
    cap = _Capture()
    run_loop("请假谁批", llm=cap, tools={}, gate=lambda q: True)
    assert len(cap.seen) == 2, [type(m).__name__ for m in cap.seen]


def test_history_inserted_between_system_and_question():
    cap = _Capture()
    run_loop("请假谁批", llm=cap, tools={}, gate=lambda q: True,
             history=["年假有几天", "病假工资怎么算"])
    assert len(cap.seen) == 3
    assert type(cap.seen[1]).__name__ == "SystemMessage"
    assert "不要复述" in cap.seen[1].content
    assert "病假工资怎么算" in cap.seen[1].content
    assert type(cap.seen[2]).__name__ == "HumanMessage"


def test_compress_keeps_recent_full_and_truncates_older():
    old = "很早以前问过的一个特别特别长的问题" + "很" * 40
    history = [old, "第二轮", "第三轮", "第四轮", "第五轮"]
    got = _compress_history(history)
    # 最近 3 轮原文保留
    assert got[-3:] == ["第三轮", "第四轮", "第五轮"]
    # 更早的两条被截断
    assert all(len(x) <= HISTORY_ITEM_CHARS + 1 for x in got[:-3])


def test_note_is_empty_without_history():
    assert _history_note([]) == ""


def test_note_marks_order_and_purpose():
    note = _history_note(["第一问", "第二问"])
    assert note.index("第一问") < note.index("第二问")
    assert "原问题为准" in note
