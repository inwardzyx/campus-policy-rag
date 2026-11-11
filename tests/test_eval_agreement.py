# -*- coding: utf-8 -*-
"""人机一致性（κ）脚本的判据 —— 三个"拒绝"与一个"能算"。

★ 为什么把"拒绝输出 κ"写成用例：本仓踩过"常红等于把警报贴住"，也踩过"假绿"。
  κ 这个数最危险的形态不是算错，而是**样本不够却照样打印一个** ——
  它会被截图、被引用，然后没人再看样本量。所以这里钉三条：
    1. n < 20 → 拒绝（exit 2），且**一个 κ 都不许打印**；
    2. 标注两边标签分布一致（pe=1）→ κ 无定义，拒绝（不许偷偷给 1.0）；
    3. 样本够且能定义 → 能算出已知值（完美一致的例子 κ=1.0）。
"""
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "eval" / "agreement.py"
PY = sys.executable


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([PY, str(SCRIPT), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", cwd=str(REPO_ROOT),
                          env={**os.environ, "PYTHONIOENCODING": "utf-8"})


def _write(path: Path, rows: list[dict]) -> Path:
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return path


def test_refuses_below_min_n(tmp_path):
    rows = [{"id": f"x{i}", "label_a": "correct", "label_b": "wrong"} for i in range(19)]
    p = _write(tmp_path / "n19.json", rows)
    r = _run("--input", str(p))
    assert r.returncode == 2, f"19 条居然没拒绝：{r.stdout}{r.stderr}"
    assert "不输出 κ" in r.stderr
    # ★ 双保险：屏幕上一个 κ 都没有（防止"先算后拒"被截图）
    assert "κ =" not in r.stdout and "κ =" not in r.stderr


def test_undefined_kappa_is_refused(tmp_path):
    """20 条全填同一个标签 ⇒ pe=1，κ 的分母是 0 ⇒ 必须拒绝，不许给 1.0。"""
    rows = [{"id": f"x{i}", "label_a": "correct", "label_b": "correct"} for i in range(20)]
    p = _write(tmp_path / "degenerate.json", rows)
    r = _run("--input", str(p))
    assert r.returncode == 2, f"pe=1 居然输出了 κ：{r.stdout}{r.stderr}"
    assert "无法定义 κ" in r.stderr
    assert "κ =" not in r.stdout


def test_kappa_is_computed_when_valid(tmp_path):
    """完美一致但两边都用到了两个标签 ⇒ pe=0.5 ⇒ κ=1.0。"""
    rows = ([{"id": f"a{i}", "label_a": "correct", "label_b": "correct"} for i in range(10)] +
            [{"id": f"b{i}", "label_a": "wrong", "label_b": "wrong"} for i in range(10)])
    p = _write(tmp_path / "valid.json", rows)
    r = _run("--input", str(p))
    assert r.returncode == 0, r.stderr
    assert "Cohen's κ = 1.0000" in r.stdout
