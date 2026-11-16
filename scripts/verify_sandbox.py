# -*- coding: utf-8 -*-
"""把「代码执行」的四种越界做成**第五份产物**：`experiments/verify-sandbox.md`。

## 为什么必须落成产物（而不是"我试过了"）

面试时那句话是："我量过四种越界（超时 / 内存 / 网络 / 写盘）都能被拦住，才敢开这个工具。"
这句话如果没有落盘的命令 + 真实输出，就只是一句口口相传 ——
和"报告里的数字手写的"是同一类问题。

## 跑法

```powershell
D:\\Python-project\\.venv\\Scripts\\python.exe scripts\\verify_sandbox.py
```

- 五个探针：正常计算 / 超时 / 内存 / 网络 / 写盘
- 每个探针都记录：**命令、原始输出、判定、期望、是否通过**
- 任一探针与期望不符 ⇒ 退出码 1（**不许报绿**）
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.sandbox import IMAGE, build_cmd, run_python        # noqa: E402

OUT = ROOT / "experiments" / "verify-sandbox.md"

PROBES = [
    ("正常计算", "print(round((5000/21.75)*3, 2))", "ok",
     "能算数：这条链路的用处（查条款 → 算数字）"),
    ("超时", "while True:\n    pass", "timeout",
     "死循环必须被杀（不挂住 agent 循环）"),
    ("内存", "x = bytearray(400 * 1024 * 1024)\nprint(len(x))", "oom",
     "超 128MB 必须失败（不拖垮宿主机）"),
    ("网络", "import urllib.request\nurllib.request.urlopen('http://example.com', timeout=2).read()",
     "network_blocked", "断网：外发数据 / 下载脚本都不行"),
    ("写盘", "open('/etc/passwd', 'a').write('x')", "write_blocked",
     "改不了系统文件（EACCES 或 EROFS，报告里保留原文）"),
    ("根挂载只读", "print([l for l in open('/proc/mounts') if ' / ' in l][0].strip())", "ok",
     "结构证据：`/` 的挂载选项里必须带 ro"),
]


def _image_id() -> str:
    try:
        r = subprocess.run(["docker", "image", "inspect", "--format", "{{.Id}}", IMAGE],
                           capture_output=True, text=True, timeout=30)
        return (r.stdout or "").strip()[:19] or "(拿不到)"
    except Exception:                                    # noqa: BLE001
        return "(拿不到)"


def _fmt(s: str, n: int = 300) -> str:
    s = (s or "").strip().replace("```", "'''")
    return (s[:n] + "…") if len(s) > n else (s or "（空）")


def main() -> int:
    rows = []
    failed = 0
    t0 = time.time()
    for name, code, expect, why in PROBES:
        r = run_python(code)
        ok = r["verdict"] == expect
        if name == "根挂载只读":
            # ★ 这条不看 verdict（它一定是 ok），要看输出里 `/` 的挂载选项有没有 ro
            ok = "ro" in (r["stdout"] or "")
            if not ok:
                r["verdict"] = "root_not_readonly"
        failed += 0 if ok else 1
        rows.append((name, code, r, expect, ok, why))
        print(f"[{'ok' if ok else 'FAIL'}] {name:<6} 期望 {expect:<15} 实际 {r['verdict']:<15} {r['ms']}ms")

    cmd = " ".join(build_cmd()[:build_cmd().index("python")])
    lines = [
        "# 代码执行沙箱：四种越界实测（第五份产物）",
        "",
        "> ★ 本文件由 `scripts/verify_sandbox.py` 生成，**不要手改**（下次生成会覆盖）。",
        f"> 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}　用时 {int(time.time() - t0)} 秒",
        f"> 镜像：`{IMAGE}`（id `{_image_id()}`）",
        "",
        "## 一句话结论",
        "",
        (f"六个探针全部符合期望：**正常计算能算、四种越界全被拦住**；另有一条结构证据（`/` 挂载为 ro）。"
         if failed == 0 else
         f"**{failed} 个探针与期望不符** —— 这个沙箱不能开（先修，别上线）。"),
        "",
        "## 限制是怎么下的（同一条命令，五个探针共用）",
        "",
        "```text",
        cmd + " python -",
        "```",
        "",
        "| 限制 | 拦的是什么 |",
        "|---|---|",
        "| `--network=none` | 外发数据、下载脚本 |",
        "| `--read-only` + `--tmpfs /tmp:size=16m` | 改系统文件；只能写 16MB 临时区 |",
        "| `--memory=128m` | 一次分配 400MB 会 OOM |",
        "| `--cpus=0.5` / `--pids-limit=64` | CPU 打满、fork 炸弹 |",
        "| `-u 65534` | 以 nobody 运行，不是 root |",
        "",
        "## 六个探针（5 个行为探针 + 1 条结构证据）",
        "",
        "| 探针 | 期望 | 实测 | 通过 | 为什么测它 |",
        "|---|---|---|---|---|",
    ]
    for name, code, r, expect, ok, why in rows:
        lines.append(f"| {name} | `{expect}` | `{r['verdict']}`（{r['ms']} ms） | "
                     f"{'✅' if ok else '❌'} | {why} |")

    lines += ["", "## 每个探针的代码与原始输出", ""]
    for name, code, r, expect, ok, why in rows:
        lines += [
            f"### {name}（期望 `{expect}`，实测 `{r['verdict']}`）",
            "",
            "代码：",
            "```python",
            code,
            "```",
            "",
            f"stdout（{r['exit_code']}）：",
            "```text",
            _fmt(r["stdout"]),
            "```",
            f"stderr（截断到 300 字）：",
            "```text",
            _fmt(r["stderr"]),
            "```",
            "",
        ]

    lines += [
        "## 判定规则（`agent/sandbox.py::classify`）",
        "",
        "| 判定 | 依据 |",
        "|---|---|",
        "| `timeout` | 进程超时（`subprocess.TimeoutExpired`） |",
        "| `oom` | 退出码 137 / `MemoryError` / `Killed` |",
        "| `network_blocked` | DNS 失败 / `Network is unreachable` / `Connection refused` |",
        "| `write_blocked` | `Read-only file system` |",
        "| `ok` | 退出码 0 |",
        "| `error` | 其它（**不当成功**） |",
        "",
    ]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n已写 {OUT}")
    print("结论：", "全部符合期望" if failed == 0 else f"{failed} 个不符")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
