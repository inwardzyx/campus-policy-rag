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
#
# ★★ 第六版起判定依据从「拒答率 vs 基线」换成【硬答率】——
#   所以 M3/M4 的语义也跟着换了：
#     M3  真硬答（no_hit 却作答）→ 必须报"发现硬答 N 题"
#     M4  有 no_hit 但硬答为 0 → 必须报"未发现硬答"
#   之前这两条还在断言旧文案（"发现回归"/"未发现回归，但低于基线"），
#   属于【测试过期】而不是守卫坏了 —— 判据抓到了"别的"，
#   排查顺序：先确认是判据过期，再去改被测代码。
EXPECTED = {
    "M1_only_refuse": {
        "must": ["未发现硬答", "_本次只跑了拒答题集"],
        # ★ 只查"应答表有没有被渲染"，不查全文有没有 "0.0%" 这个字符串——
        #   守卫的解释文字里就含「0.0%」（"0/0 会显示成 0.0%"），
        #   那是守卫在工作。第一版判据写成查全文，
        #   结果把自己的说明文字判成了故障 —— 判据抓到了"别的"。
        "must_not": ["| 直打 `/chat` | ", "| 走 agent |"],
    },
    "M2_single_run": {
        "must": ["本次不作判定", "底噪无从量化"],
        "must_not": ["**发现硬答"],
    },
    "M3_real_hard_answer": {
        "must": ["**发现硬答 6、5、4 题**", "10.0%"],
        "must_not": ["**未发现硬答**"],
    },
    "M4_no_hard_answer": {
        "must": ["**未发现硬答**"],
        "must_not": ["**发现硬答", "本次不作判定"],
    },
    "M5_single_run_with_hard": {
        # 单遍：硬答数照实报，但不能下"回归"结论。
        # ★ 断言用"观测到硬答"而不是"发现硬答"：
        #   单遍 verdict 刻意换了个词 —— "发现"是结论，"观测到"只是事实。
        #   这里如果照抄多遍那次的措辞，判据就会把
        #   "该避开的结论词出现了" 当成合格，属于判据自己没对齐语义。
        "must": ["本次不作判定", "观测到硬答 6 题"],
        "must_not": ["**未发现硬答**", "这才是本次验收要找的回归"],
    },
    "M6_direct_split": {
        # ★ 这条是被反向验证逼出来的，两次才写对：
        #   1) 把直打拆解段整段删掉后，M1~M5 依然全绿 → 覆盖缺口。
        #   2) 补断言后仍然抓不住 —— 因为断言用的字符串
        #      「压根不调模型」「服务替模型拒的」在第三节里也各出现过一次，
        #      删掉拆解段它们照样命中。
        #      ⇒ 断言必须锁【只有那张表才有】的结构，
        #         拿"别处也有的词"当判据就是在判"别的"。
        #      这正是本项目写进报告的那条教训的现场复现。
        "must": ["| 遍 | 服务硬短路（未调模型） | 其中拒答 | 真调了模型 |",
                 "剔掉服务硬短路后"],
        "must_not": [],
    },
    #★ 2026-10-13 新增：逐遍硬答题号明细 + 跨遍稳定性。
    #   这一节是响应"报告把 15.8%→12.5%→10.5% 叙述成在收敛"这个洞加的，
    #   而它自己也有一个覆盖缺口：`make_fake` 原来 agent 侧逐题是空列表，
    #   ⇒ 这一节在全部用例里都走"未算出"分支，测了个寂寞。
    #   反向验证（删掉这一整节）必须报漏检，否则就是又一个假绿。
    "M7_stability": {
        #★ 断言必须锁【只有这一节才有】的结构（表头 + 那句结论）。
        #   "并集"这个词报告别处也出现过，只拿它当判据就是在判"别的"。
        #   ⚠️ 这份假数据里三遍题号【两两完全不重叠】⇒ 交集为空，
        #     而真数据是"交集 1 题"。两个形状都要能过同一套判据，
        #     否则判据就是照着真数据写的。
        "must": ["逐遍硬答题号明细",
                 "| 遍 | 硬答题数 | 题号 |",
                 "**每遍都硬答的只有 0 题**",
                 "不能读成改善",
                 "两遍之间翻转的题数"],
        "must_not": ["跨遍稳定**"],
    },
    # ★ 负向用例：三遍题号【完全一致】时，绝不能说"不能读成改善" ——
    #   那才是真的稳定存在，此时该警告的反而是"只有 1 题稳定"之外的另一件事。
    #   没有这条，"该报警时报、不该报时报"就是同一条判据的两个方向。
    "M8_stable_across_runs": {
        "must": ["**每遍都硬答的只有 3 题**", "q0", "q1", "q2"],
        "must_not": ["不能读成改善"],
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

    # ★★★ 用例缺失必须算失败（2026-10-13 补）。
    #   这个洞是反向验证抓出来的：把新加的那一整节渲染删掉之后，
    #   对应的用例 M7 **直接从输出里消失了**，而收尾仍然打印
    #   「全部 N 个用例通过」、退出码仍是 0。
    #   根因：循环遍历的是 make_fake.build() 返回的用例，
    #   而【断言】住在 EXPECTED 里 —— 两边没有对账，
    #   断言没配数据就等于这条判据从来没跑过。
    #   ⇒ 判据的方向必须反过来：以 EXPECTED 为准，
    #     少一条用例就报错，而不是"有多少跑多少"。
    missing_cases = [k for k in EXPECTED if k not in cases]
    missing_assert = [k for k in cases if k not in EXPECTED]
    for k in missing_cases:
        bad.append((k, f"★EXPECTED 里有断言，但 make_fake 没造这份数据"
                         f"⇒ 这条判据根本没跑（它从来没被验证过）"))
    for k in missing_assert:
        bad.append((k, "★造了数据但没有断言 ⇒ 跑了但没人验"))

    print(f"变异测试：{len(cases)} 个用例"
          f"（断言 {len(EXPECTED)} 条）\n")
    for name, data in cases.items():
        if name not in EXPECTED:
            continue                      # 没有断言的，上面已记为 bad
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
    print(f"[OK] 全部 {len(cases)} 个用例通过"
          f"（断言 {len(EXPECTED)} 条，无缺失）")
    return 0


if __name__ == "__main__":
    sys.exit(main())