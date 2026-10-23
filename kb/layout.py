# -*- coding: utf-8 -*-
"""kb/layout.py —— 复杂 PDF / 扫描件的版面解析后端（Docling / MinerU）

为什么不在仓库里手写 OCR：
    OCR + 版面分析是重模型（Docling 的布局模型、MinerU 的 VLM），
    自己训/调不现实，也违反“不要重复造轮子”。

设计：
    · 只做**适配器**：把一个 PDF 文件交给本地已安装的 Docling 或 MinerU，
      拿回 Markdown 正文；
    · 两个后端都是可选的：没装就报清楚“装什么/怎么装”，不静默返回空；
    · 外部命令一律列表参数 + 超时，不走 shell（防止上传文件名注入）。

配置（环境变量）：
    PDF_BACKEND=auto|pypdf|docling|mineru   默认 auto
    MINERU_CMD=mineru                       可执行文件路径
    MINERU_ARGS="-p {input} -o {out}"       参数模板；{input}/{out} 占位
    MINERU_TIMEOUT=300
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Protocol


class BackendUnavailable(RuntimeError):
    """后端没安装 / 命令找不到 / 输出为空。"""


class LayoutBackend(Protocol):
    name: str

    def available(self) -> bool: ...

    def to_markdown(self, pdf_path: Path) -> str: ...


class DoclingBackend:
    name = "docling"

    def __init__(self, converter_factory: Callable | None = None) -> None:
        self._factory = converter_factory

    def available(self) -> bool:
        import importlib.util
        return importlib.util.find_spec("docling") is not None

    def _converter(self):
        if self._factory is not None:
            return self._factory()
        try:
            from docling.document_converter import DocumentConverter  # type: ignore
        except ImportError as exc:
            raise BackendUnavailable(
                "没装 Docling。安装：D:\\Python-project\\.venv\\Scripts\\python.exe -m pip install docling"
            ) from exc
        return DocumentConverter()

    def to_markdown(self, pdf_path: Path) -> str:
        converter = self._converter()
        try:
            result = converter.convert(str(pdf_path))
            doc = getattr(result, "document", result)
            md = doc.export_to_markdown()
        except Exception as exc:
            raise BackendUnavailable(f"Docling 解析失败：{exc}") from exc
        if not str(md).strip():
            raise BackendUnavailable("Docling 返回空 Markdown")
        return str(md)


class MinerUBackend:
    name = "mineru"

    def __init__(self, cmd: str | None = None, args_template: str | None = None,
                 timeout: int | None = None, runner: Callable | None = None) -> None:
        self.cmd = cmd or os.environ.get("MINERU_CMD", "mineru")
        self.args_template = args_template or os.environ.get(
            "MINERU_ARGS", "-p {input} -o {out}")
        self.timeout = timeout or int(os.environ.get("MINERU_TIMEOUT", "300"))
        self._runner = runner

    def available(self) -> bool:
        return shutil.which(self.cmd) is not None

    def _run(self, args: list[str]) -> subprocess.CompletedProcess:
        if self._runner is not None:
            return self._runner(args)
        try:
            return subprocess.run(args, capture_output=True, timeout=self.timeout, check=False)
        except FileNotFoundError as exc:
            raise BackendUnavailable(
                f"找不到 MinerU 命令 {self.cmd!r}。安装后设 MINERU_CMD，或用 PDF_BACKEND=docling"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise BackendUnavailable(f"MinerU 超时（>{self.timeout}s）") from exc

    def to_markdown(self, pdf_path: Path) -> str:
        with tempfile.TemporaryDirectory(prefix="mineru_") as out:
            args = [self.cmd] + [
                part.replace("{input}", str(pdf_path)).replace("{out}", out)
                for part in shlex.split(self.args_template)
            ]
            proc = self._run(args)
            if proc.returncode != 0:
                err = (proc.stderr or b"").decode("utf-8", errors="replace")[-500:]
                raise BackendUnavailable(f"MinerU 退出码 {proc.returncode}：{err}")
            mds = sorted(Path(out).rglob("*.md"), key=lambda p: p.stat().st_size, reverse=True)
            if not mds:
                raise BackendUnavailable("MinerU 没产出 .md（检查 MINERU_ARGS 输出目录）")
            text = mds[0].read_text(encoding="utf-8", errors="replace").strip()
            if not text:
                raise BackendUnavailable("MinerU 产出的 Markdown 为空")
            return text


def select_backend(name: str) -> LayoutBackend:
    name = (name or "auto").strip().lower()
    if name == "docling":
        return DoclingBackend()
    if name == "mineru":
        return MinerUBackend()
    raise BackendUnavailable(f"未知 PDF_BACKEND={name!r}（支持 pypdf/docling/mineru/auto）")
