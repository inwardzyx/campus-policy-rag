# -*- coding: utf-8 -*-
"""render_regression_report.py 的变异测试。

★ 为什么需要它：
  这份报告生成器里有 4 条"遇到边界情况不许输出假结论"的守卫
  （无应答数据不渲染 0/0 表、单遍不判定回归、单遍不报底噪、用例数动态读）。
  守卫是那种"加了就不容易看见"的东西——改坏它不会报错，
  只会安静地开始输出看起来很像结论的假东西。所以必须变异测试盯着。

跑法：
    D:/Python-project/.venv/Scripts/python.exe agent/_mut/test_report_renderer.py

判据：4 个用例的判定必须逐字匹配 EXPECTED 里写的。
不符就抛异常（这就是"改坏判据时会被抓住"的那道网）。
"""

from __future__ import annotations

import importlib
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_AGENT = os.path.join(_ROOT, "agent")
sys.path.insert(0, _AGENT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ★★ 假数据写到【独立临时路径】，绝不碰 agent/_refusal_regression.json。
#   那个文件是正跑的回归每遍末尾要写的目标 —— 一旦被假数据覆盖，
#   回归会以为自己已经跑过而跳过（续跑逻辑），或直接把假结果当实测。
#   这就是"不要污染基线"的同一件事，只是这次污染源是我自己的测试。
SRC = os.path.join(_ROOT, "agent", "_mut", "_fake_regression.json")
OUT_MD = os.path.join(_ROOT, "agent", "_mut", "_fake_report.md")

# 每个用例 → 判定行里必须出现的片段 / 必须不出现的片段
EXPECTED = {
    "M1_only_refuse": {
        "must": ["未发现回归", "_本次只跑了拒答题集"],
        # ★ 只查"应答表有没有被渲染"，不查全文有没有 "0.0%" 这个字符串——
        #   守卫的解释文字里就含「0.0%」（"0/0 会显示成 0.0%"），
        #   那是守卫在工作。第一版判据写成查全文，
        #   结果把自己的说明文字判成了故障 —— 判据抓到了"别的"。
        "must_not": ["| 直打 `/chat` | ", "| 走 agent |", "## 四、工具调用"],
    },
    "M2_single_run": {
        "must": ["本次不作判定", "底噪无从量化"],
        "must_not": ["**发现回归**"],  # 单遍不许判回归
    },
    "M3_real_regression": {
        "must": ["**发现回归**"],
        "must_not": ["未发现回归"],
    },
    "M4_within_noise": {
        "must": ["未发现回归，但低于基线"],
        "must_not": ["**发现回归**"],
    },
}


def run_case(name: str, data: dict) -> tuple[bool, list[str]]:
    import render_regression_report as rr
    importlib.reload(rr)          # ★ 每次重载，避免模块级缓存旧逻辑
    with open(SRC, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)

    rr.SRC, rr.OUT = SRC, OUT_MD
    rc = rr.main()
    md = open(OUT_MD, encoding="utf-8").read()

    problems = []
    if rc != 0:
        problems.append(f"退出码 {rc}")
    for s in EXPECTED[name]["must"]:
        if s not in md:
            problems.append(f"缺：{s!r}")
    for s in EXPECTED[name]["must_not"]:
        if s in md:
            problems.append(f"不该出现：{s!r}")
    return (not problems), problems


def main() -> int:
    from make_fake import build

    cases = build()
    bad = []
    print(f"变异测试：{len(cases)} 个用例（守卫是否会被改坏）\n")
    for name, data in cases.items():
        ok, problems = run_case(name, data)
        print(f"  {'[OK]  ' if ok else '[!!]  '}{name}")
        for p in problems:
            print(f"          {p}")
            bad.append((name, p))

    # 收尾：清掉假数据，别把 M4 的 60.7% 当成实测结果留在仓库里
    if os.path.exists(SRC):
        os.remove(SRC)
    if os.path.exists(OUT_MD):
        os.remove(OUT_MD)
    print("\n[已清理] 假数据与它生成的报告都已删除")
    if os.path.exists(SRC):
        print("[!!] 清理失败：假数据还在")
        bad.append(("cleanup", "SRC 残留"))

    print()
    if bad:
        print(f"[!!] {len(bad)} 处不符 —— 守卫被改坏了")
        return 1
    print(f"[OK] 全部 {len(cases)} 个用例通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())