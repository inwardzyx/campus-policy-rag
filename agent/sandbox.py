# -*- coding: utf-8 -*-
"""受限代码执行：给 agent 加一个「能算数」的工具，但把容器关死。

## 为什么要有它（不是为了面试好看）

制度问答里有一类题**一步管线做不了**：
「工龄十二年、今年病假两个月，年假折算多少钱？」——要先查条款（折算系数、基数），
再做日期/金额计算，最后把数字和出处一起给出来。纯检索 agent 只能查到条款，
算不了数；让它"心算"就等于允许它编数字。

⇒ 这个工具补的是**真实能力缺口**：检索型 agent → 检索 + 计算混合 agent。

## 为什么用容器 + 五道限制

模型生成的代码是**不可信输入**。只要它能联网、能写盘、能无限吃内存，
一次生成就能把宿主机拖坏或把数据送出去。

    --network=none         断网（外发数据 / 下载脚本都不行）
    --read-only            根文件系统只读（写盘直接 EROFS）
    --memory=128m          内存上限（超了 OOM kill / MemoryError）
    --cpus=0.5 --pids-limit=64   防 fork 炸弹与 CPU 打满
    -u 65534               nobody 运行（不是 root）
    --tmpfs /tmp:size=16m  唯一可写处，且只有 16MB

## 默认关闭

`SANDBOX_ENABLED=1` 才把这个工具暴露给模型。理由：多一个能执行代码的工具就多一类风险，
默认开等于把"实验特性"变成"默认攻击面"。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

IMAGE = os.environ.get("SANDBOX_IMAGE", "python:3.12-slim")
DEFAULT_TIMEOUT = 5.0
MAX_OUTPUT = 4000          # 输出截断：别让一次 print 循环把上下文吃掉


def enabled() -> bool:
    """开关：默认 off。每次现读环境变量（测试要能开关）。"""
    return (os.environ.get("SANDBOX_ENABLED") or "0").strip().lower() in ("1", "true", "yes", "on")


def build_cmd() -> list[str]:
    """docker 命令（不含代码）：代码从 **stdin** 喂进去，避免 Windows 下的引号地狱。"""
    return [
        "docker", "run", "--rm", "-i",
        "--network=none",
        "--read-only",
        "--memory=128m",
        "--cpus=0.5",
        "--pids-limit=64",
        "-u", "65534",
        "--tmpfs", "/tmp:size=16m",
        IMAGE, "python", "-",
    ]


def classify(stdout: str, stderr: str, exit_code: int | None, timed_out: bool) -> str:
    """把一次执行归成六类之一。**纯函数**：判据要能被单测钉住，不依赖 docker。"""
    if timed_out:
        return "timeout"
    text = f"{stdout}\n{stderr}"
    if exit_code == 137 or "MemoryError" in text or "Killed" in text:
        return "oom"
    if any(m in text for m in ("Temporary failure in name resolution", "Name or service not known",
                               "Network is unreachable", "Connection refused", "urlopen error")):
        return "network_blocked"
    # ★ 写盘有两种拦法，都算"写不了"，但报告里要保留原文：
    #   EROFS（只读挂载）与 EACCES（nobody 没有权限）。
    #   第一版只认 EROFS，探针实测拿到的是 `Permission denied` ⇒ 判成 error、误报失败。
    if "Read-only file system" in text or "EROFS" in text or "Permission denied" in text:
        return "write_blocked"
    if exit_code == 0:
        return "ok"
    return "error"


def run_python(code: str, *, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """在受限容器里跑一段 Python，返回结构化结果（永不抛：调用方是 agent 循环）。"""
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(build_cmd(), input=code, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
        stdout, stderr, code_ = proc.stdout or "", proc.stderr or "", proc.returncode
        timed_out = False
    except subprocess.TimeoutExpired as e:
        stdout = (e.stdout or "") if isinstance(e.stdout, str) else ""
        stderr = (e.stderr or "") if isinstance(e.stderr, str) else ""
        code_, timed_out = None, True
    except FileNotFoundError:
        return {"status": "docker_missing", "stdout": "", "stderr": "找不到 docker", "exit_code": None,
                "ms": 0, "verdict": "error"}

    verdict = classify(stdout, stderr, code_, timed_out)
    return {
        "status": verdict,
        "verdict": verdict,
        "stdout": stdout[:MAX_OUTPUT],
        "stderr": stderr[:MAX_OUTPUT],
        "exit_code": code_,
        "ms": int((time.perf_counter() - t0) * 1000),
        "truncated": len(stdout) > MAX_OUTPUT,
    }


if __name__ == "__main__":
    r = run_python("print(1 + 1)")
    print(r)
