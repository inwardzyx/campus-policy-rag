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


def load() -> tuple[dict, dict, list] | None:
    if not DATA.exists():
        return None
    data = json.loads(DATA.read_text(encoding="utf-8"))
    direct = {r["id"]: r for r in data.get("direct", []) if not r.get("error")}
    agent = {r["id"]: r for r in data.get("agent", []) if not r.get("error")}
    if not direct or not agent:
        return None
    # ★★ 2026-10-13 补：`runs`（--runs N 的逐遍账）以前在校验范围外。
    #   实测：整张三遍表从报告里删掉，本脚本照样 exit 0。
    #   而那批数据里 run2 的 tool_usage_missing=2 —— 那个字段被吞掉的话，
    #   没人知道"含工具 3.40 倍"本身是**低估**的（那 2 次没报上账）。
    #   ⇒ 这里返回原始列表，让 main() 去逐格对，别在 load 里顺手过滤掉异常遍。
    runs = [r for r in data.get("runs", []) if isinstance(r, dict)]
    return direct, agent, runs


def parse_rows(text: str) -> list[list[str]]:
    """把逐题表格行解析成单元格列表。"""
    out = []
    for line in text.splitlines():
        if ROW_RE.match(line):
            out.append([strip_md(c) for c in line.strip().strip("|").split("|")])
    return out


