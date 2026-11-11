# -*- coding: utf-8 -*-
"""held-out 校验和的判据 —— 外加一条**常驻**变异，证明它真的会红。

背景见 `scripts/check_heldout_sha.py` 的 docstring：题目文件原来是"裸奔"的
（没有校验和/版本字段），悄悄改一道题的 gold 再跑一遍，`kb/runstate.py` 的指纹不会变，
分数变了也只会被解释成"模型抖动"。

★ 为什么变异要写成 pytest 而不是一次性脚本：
  一次性的变异只能证明"那天会红"；写成用例之后，**每次 pytest 都重新证明一遍**。
  这也是本仓 `check_judge_rules.py` 那条规矩（判据必须有变异）的落地方式。
"""
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
QUESTS = REPO_ROOT / "evalset" / "heldout" / "questions.json"
CHECKER = REPO_ROOT / "scripts" / "check_heldout_sha.py"
PY = sys.executable


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([PY, str(CHECKER), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", cwd=str(REPO_ROOT),
                          env={**os.environ, "PYTHONIOENCODING": "utf-8"})


def test_heldout_sha_matches():
    """现状必须一致（否则说明有人改了题没更新 sha）。"""
    r = _run()
    assert r.returncode == 0, f"check_heldout_sha 报红：\n{r.stdout}\n{r.stderr}"


def test_heldout_sha_catches_one_char_change(tmp_path):
    """★ 变异用例：改一个字符 ⇒ 必须 exit 1，并指出"改题就要更新 sha"。"""
    mutated = tmp_path / "questions.json"
    text = QUESTS.read_text(encoding="utf-8")
    # 只改一个字符：把第一道题的题面末字换掉（保证文件仍然可读，只是内容不同）
    old, new = "处分？", "处分!"
    assert old in text, "题面里找不到用来变异的锚点，请更新这条用例"
    mutated.write_text(text.replace(old, new, 1), encoding="utf-8")

    r = _run("--questions", str(mutated))
    assert r.returncode == 1, f"改了一个字符却没报红，退出码={r.returncode}\n{r.stdout}\n{r.stderr}"
    assert "不一致" in r.stderr
    assert "questions.sha256" in r.stderr
