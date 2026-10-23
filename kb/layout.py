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
import uuid
from pathlib import Path
from typing import Callable, Protocol


def _resolve_cmd(cmd: str) -> str:
    """Windows 下 Docker 常不在 PATH 里；显式找 Docker Desktop 的 bin。"""
    found = shutil.which(cmd)
    if found:
        return found
    if os.name == "nt":
        candidates = [
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) /
            "Docker" / "Docker" / "resources" / "bin" / f"{cmd}.exe",
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) /
            "Docker" / "Docker" / "resources" / "bin" / "docker.exe",
        ]
        for p in candidates:
            if p.is_file():
                return str(p)
    return cmd


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
            detail = str(exc)
            if "DLL load failed" in detail or "_pava_pybind" in detail:
                detail += (
                    "；这是 Windows 应用控制策略拦截了 Docling 的 .pyd（不是代码问题）。"
                    "请改在 Docker/WSL 里跑 Docling，或先用文本层 PDF。"
                )
            raise BackendUnavailable(f"Docling 解析失败：{detail}") from exc
        if not str(md).strip():
            raise BackendUnavailable("Docling 返回空 Markdown")
        return str(md)


class DoclingDockerBackend:
    """在 Docker 容器里跑 Docling —— 绕开 Windows WDAC 对 .pyd 的拦截。

    设计取舍：
      · 一次性 `docker run --rm`，不常驻；与 MinerU 的外部命令模式一致；
      · 输入/输出各挂一个临时目录，输入只读；文件名固定为 doc.pdf，
        避免中文/空格/argv 编码问题；
      · 超时先 terminate，再 best-effort `docker kill <唯一 name>` ——
        前台 docker run 被杀不保证容器退出，不能留下孤儿容器。
    """

    name = "docling"

    def __init__(self, cmd: str | None = None, image: str | None = None,
                 timeout: int | None = None, runner: Callable | None = None,
                 cache_volume: str | None = None) -> None:
        self.cmd = _resolve_cmd(cmd or os.environ.get("DOCLING_CMD", "docker"))
        self.image = image or os.environ.get("DOCLING_IMAGE", "docling-runner:2.142.1")
        self.timeout = timeout or int(os.environ.get("DOCLING_TIMEOUT", "300"))
        self.cache_volume = cache_volume or os.environ.get("DOCLING_CACHE_VOLUME", "docling-models")
        self._runner = runner

    def available(self) -> bool:
        if self._runner is not None:
            return True
        if shutil.which(self.cmd) is None:
            return False
        try:
            p = subprocess.run([self.cmd, "image", "inspect", self.image],
                               capture_output=True, timeout=15)
            return p.returncode == 0
        except Exception:
            return False

    def _run(self, args: list[str]) -> subprocess.CompletedProcess:
        if self._runner is not None:
            return self._runner(args)
        try:
            return subprocess.run(args, capture_output=True, timeout=self.timeout, check=False)
        except FileNotFoundError as exc:
            raise BackendUnavailable(f"找不到 Docker 命令 {self.cmd!r}") from exc
        except subprocess.TimeoutExpired as exc:
            raise exc

    def _kill(self, name: str) -> None:
        try:
            self._run([self.cmd, "kill", name])
        except Exception:
            pass

    def to_markdown(self, pdf_path: Path) -> str:
        with tempfile.TemporaryDirectory(prefix="docling_docker_") as td:
            in_dir = Path(td) / "in"
            out_dir = Path(td) / "out"
            in_dir.mkdir()
            out_dir.mkdir()
            # 固定文件名，规避中文/空格在 argv / 容器路径里的编码问题
            shutil.copyfile(pdf_path, in_dir / "doc.pdf")
            name = f"docling-{uuid.uuid4().hex[:8]}"
            args = [
                self.cmd, "run", "--rm", "--name", name,
                "-v", f"{in_dir}:/io/in:ro",
                "-v", f"{out_dir}:/io/out",
                "-v", f"{self.cache_volume}:/root/.cache",
                "-e", f"HF_ENDPOINT={os.environ.get('HF_ENDPOINT', 'https://hf-mirror.com')}",
                "-e", "HF_HUB_DISABLE_XET=1",
                self.image,
                "/io/in/doc.pdf", "/io/out/doc.md",
            ]
            try:
                proc = self._run(args)
            except subprocess.TimeoutExpired as exc:
                self._kill(name)
                raise BackendUnavailable(f"docling 容器超时（{self.timeout}s）") from exc

            if proc.returncode != 0:
                err = (proc.stderr or b"").decode("utf-8", errors="replace")[-800:]
                raise BackendUnavailable(f"docling 容器退出码 {proc.returncode}：{err}")

            md = out_dir / "doc.md"
            if not md.is_file():
                candidates = sorted(out_dir.rglob("*.md"), key=lambda p: p.stat().st_size, reverse=True)
                if not candidates:
                    raise BackendUnavailable("docling 容器没有生成 .md（检查镜像内 CLI 参数）")
                md = candidates[0]
            text = md.read_text(encoding="utf-8", errors="replace").strip()
            if not text:
                raise BackendUnavailable("docling 产出的 Markdown 为空")
            return text


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
        # 默认走 Docker：Windows WDAC 会拦 Docling 的 .pyd；容器内 Linux .so 不受影响。
        return DoclingDockerBackend()
    if name == "docling-local":
        # 仅在没有 WDAC 的机器 / 测试里用；Windows 本机可能报 _pava_pybind DLL 错误。
        return DoclingBackend()
    if name == "mineru":
        return MinerUBackend()
    raise BackendUnavailable(
        f"未知 PDF_BACKEND={name!r}（支持 pypdf/docling/docling-local/mineru/auto）"
    )