def parse_runs_table(text: str) -> list[tuple[int, list[str]]]:
    """把三遍账那张表解析成 [(遍号, 六格), ...]。

    ★★ 为什么要单独一个解析器，不能复用 `parse_rows`：
      两张表都是 markdown 行、都用 `|`，但列数完全不同
      （逐题表 10 列、题号是 h1..h20；三遍表 6 列、首列是纯数字遍号）。
      `ROW_RE` 要求首列像 `h\\d+`，所以它天然抓不到三遍表。

    ★★ 为什么容忍行首的 "> "：
      三遍表整块被包在 markdown 引用块里（每行都是 `> | 1 | 961 | ... |`）——
      因为它整个躺在**引用块**的更正说明内部，是别人更正块的一部分。
      原来直接 `s.startswith("|")`，结果一行都抓不到，而函数返回空列表，
      上层报的却是"表被删了" —— **诊断把方向指偏了**（表明明在）。
      这个坑与 parse_rows 那段注释里记的是同一类。
    """
    out: list[tuple[int, list[str]]] = []
    for line in text.splitlines():
        s = line.strip()
        # 剥掉 markdown 引用/列表前缀（`> `、`> - `、`- `），再剥表格首尾的 `|`
        s = re.sub(r"^(?:>\s*)?(?:[-*]\s+)?", "", s)
        if not s.startswith("|"):
            continue
        cells = [strip_md(c) for c in s.strip("|").split("|")]
        if len(cells) != 6 or not re.fullmatch(r"\d+", cells[0]):
            continue
        # 末两格必须是倍率（含 × 或纯小数），避免把别的 6 列表当三遍表
        if not (("×" in cells[4] or re.fullmatch(r"[\d.]+", cells[4]))
                and ("×" in cells[5] or re.fullmatch(r"[\d.]+", cells[5]))):
            continue
        out.append((int(cells[0]), cells))
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
    direct, agent, runs = loaded

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

        # ★★ 2026-10-13 改：声称值**从报告正文里抽**，不再在这里硬编码。
        #
        #   原来这里是 (名称, 报告里写的数, 算出来的数) —— 中间那个数是【抄进来的常量】。
        #   后果：它其实是"报告正文的副本"，正文漂了它不知道。
        #   实测（同一份报告）：正文写着缓存命中率 **66.5%**（22139/33281），
        #   而数据早就是 **61.2%**（20475/33455）—— 本脚本照样 exit 0。
        #   ⇒ 一个把被检查对象抄一份到自己家里的检查器，检查的是它自己。
        #
        #   现在：正则从**报告文本**里抠出它写的那个数（抠不到就报"正文里抽不到"），
        #   再和从 JSON 现算的值比。正文一漂就红。
        txt = REPORT.read_text(encoding="utf-8")

        def grab(pat: str, scale: int = 1):
            """从报告正文里抠出它写的那个数；抠不到返回 None（→ 报错，不静默放过）。"""
            m = re.search(pat, txt)
            return None if not m else int(round(float(m.group(1)) * scale))

        claims = [
            ("直打均值",
             grab(r"\*\*均值：直打 (\d+) token/题"), round(d_tot)),
            ("agent 均值",
             grab(r"\*\*均值：直打 \d+ token/题\s*agent (\d+) token/题"), round(a_tot)),
            ("拒答 input 均值",
             grab(r"\| 硬短路拒答（没调生成） \| \d+ \| (\d+) \|"),
             round(sum(x["input_tokens"] for x in hits) / len(hits)) if hits else 0),
            ("应答 input 均值",
             grab(r"\| 调了生成作答 \| \d+ \| (\d+) \|"),
             round(sum(x["input_tokens"] for x in miss) / len(miss)) if miss else 0),
            ("拒答题数",
             grab(r"\| 硬短路拒答（没调生成） \| (\d+) \|"), len(hits)),
            ("应答题数",
             grab(r"\| 调了生成作答 \| (\d+) \|"), len(miss)),
            ("output 占比%×10",
             grab(r"output 占比 ([\d.]+)%", 10),
             round(a_out / a_tot * 1000) if a_tot else 0),
            ("缓存命中率%×10",
             grab(r"缓存命中率 ([\d.]+)%", 10),
             round(a_cache / a_in * 1000) if a_in else 0),
            ("直打侧调用总次数",
             grab(r"直打侧 (\d+) 次 LLM 调用"), sum(x["n_calls"] for x in d_list)),
            ("agent 侧调用总次数",
             grab(r"agent 侧 (\d+) 次调用"), sum(x["n_calls"] for x in a_list)),
        ]
        # ★★ 表格第 4/5 格（output / total）以前从不被校。
        #   实测漏检经过：那一列从"平均 total"被换成了"平均 output"（表头没改），
        #   本脚本 exit 0 —— 而 155/216 恰好是**算对的 output 均值**，
        #   唯一错的是表头。这种错比数字错更难看见，因为数字本身是对的。
        #   ⇒ 现在 output 与 total 各自抠、各自对：表头与内容不符必然报红。
        claims += [
            ("拒答 output 均值",
             grab(r"\| 硬短路拒答（没调生成） \| \d+ \| \d+ \| (\d+) \|"),
             round(sum(x["output_tokens"] for x in hits) / len(hits)) if hits else 0),
            ("应答 output 均值",
             grab(r"\| 调了生成作答 \| \d+ \| \d+ \| (\d+) \|"),
             round(sum(x["output_tokens"] for x in miss) / len(miss)) if miss else 0),
            ("拒答 total 均值",
             grab(r"\| 硬短路拒答（没调生成） \| \d+ \| \d+ \| \d+ \| (\d+) \|"),
             round(sum(x["total_tokens"] for x in hits) / len(hits)) if hits else 0),
            ("应答 total 均值",
             grab(r"\| 调了生成作答 \| \d+ \| \d+ \| \d+ \| (\d+) \|"),
             round(sum(x["total_tokens"] for x in miss) / len(miss)) if miss else 0),
        ]

        # ★★ 〈这组数字里最值得说的四点〉那一段以前完全在校验范围外。
        #   实测：把「output 均值 242 对 195，只多 47」整句改掉，脚本照样 exit 0。
        #   而 242/47 是上一批数据的残留 —— 本批真值是 228 / 只多 33。
        #   ⇒ 这段是纯手写算式，正文里出现了就得有人盯。
        _m = re.search(r"output 均值 (\d+) 对 (\d+)，\*\*只多 (\d+) token\*\*", txt)
        claims += [
            ("①里 agent output 均值",
             int(_m.group(1)) if _m else None,
             round(a_out)),
            ("①里 直打 output 均值",
             int(_m.group(2)) if _m else None,
             round(d_out)),
            ("①里 output 差值",
             int(_m.group(3)) if _m else None,
             round(a_out - d_out)),
            ("①里 agent input 均值",
             grab(r"agent 侧 input 均值 (\d+)"), round(a_in)),
            ("①里 直打 input 均值",
             grab(r"直打侧 (\d+)，\*\*input 多"), round(d_in)),
            # ★ 原式写的是 `input 多 \d+%（\*\*([\d.]+) 倍）` —— 抓不到。
            #   原因：正文是 `**input 多 120%（2.20 倍）**`，星号在 `**input` 那一头，
            #   不在 `）` 前面。⇒ 星号位置靠猜必然错，改成【只认数字本身】，
            #   把星号/括号全用 `[^）]*` 之类跳过。
            ("①里 input 倍率%×100",
             grab(r"input 多 \d+%[^）]*?([\d.]+)\s*倍", 100),
             round(a_in / d_in * 100) if d_in else 0),
            ("①里 input 多了百分之几",
             grab(r"input 多 (\d+)%"), round((a_in / d_in - 1) * 100) if d_in else 0),
            ("③里 output 占比的分母(agent均值)",
             grab(r"output 只占 [\d.]+%（\d+/(\d+)）"), round(a_tot)),
        ]

        # ---- 4. ----
        for name, claimed, real in claims:
            if claimed is None:
                bad.append(f"  汇总「{name}」：**报告正文里抽不到这个数** —— "
                           f"这句话被改过说法、或整句被删了。"
                           f"（正文是给人看的，但这里的判据靠它，得让两者都成立）")
            elif claimed != real:
                bad.append(f"  汇总「{name}」：报告正文写 {claimed}，数据算出 {real}")
    else:
        bad.append(f"  行数对不上：报告 {len(rows)} 行，"
                   f"direct {len(d_list)} / agent {len(a_list)}")

    # ---- 3. missing_calls 必须是 0（这批数据才能当真值用）----
    if sum(x.get("missing_calls", 0) for x in d_list) != 0:
        bad.append("  直打侧 missing_calls 不为 0—— 均值偏低，不能当真值用")

    # ---- 4. runs 逐遍账（--runs N）----
    # ★ 以前整块在校验范围外：实测把三遍表整张删掉，本脚本 exit 0。
    #   报告第 96~100 行那张表有 6 列（遍/直打/agent自身/工具侧/仅自身倍率/含工具倍率）
    #   加上正文里 tool_usage_missing 的说明，一个都没被校。
    if runs:
        run_rows = parse_runs_table(txt)
        if not run_rows:
            bad.append(f"  runs：JSON 里有 {len(runs)} 遍账（runs_n="
                       f"{len(runs)}），但报告里那张三遍表**一行都没解析到** —— "
                       f"表被删了，或表头/列数变了")
        elif len(run_rows) != len(runs):
            bad.append(f"  runs：报告三遍表 {len(run_rows)} 行，JSON 里 {len(runs)} 遍 —— "
                       f"少写了遍（末尾几遍被截掉的情况最容易发生）")
        else:
            # 列：遍 | 直打 | agent 自身 | 工具侧 | 仅自身倍率 | 含工具倍率
            for (run_i, cells), r in zip(run_rows, runs):
                rid = r.get("run", run_i)
                # ★ 倍率那两格带 × 号，先剥掉再转 float
                for col, label, key in ((1, "直打", "direct_mean"),
                                        (2, "agent 自身", "agent_own_mean"),
                                        (3, "工具侧", "tool_mean")):
                    real_v = r.get(key)
                    if real_v is None:
                        bad.append(f"  第 {rid} 遍：JSON 里没有 {key} —— "
                                   f"这遍的账不完整，不能只对一半")
                        continue
                    if int(cells[col]) != round(real_v):
                        bad.append(f"  第 {rid} 遍 {label}：报告写 {cells[col]}，"
                                   f"JSON 是 {real_v}")

                for col, label, key in ((4, "仅自身倍率", "ratio_own"),
                                        (5, "含工具倍率", "ratio_all")):
                    real_v = r.get(key)
                    if real_v is None:
                        bad.append(f"  第 {rid} 遍：JSON 里没有 {key}")
                        continue
                    claimed_v = float(cells[col].replace("×", "").replace("X", ""))
                    # 报告写两位小数（3.38×），JSON 存全精度 ⇒ 容差 0.005
                    if abs(claimed_v - round(real_v, 2)) > 0.005:
                        bad.append(f"  第 {rid} 遍 {label}：报告写 {cells[col]}，"
                                   f"JSON 是 {real_v:.4f}")

                # ★★ tool_usage_missing 必须出现：这批数据 run2 是 2，
                #   也就是那遍的 ratio_all=3.40 **本身是低估的**（有 2 次没报上账）。
                #   漏掉这个字段 ⇒ 读的人以为三遍口径一致。
                # ★ 正文那句写的是「第 2 遍出现过「工具侧有 2 次没报上账」」——
                #   中间隔着中文引号和"有"字，所以正则要放宽到
                #   「次没报上账」这几个字，前面是什么都行。
                m = re.search(r"(\d+) 次没报上账", txt)
                # ★★ 两个方向都要判。第一个版本只判了"JSON 有而正文没"，
                #   实测把 JSON 里的 tool_usage_missing 改成 0 时**漏检** ——
                #   而那正是"那遍的低估被悄悄抹掉"的情形，比反方向更危险。
                total_miss = sum(x.get("tool_usage_missing", 0) or 0 for x in runs)
                if total_miss and not m:
                    bad.append(f"  tool_usage_missing：这批数据里共有 {total_miss} 次没报上账，"
                               f"但报告正文里**没有这个数** —— "
                               f"那几遍的含工具倍率是【低估】的，必须如实标出")
                elif m and int(m.group(1)) != total_miss:
                    bad.append(f"  tool_usage_missing：报告写 {m.group(1)} 次，"
                               f"三遍合计 {total_miss} 次")
                elif not total_miss and m:
                    bad.append(f"  tool_usage_missing：报告写「{m.group(1)} 次没报上账」，"
                               f"但三遍合计是 0 —— 要么数据变了，要么这句该删")

        # ★★★ 纯结构不变式：抓的是【口径缺失】，不是数字算错。
        #   只要这批数据有工具侧的账，"含工具倍率"就必须**严格大于**"仅自身倍率" ——
        #   因为工具侧是正数，加进去只会更大。
        #   ⇒ 哪天又只算一层账（工具侧漏进 JSON / 合并时被覆盖），这条立刻报红。
        #   它不依赖价格表、不依赖具体题集，是这批断言里最耐改的一条。
        for r in runs:
            ra, ro = r.get("ratio_all"), r.get("ratio_own")
            if ra is None or ro is None:
                continue
            # ★ 原式写的是 `if ... or not r.get("tool_mean"): continue` ——
            #   实测这行把"tool_mean 被归零"这种**最该抓**的情况整个跳过了，
            #   漏检率因此是 100%。
            #   而 tool_mean==0 恰恰意味着"工具侧那本账没了"，
            #   这时 ratio_all > ratio_own 也必然不成立（只剩一层账）——
            #   所以正确的做法是【照算，让不等式自己响】，不是提前 continue。
            if ra <= ro:
                bad.append(f"  第 {r.get('run')} 遍：含工具倍率 {ra:.4f} ≤ "
                           f"仅自身倍率 {ro:.4f}"
                           + ("　★★ 而 tool_mean=0（工具侧那本账没了）"
                              if not r.get("tool_mean") else "")
                           + " ⇒ 口径缺失（工具侧没并进来），不是数字算错")

            # ★★★ 第二条，且它比上面那条【更根本】。
            #   上面比的是两个**派生量**（ratio_all / ratio_own）——
            #   而派生量是上游算好的；上游把两本账合并错了，这两个数会**一起错**，
            #   不等式照样成立。实测：把 tool_mean 归零而 ratio_all 不动，
            #   上面那条**不会响**，只有"报告 vs JSON"那条在抓。
            #   ⇒ 必须回到**账本本身**重算：
            #     ratio_all == (agent_own_mean + tool_mean) / direct_mean
            #     ratio_own == agent_own_mean / direct_mean
            #   这两条不依赖报告、不依赖价格表、不依赖具体题集。
            dm = r.get("direct_mean")
            aom, tm = r.get("agent_own_mean"), r.get("tool_mean")
            if dm and aom is not None and tm is not None:
                exp_all = (aom + tm) / dm
                exp_own = aom / dm
                if abs(ra - exp_all) > 0.005:
                    bad.append(f"  第 {r.get('run')} 遍：ratio_all={ra:.4f}，"
                               f"但账本重算 (agent_own {aom} + 工具侧 {tm})"
                               f"/ 直打 {dm} = {exp_all:.4f} ⇒ 派生量与账本不一致")
                if abs(ro - exp_own) > 0.005:
                    bad.append(f"  第 {r.get('run')} 遍：ratio_own={ro:.4f}，"
                               f"但账本重算 {aom}/{dm} = {exp_own:.4f} ⇒ 派生量与账本不一致")
    else:
        # ★ JSON 里没有 runs（单遍数据）不是错，但报告不许画三遍表 ——
        #   拿单遍的数画三遍表，就是那份报告自己第 115 行骂过的"单次跑出来的点值"。
        if "含工具倍率" in txt:
            bad.append("  报告里画了『含工具倍率』三遍表，但 JSON 里没有 runs —— "
                       "要么数据该重跑 --runs N，要么这张表不该存在")

    if bad:
        print(f"✗ {len(bad)} 处对不上：")
        print("\n".join(bad))
        return 1
    print(f"✓ {REPORT.relative_to(ROOT)} 的 {len(rows)} 行逐题数字 + "
          f"{len(claims)} 项汇总"
          + (f" + {len(runs)} 遍账的 5 列" if runs else "")
          + f"，均与 {DATA.name} 一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())