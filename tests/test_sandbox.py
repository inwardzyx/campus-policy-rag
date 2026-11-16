# -*- coding: utf-8 -*-
"""沙箱判据：**四种越界必须各归各类**（纯函数，不依赖 docker）。

★ 为什么先测 classify 而不是直接跑容器：
  容器测试会受镜像/网络/宿主影响（慢且不稳），但"判定逻辑对不对"是纯函数，
  能在 CI 里毫秒级复算。真容器的证据在 `experiments/verify-sandbox.md`（脚本生成）。
"""
import os
import sys

os.environ["LANGSMITH_TRACING"] = "false"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import pytest                                                   # noqa: E402

from agent.sandbox import build_cmd, classify, enabled          # noqa: E402


def test_ok():
    assert classify("2.0\n", "", 0, False) == "ok"


def test_timeout_wins_over_everything():
    assert classify("", "partial", None, True) == "timeout"


def test_oom_by_exit_code_and_by_memory_error():
    assert classify("", "", 137, False) == "oom"
    assert classify("", "MemoryError", 1, False) == "oom"


def test_network_markers():
    for msg in ("Temporary failure in name resolution", "Name or service not known",
                "Network is unreachable", "Connection refused"):
        assert classify("", msg, 1, False) == "network_blocked", msg


def test_write_blocked_accepts_eacces_and_erofs():
    """★ 第一版只认 EROFS，实测探针拿到的是 `Permission denied`（nobody 无权限）⇒ 误报失败。"""
    assert classify("", "PermissionError: [Errno 13] Permission denied", 1, False) == "write_blocked"
    assert classify("", "OSError: [Errno 30] Read-only file system", 1, False) == "write_blocked"


def test_unknown_is_error_not_success():
    assert classify("", "ZeroDivisionError: division by zero", 1, False) == "error"


def test_switch_is_off_by_default(monkeypatch):
    monkeypatch.delenv("SANDBOX_ENABLED", raising=False)
    assert enabled() is False
    monkeypatch.setenv("SANDBOX_ENABLED", "1")
    assert enabled() is True


def test_cmd_has_the_five_limits():
    cmd = build_cmd()
    joined = " ".join(cmd)
    for must in ("--network=none", "--read-only", "--memory=128m",
                 "--cpus=0.5", "--pids-limit=64", "-u 65534"):
        assert must in joined, must
    # 代码必须从 stdin 喂（`python -`），避免 Windows 引号问题
    assert cmd[-2:] == ["python", "-"]
