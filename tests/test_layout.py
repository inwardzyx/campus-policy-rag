# -*- coding: utf-8 -*-
"""Docling / MinerU 适配器测试：用假 converter / runner，不装重模型。"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from kb.layout import (
    BackendUnavailable,
    DoclingBackend,
    DoclingDockerBackend,
    MinerUBackend,
    select_backend,
)


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


def test_docling_docker_adapter_runs_image_and_reads_md(tmp_path):
    calls = []

    def runner(args):
        calls.append(args)
        for i, a in enumerate(args):
            if a == "-v" and args[i + 1].endswith(":/io/out"):
                out = Path(args[i + 1].rsplit(":", 1)[0])
                (out / "doc.md").write_text("## Docker OCR\n\n内容。", encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, b"", b"")

    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"fake")
    be = DoclingDockerBackend(cmd="docker", image="docling-runner:test",
                              cache_volume="docling-models", runner=runner)
    md = be.to_markdown(pdf)
    assert "## Docker OCR" in md
    argv = calls[0]
    assert "docling-runner:test" in argv
    assert "/io/in/doc.pdf" in argv and "/io/out/doc.md" in argv
    assert "docling-models:/root/.cache" in argv


def test_docling_docker_timeout_kills_container(tmp_path):
    killed = []

    def runner(args):
        if len(args) > 1 and args[1] == "kill":
            killed.append(args[2])
            return subprocess.CompletedProcess(args, 0, b"", b"")
        raise subprocess.TimeoutExpired(args, 1)

    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"fake")
    be = DoclingDockerBackend(cmd="docker", image="img", timeout=1, runner=runner)
    with pytest.raises(BackendUnavailable) as exc:
        be.to_markdown(pdf)
    assert "超时" in str(exc.value)
    assert killed and killed[0].startswith("docling-")


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
