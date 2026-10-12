# -*- coding: utf-8 -*-
"""核对 report-token-cost.md 里声称的每个数字，对得上 agent/_token_cost.json 才算过。

★ 为什么要固化下来（而不是每次手搓一次性核对脚本）：
  这一版报告写完，我手工核对了 27 项，**在正文里抓到自己 3 处算错**：
    · 缓存命中率 67.1% → 实际 66.5%
    · output 差值写 46→ 实际 47
    · "52 次 LLM 调用" → 实际 53（13×3 + 7×2）
  三处都是"手写算式"的必然产物。而"写完自己读一遍"这种核对方式
  恰恰最容易漏，因为它读的是自己刚写的句子，不是数据。

⇒ 所以凡���是"正文里出现了由数据算出来的数"，就该有一条判据盯着。
  这条判据盯的是【报告 ↔ JSON 的逐格一致】，不是"报告里有数字"。

跑法（仓库根目录）：
    python -m scripts.check_token_report

★ 先跑 agent/token_cost.py 生成 _token_cost.json，否则本脚本直接退出并说清为什么
  —— 不拿别的数据凑，也不"没数据就当过了"。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# ★★ 2026-10-12 补：stdout 必须显式转 UTF-8。
#   这是同一个病的**第 4 次**（前三次：check_readme_numbers.py / mutate_check_numbers.py /
#   check_artifact_sanity.py），而这次最难看 —— 它**拦住了推送，还报了一句假话**：
#     pre-push 里跑本脚本时没有 PYTHONIOENCODING，stdout 是管道 + GBK，
#     于是最后那句 print("✓ …") 的 ✓ 编不出来 ⇒ UnicodeEncodeError ⇒ 退出码 1
#     ⇒ 钩子打出「★ token 报告里的数字与实测数据对不上（上面列出的那些）」。
#     而**数字其实全对**，它只是崩在打印成功消息上。
#   ★ 教训：修法不会自动传播。前三个脚本各补了一行，第四个（新加的）就没有 ——
#     所以现在配了一条系统性防线：tests/test_tooling_encoding.py
#     （用非 UTF-8 的 stdout 跑一遍钩子会调的所有脚本，谁崩谁红）。
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "agent" / "_token_cost.json"
REPORT = ROOT / "evalset" / "report-token-cost.md"

# ★ 表格行必须以题号开头。| 20 | ... | 或 | **20** | 这类均值行不能被当成逐题行，
#   否则均值也会被拿去和某一题比，报出一堆假错。
ROW_RE = re.compile(r"^\|\s*\*{0,2}(h\d+)\*{0,2}\s*\|")


def strip_md(s: str) -> str:
    """去掉粗体/反引号，只留值本身。

    ★ 为什么必须剥：报告里拒答题那一列写成 | **2** |（加粗强调），
      直接 int() 会 ValueError —— 而这种报错看起来像"脚本报错了"，
      不会像"文档格式和解析器约定不一致"，很难查。
    """
    return s.replace("*", "").replace("`", "").strip()


def load() -> tuple[dict, dict] | None:
    if not DATA.exists():
        return None
    data = json.loads(DATA.read_text(encoding="utf-8"))
    direct = {r["id"]: r for r in data.get("direct", []) if not r.get("error")}
    agent = {r["id"]: r for r in data.get("agent", []) if not r.get("error")}
    if not direct or not agent:
        return None
    return direct, agent


def parse_rows(text: str) -> list[list[str]]:
    """把逐题表格行解析成单元格列表。"""
    out = []
    for line in text.splitlines():
        if ROW_RE.match(line):
            out.append([strip_md(c) for c in line.strip().strip("|").split("|")])
    return out


def main() -> int:
    if not DATA.exists():
        print(f"✗ 缺 {DATA.relative_to(ROOT)} —— 先跑 agent/token_cost.py")
        print("  （不拿别的数据凑：这份 JSON 就是本报告的唯一数据源）")
        return 1
    loaded = load()
    if loaded is None:
        print("✗ _token_cost.json 里没有可用的 direct/agent 明细（全是 error？）")
        return 1
    direct, agent = loaded

    text = REPORT.read_text(encoding="utf-8")
    rows = parse_rows(text)
    if not rows:
        #★ 这里要说清是"文件里真的一行都没有"还是"表格头变了"。
        #  原来只有一句兜底文案，结果删掉一行之后它报的还是这一句 ——
        #  判据抓到了，但诊断把方向指偏了（"表格结构变了"其实什么都没变）。
        if "| 题号 |" in text:
            print(f"✗ {REPORT.name} 里有表头，但一行逐题数据都没解析到")
            print("  ⇒ 大概率是表格列数变了，ROW_RE 没匹配上。去看一眼那张表。")
        else:
            print(f"✗ {REPORT.name} 里找不到逐题表格（连表头都没有）")
        return 1

    bad: list[str] = []

    # ---- 1. 逐格核对 ----
    for c in rows:
        if len(c) < 10:
            bad.append(f"  {c[0]}：只解析出 {len(c)} 列（期望 10 列），表格结构变了")
            continue
        qid, typ, d_calls, d_total, a_calls, a_in, a_out, a_total, a_cache, cost = c[:10]
        if qid not in direct or qid not in agent:
            bad.append(f"  {qid}：JSON 里没有这道题（报告里有多余行，或题集变了？）")
            continue
        u = direct[qid]["usage_raw"]
        au = agent[qid]["usage"]
        for label, claimed, real in (
            ("直打调用次数", d_calls, u["n_calls"]),
            ("直打 total", d_total, u["total_tokens"]),
            ("agent 调用次数", a_calls, au["n_calls"]),
            ("agent input", a_in, au["input"]),
            ("agent output", a_out, au["output"]),
            ("agent total", a_total, au["total"]),
            ("缓存命中", a_cache, au["cache_hit"]),
        ):
            try:
                if int(claimed) != int(real):
                    bad.append(f"  {qid} {label}：报告写 {claimed}，JSON 是 {real}")
            except ValueError:
                bad.append(f"  {qid} {label}：报告写 {claimed!r}，不是整数")
        try:
            if abs(float(cost) - au["cost"]) > 1e-6:
                bad.append(f"  {qid} 成本：报告写 {cost}，JSON 是 {au['cost']}")
        except ValueError:
            bad.append(f"  {qid} 成本：报告写 {cost!r}，不是数字")

        # ★ 题型不能是我手工贴的标签，必须与 n_calls 自洽：
        #   n_calls<3 ⇒ 没调生成 ⇒ 必然是拒答
        want = "拒答" if u["n_calls"] < 3 else "应答"
        if typ != want:
            bad.append(f"  {qid} 题型：报告写「{typ}」，但 n_calls={u['n_calls']}⇒应为「{want}」")

    # ---- 2. 汇总数字核对（正文里那些"平均""占比"）----
    ids = [r[0] for r in rows]

    # ★ 覆盖度先查：少一行题，底下的均值就全都变了，
    #   再去比均值会报出一堆【假错】（"均值对不上"其实是"少了一道题"）。
    #   所以缺题必须单独、优先报出来——它才是根因。
    only_data = [k for k in direct if k in agent]
    missing_in_report = [k for k in only_data if k not in ids]
    extra_in_report = [k for k in ids if k not in direct or k not in agent]
    for k in missing_in_report:
        bad.append(f"  {k}：JSON 里有这道题，报告表格里没有 —— 均值类数字会跟着变")
    for k in extra_in_report:
        bad.append(f"  {k}：报告表格里有，但 JSON 里没有")

    d_list = [direct[i]["usage_raw"] for i in ids if i in direct]
    a_list = [agent[i]["usage"] for i in ids if i in agent]
    # ★ 覆盖不全时【不】再比均值——那些数一定对不上，会淹没上面那条真正的错误
    if missing_in_report or extra_in_report:
        print(f"✗ {len(bad)} 处对不上：")
        print("\n".join(bad))
        return 1
    if len(d_list) == len(rows) == len(a_list):
        n = len(d_list)
        # ★ a_list 里每一项【已经】是 usage 本体（agent[i]["usage"]），
        #   所以下面直接 x["total"] —— 我第一版在这里又写了一遍 x["usage"]["total"]，
        #   同一类错误在同一文件犯两次。把它集中到这两个列表的构造处，后面只认一种形状。
        d_tot = sum(x["total_tokens"] for x in d_list) / n
        a_tot = sum(x["total"] for x in a_list) / n
        a_in = sum(x["input"] for x in a_list) / n
        a_out = sum(x["output"] for x in a_list) / n
        a_cache = sum(x["cache_hit"] for x in a_list) / n
        d_in = sum(x["input_tokens"] for x in d_list) / n
        d_out = sum(x["output_tokens"] for x in d_list) / n
        hits = [x for x in d_list if x["n_calls"] < 3]
        miss = [x for x in d_list if x["n_calls"] >= 3]

        # 声称值 → 用同一个函数从数据算出来。凡是报告里写了 x，
        # 就在这里用 (名称, 报告里写的数, 算出来的数) 三元组登记。
        claims = [
            ("直打均值", 957, round(d_tot)),
            ("agent 均值", 1906, round(a_tot)),
            ("拒答 input 均值", 634,
             round(sum(x["input_tokens"] for x in hits) / len(hits)) if hits else 0),
            ("应答 input 均值", 831,
             round(sum(x["input_tokens"] for x in miss) / len(miss)) if miss else 0),
            ("拒答题数", 7, len(hits)),
            ("应答题数", 13, len(miss)),
            ("output 占比%×10", 127,
             round(a_out / a_tot * 1000) if a_tot else 0),
            ("缓存命中率%×10", 665, round(a_cache / a_in * 1000) if a_in else 0),
            ("直打侧调用总次数", 53, sum(x["n_calls"] for x in d_list)),
            ("agent 侧调用总次数", 40, sum(x["n_calls"] for x in a_list)),
        ]
        for name, claimed, real in claims:
            if claimed != real:
                bad.append(f"  汇总「{name}」：报告写 {claimed}，数据算出 {real}")
    else:
        bad.append(f"  行数对不上：报告 {len(rows)} 行，"
                   f"direct {len(d_list)} / agent {len(a_list)}")

    # ---- 3. missing_calls 必须是 0（这批数据才能当真值用）----
    if sum(x.get("missing_calls", 0) for x in d_list) != 0:
        bad.append("  直打侧 missing_calls 不为 0—— 均值偏低，不能当真值用")

    if bad:
        print(f"✗ {len(bad)} 处对不上：")
        print("\n".join(bad))
        return 1
    print(f"✓ {REPORT.relative_to(ROOT)} 的 {len(rows)} 行逐题数字 + "
          f"10 项汇总，均与 {DATA.name} 一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())