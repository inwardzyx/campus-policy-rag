# -*- coding: utf-8 -*-
"""Docling / MinerU 适配器测试：用假 converter / runner，不装重模型。"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from kb.layout import BackendUnavailable, DoclingBackend, MinerUBackend, select_backend


class _Doc:
    def export_to_markdown(self):
        return "## 报销制度\n\n费用发生后 30 日内提交。"


class _Result:
    document = _Doc()


class _Converter:
    def convert(self, path):
        assert path.endswith(".pdf")
        return _Result()


def test_docling_adapter_returns_markdown():
    be = DoclingBackend(converter_factory=lambda: _Converter())
    md = be.to_markdown(Path("x.pdf"))
    assert "## 报销制度" in md


def test_mineru_adapter_reads_output_markdown(tmp_path):
    def runner(args):
        out = Path(args[args.index("-o") + 1])
        out.mkdir(parents=True, exist_ok=True)
        (out / "x.md").write_text("## OCR 标题\n\n内容。", encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, b"", b"")

    be = MinerUBackend(cmd="fake-mineru", runner=runner)
    md = be.to_markdown(tmp_path / "x.pdf")
    assert "## OCR 标题" in md


def test_mineru_nonzero_exit_raises(tmp_path):
    def runner(args):
        return subprocess.CompletedProcess(args, 2, b"", b"boom")

    be = MinerUBackend(cmd="fake-mineru", runner=runner)
    with pytest.raises(BackendUnavailable) as exc:
        be.to_markdown(tmp_path / "x.pdf")
    assert "退出码 2" in str(exc.value)


def test_select_unknown_backend_raises():
    with pytest.raises(BackendUnavailable):
        select_backend("nope")
