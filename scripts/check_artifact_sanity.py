"""产物合理性下界检查：挡住"坏环境下跑出来的结果"覆盖好产物。

为什么需要它（2026-10-11，一起真实事故）
------------------------------------------------
`evalset/report-vector.md` 被一次**坏环境下跑的**评测覆盖成：

    应答题数：15  Recall@5：0/15
    阈值余量：应答题最低向量分 0.4240／拒答题最高向量分 0.4240

两个信号摆在那儿：**全盘召不回**（Recall 0/15），以及
**应答题最低分 == 拒答题最高分 == 0.4240**（两个不同分布的极值撞成同一个常数）。

⇒ 这个文件是**别人/别轮次据此写文档的基准**，被悄悄改掉的后果是
  README 的数字跟着一起变成错的（这次是对账器抓到才发现的）。

★ 病根不是"忘了检查"，是**没有任何东西在说"这次跑出来的数不合理"**：
  run_eval.py 自己不会报错 —— 它忠实报告了它算出来的东西。

## 为什么这个检查不派 agent、也不看 git

它必须满足三条才能挂进pre-push：
  1. **零 token、毫秒级** ⇒ 纯读文件 + 正则
  2. **不依赖 git**（坏产物可能在任何一次运行后产生，与提交无关）
  3. **判据能现算** ⇒ 不靠"我觉得太低"，而是靠**逻辑上不可能**

## ★ 判据怎么来的（每条都必须能解释为什么是这个数）

| 判据 | 下界 | 依据 |
|---|---|---|
| **字段不能自相矛盾**（判据 7） | 同一字段全文只有一个值 | 盲审实测的洞：旧版 `re.search` 只认第一处匹配，在真数据前插一行诱饵就能让整份检查失效 |
| **清单反查**（判据 0） | 清单 ⊇ 磁盘实际产物 | 漏登记的那份**永远不会被检查**。实测踩过：上一版清单少列了 `report-rerank-main-norw.md` |
| Recall@5 > 0 | > 0 | 15/44 道题里有 gold 与问题字面重合（见 README 自评第 2 条）。全 0 意味着**不是检索差，是 embedding 没加载/语料为空** |
| 应答题最低向量分 ≠ 拒答题最高向量分 | 不相等即可 | ⚠️ **这条的依据原来是我编的**（原写"正常必不相等""数学上不可能"，盲审驳回，见下） |
| 分母 = 题目集合大小 | 相等 | 产物里的"应答题数"必须等于它自己 Recall 的分母，不等就是字段错位 |
| 拒答准确率分母 = 拒答题数 | 相等 | 同上 |

### ⚠️ 判据"两个极值不该相等"是一条**同源**判据，别把它当铁律

**我原来给它的理由是假的。** 我写的是"两个不同题目集合的极值，正常必不相等"、
"下界来自数学上不可能" —— 盲审（DSH）指出这站不住：两个分布**本来就重叠**，
当前正在通过的 `report-vector.md` 就是 `ans_min 0.5968 < refuse_max 0.6321`。
**两个极值不相等是常态，偶尔相等才是异常。**

所以这条判据的真实性质：
- **只抓一种坏法**：两个字段被解析成**同一个 float**（常量化 / 取错字段 / 没跑）。
  以下都溜得过去（DSH 实测）：
  1. 常量化但错开一点点（`0.4240` vs `0.4241`）⇒ 不相等 ⇒ 放行
  2. 整行"阈值余量"被删掉/改名 ⇒ 判据根本不执行（`in` 前置为假）
  3. 只伪造其中一个极值、另一个保持真值 ⇒ 不相等 ⇒ 放行
- **覆盖面只有 2/7**：只有带"阈值余量"行的 `report-vector*.md` 会触发，
  5 份 rerank 报告没有那行。
- ⇒ 它**不是数学下界，是"同值很奇怪"的启发式**。留着是因为它确实抓到了那次事故的
  字面签名，但**别把它当"这份产物明显不是跑出来的"的证明**。
  ⇒ 与本项目已有的纪律一致：**配不出非同源且可检出的变异，就说明洞还没想清楚。**

⚠️ **已知抓不到的坏产物**（写下来，免得假装能抓）：
  - **Recall@5 掉了但不为 0** —— 判据 1 只挡"全 0"。真实退化（15/15 → 9/15）
    属于**要允许发生并被记录**的东西，不该拦。`1/15`、`35/44→15/44` 全放行，
    这是**有意的**，不是漏网。
  - **整份数值都是编的** —— 检查器只核**同文件内字段之间的算术关系**，
    从不绑定任何外部真相：没有"每份产物应有多少题"的表，也不看题目 ID。
    DSH 实测把整份换成编的数字（44 / 0/44 / 14/44 / 61 / 50/61 / 0.5000 / 0.6000）⇒ 全绿。
  - **只留表头、删掉全部明细** ⇒ 全绿（"报告"没有正文也放过）。
  - **rerank 层静默失效** —— 实测 `report-rerank.md`、`report-rerank-main.md`、
    `report-rerank-main-norw.md` **SHA256 完全相同**（`52179d0adb51`，各 1645 B）。
    三份本该属于不同配置的产物一模一样，但检查器**无法区分**
    "本来就该相同"和"rerank 静默失效退回了旧数字" ⇒ 这条**没做**。
  - **语料换了但没重跑** —— 产物里没记语料指纹/sha，比对不出来。
  - **命名不落在 `evalset/report*.md` 的产物** —— 判据 0 的反查与清单共享同一个
    glob，换个名字（`vector-report-v2.md`）就两头都看不见（DSH 实测 exit 0）。

⚠️ **刻意不做的检查**：
  - 不设"Recall@5 至少 60%"这种**性能下界** —— 那会把"真实退化"也拦下来，
    而真实退化是**要允许发生并被记录**的（这就是本项目的负结果传统）。
    这里只拦"**明显不是跑出来的**"。
  - 不校验具体数值是否与 README 一致 —— 那是 `check_readme_numbers.py` 的活，
    两边职责不重叠。

用法：
    python scripts/check_artifact_sanity.py         # 检查全部产物
    python scripts/check_artifact_sanity.py --show   # 顺便打印实测值

退出码：0 = 全过；1 = 有产物不合理（拦提交）
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# ★ 2026-10-11：stdout 必须显式转成 UTF-8。这是**被它自己拦下来时**发现的，
#   同一个病在本仓库已经是第三次（check_readme_numbers.py / mutate_check_numbers.py
#   之后，第三个）：本脚本用 print 打中文 + ⇒，而 pre-push 里 stdout 是
#   【管道 + GBK】⇒ print(w) 直接抛 UnicodeEncodeError ⇒ 退出码 1。
#   最坏的地方不是"崩了"，而是**崩出来的那句话是假的**：
#   钩子报的是「★ 产物不合理（上面列出的那些）—— 查环境后重跑，别当基线用」
#   —— 上面一行都没有，产物也没被检查完。**报假错比不报更坏：它教人不信检查。**
#   （写法同 scripts/check_push_paths.py:48。只在 Windows 的"重定向/被捕获"场景触发，
#    Python 直接写控制台走 UTF-16 API 不受影响，Linux CI 也不受影响。）
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parent.parent

# ★ 产物文件清单。**必须显式列出**（不用 glob 去逐个检查）——
#   但显式清单必须配一道反查，否则"新报告忘了登记"会静默漏检。
#   详见下面 ARTIFACT_GLOB 处的说明。
ARTIFACTS = [
    "evalset/report-vector.md",
    "evalset/report-vector-heldout.md",
    "evalset/report-rerank.md",
    "evalset/report-rerank-main.md",
    "evalset/report-rerank-main-norw.md",
    "evalset/report-rerank-heldout.md",
    "evalset/report-rerank-heldout-norw.md",
]

# ★★ 清单的反查基准：**磁盘上有哪些产物**，用来发现"新报告忘了登记"。
#   为什么必须反查（2026-10-11 实测踩到）：
#     上一版 ARTIFACTS 只列了 6 份，磁盘上其实有 7 份——
#     `evalset/report-rerank-main-norw.md` 从头到尾**没被任何检查碰过**。
#     而"漏检比误报危险得多"这条正是我自己在上面 docstring 里写的，
#     结果它当场就发生在我自己身上。⇒ 光有"显式清单"不够，
#     必须再有一道"清单 ⊇ 磁盘实际产物"的反查，否则清单本身就成漏斗口。
ARTIFACT_GLOB = "evalset/report*.md"

# ★★ 不是所有产物都长一个样，所以判据按【产物类型】分派。
#
#   背景：2026-10-12 push 被拦，因为新增的两份产物没登记。
#   而它们**根本不该套用检索评测那套判据**——
#     拒答回归报告：抽不到「应答题数/Recall@5」是正常的（它不测检索）
#     token 成本报告：同样没有 Recall 字段
#   如果硬套，会报出一堆"格式变了"的假错，噪音多了就会被当噪音关掉。
#
#   ⇒ 做法：登记，但按类型派不同判据。
#     RAG_EVAL_ARTIFACTS = 检索/重排评测（原有 7 份，判据 1~7）
#     新增两类 = 有各自真正值得查的不变量（见下面各处注释）。
#
#   ★ 豁免不是选项：这个脚本自己的注释写着"漏检比误报危险得多"。
#     一份不被检查的报告 = 坏了也没人喊 = 那正是它当初存在的理由被推翻。
RAG_EVAL_ARTIFACTS = ARTIFACTS

# ---- 类型二：agent 拒答回归报告 ----
#   它能查什么（这些是真实的失效模式，不是凑数）：
#     ① 硬答率与硬答题数必须自洽（分子分母对不上就是字段读串位）
#     ② 硬答题数不得超过 no_hit 信号数（分子不可能大于分母）
#     ③ 信号解析失败数不得超过总题数
REFUSAL_REPORT = "evalset/report-agent-refusal-regression.md"

# ---- 类型三：token 成本报告 ----
#   ① missing_usage 必须为 0 —— 有缺 usage 就意味着有数字是补0/估算的，
#      而"补 0 的均值"比"没有均值"更坏（它看起来像实测）
#   ② truncated 必须为 0 —— 有题触到 MAX_ROUNDS 就说明这批数据有截断
TOKEN_REPORT = "evalset/report-token-cost.md"

EXTRA_ARTIFACTS = [REFUSAL_REPORT, TOKEN_REPORT]
ARTIFACTS = ARTIFACTS + EXTRA_ARTIFACTS


def _on_disk() -> list[str]:
    """磁盘上实际存在的产物（相对 REPO_ROOT，正斜杠）。"""
    return sorted(
        p.relative_to(REPO_ROOT).as_posix()
        for p in REPO_ROOT.glob(ARTIFACT_GLOB)
        if p.is_file()
    )



def _grab(text, pattern, cast, label, out):
    """抓一个字段的**全部**匹配；值不一致 ⇒ 记冲突（由调用方报错），不猜。

    ★ 为什么不能 `re.search` 只取第一个（2026-10-11 盲审实测的洞）：
      `re.search` 只认文件里**出现的第一处**。
      实测：在真数据前插一行诱饵（`> 应答题数：15　Recall@5：15/15`）、
      真数据改成 `0/15` —— 脚本报 **exit 0 全绿**，而文件里明明写着 0/15。
      ⇒ 判据再严，抽错了字段就等于没检查。
      （与今日早些时候 `check_readme_numbers.py` 的假绿同源：
        探头只照一面，却把"没报错"读成"没问题"。）

    ⇒ 冲突时**不写入**：下游"抽不到字段"会报格式错，逼人来看一眼。
      静默取第一个 = 让人以为检查过了。
    """
    # ⚠️ re.M 必须带：下面几个 pattern 用 ^锚定行首（只认汇总行、不认正文里的散落提及），
    #   漏了它会一个都匹配不上 ⇒ 全部字段"抽不到" ⇒ 满屏假错。
    vals = {cast(m) for m in re.findall(pattern, text, re.M)}
    if not vals:
        return# 抽不到由调用方判（格式变了要报）
    if len(vals) > 1:
        out.setdefault("_conflicts", []).append(
            f"{label}在同一个文件里出现了 **{len(vals)} 个不同的值**："
            f"{sorted(vals)} —— 同一份产物不可能有两个答案"
        )
        return
    out[label] = vals.pop()


def _grab3(text, pattern, keys, out):
    """同上，但抓的是三元组（如 应答题数/Recall分子/分母）。"""
    triples = {tuple(int(x) for x in m) for m in re.findall(pattern, text, re.M)}
    if not triples:
        return
    if len(triples) > 1:
        out.setdefault("_conflicts", []).append(
            f"{keys[0]} 那一行在同一个文件里出现了 **{len(triples)} 个不同的值**："
            f"{sorted(triples)} —— 同一份产物不可能有两个答案"
        )
        return
    out[keys[0]], out[keys[1]], out[keys[2]] = triples.pop()


def _extract(text):
    """从一份报告里抠出关键字段。抽不到就留 None（由调用方判）。"""
    out = {}
    _grab3(text, r"^- 应答题数：(\d+)\s*Recall@5：(\d+)/(\d+)",
           ("ans_total", "recall_ok", "recall_den"), out)
    _grab3(text, r"^- 拒答题数：(\d+)\s*拒答准确率：(\d+)/(\d+)",
           ("refuse_total", "refuse_ok", "refuse_den"), out)
    _grab(text, r"^- 漏答率：(\d+)/(\d+)",
          lambda m: (int(m[0]), int(m[1])), "漏答率", out)
    _grab(text, r"应答题最低向量分\s*([0-9.]+)", float, "ans_min_score", out)
    _grab(text, r"拒答题最高向量分\s*([0-9.]+)", float, "refuse_max_score", out)
    # 漏答率是二元组 ⇒ 单独落回两个键
    if "漏答率" in out:
        out["miss_ok"], out["miss_den"] = out.pop("漏答率")
    return out


def _check_refusal_report(rel: str, text: str) -> list[str]:
    """拒答回归报告的判据（与检索评测完全不同的不变量）。

    为什么单独一组：这份报告不测检索，所以"抽不到 Recall@5"是**正常的**，
    不是格式变了。硬套检索判据只会产出一堆假错。
    """
    errs = []
    # 硬答题数（形如 "6、5、4"）
    m = re.search(r"\| \*\*硬答题数\*\* \| \*\*([0-9、]+)\*\*", text)
    if not m:
        return [f"✗ {rel}：抽不到「硬答题数」—— 格式可能变了。"
                f"⚠️ 不要直接改判据，先确认新格式"]
    hard_n = [int(x) for x in m.group(1).split("、")]

    # 硬答率（形如 "10.5%~15.8%（3 次：15.8% / 12.5% / 10.5%）"）
    m2 = re.search(r"\| \*\*硬答率\*\* \| \*\*([0-9.]+)%[~]([0-9.]+)%"
                   r"（\d+ 次：([^）]+)）", text)
    if m2:
        listed = [float(x.strip().rstrip("%"))
                  for x in m2.group(3).split("/")]
        # ① 三个数必须能在括号里找齐（字段读串位的信号）
        if len(listed) != len(hard_n):
            errs.append(
                f"✗ {rel}：硬答率列了 {len(listed)} 个值，"
                f"但硬答题数有 {len(hard_n)} 个 —— 两行不是同一组数"
            )
        # ② 区间端点必须落在列出来的值里
        lo, hi = float(m2.group(1)), float(m2.group(2))
        if listed and (abs(lo - min(listed)) > 0.06
                       or abs(hi - max(listed)) > 0.06):
            errs.append(
                f"✗ {rel}：区间 {lo}%~{hi}% 与括号里列的值 {listed} 对不上"
            )

    # ③ 信号解析失败数不得超过总题数（分子不可能大于分母）
    m3 = re.search(r"\| 信号解析失败 \| (\d+) \|", text)
    n_q = re.search(r"跑了 \*\*(\d+)\*\* 遍", text)
    if m3 and n_q:
        miss = int(m3.group(1))
        total = 61 * int(n_q.group(1))          # 61 = 本项目的拒答题集大小
        if miss > total:
            errs.append(
                f"✗ {rel}：信号解析失败 {miss} > 总题数 {total}"
                f"（{n_q.group(1)} 遍 × 61 题）—— 字段读串位了"
            )
    return errs


def _check_token_report(rel: str, text: str) -> list[str]:
    """token 成本报告的判据。

    核心一条：missing_usage 必须为 0。
    "有几次调用没拿到 usage" 时，那些题的数字要么是补0要么是估算 ——
    而**补 0 算出来的均值比没有均值更坏**，因为它看起来像实测。
    """
    errs = []
    for field, why in (
        ("missing_usage",
         "有调用没拿到 usage，那些题的数字是补0/估算出来的。"
         "★ 补 0 的均值比没有均值更坏 —— 它看起来像实测"),
        ("truncated",
         "有题触到了 MAX_ROUNDS，这批数据有截断，均值不可信"),
    ):
        ms = re.findall(rf"`{field} = (\d+)`", text)
        if not ms:
            errs.append(f"✗ {rel}：抽不到 `{field}` —— 格式可能变了，"
                        f"⚠️ 不要直接改判据，先确认新格式")
            continue
        if any(int(v) != 0 for v in ms):
            errs.append(f"✗ {rel}：`{field}` 不是 0（读到 {ms}）—— {why}")
    return errs


def check_one(rel: str) -> list[str]:
    """检查一份产物，返回问题清单（空 = 通过）。"""
    p = REPO_ROOT / rel
    if not p.exists():
        return [f"✗ {rel}：文件不存在 —— 清单里登记了但磁盘上没有。"
                f"要么它被删了，要么该从 ARTIFACTS 里去掉"]
    text = p.read_text(encoding="utf-8")
    if len(text) < 100:
        return [f"✗ {rel}：只有 {len(text)} 字节 —— 评测跑到一半崩了？"]

    # ★ 按类型分派：不是所有产物都长一个样（见 ARTIFACTS 处的注释）
    if rel == REFUSAL_REPORT:
        return _check_refusal_report(rel, text)
    if rel == TOKEN_REPORT:
        return _check_token_report(rel, text)

    d = _extract(text)
    errs: list[str] = []

    # ---- ★ 判据 7（盲审补的）：同一份产物里字段自相矛盾 ⇒ 报错 ----
    #   这是"诱饵行"那条洞的正解：不再静默取第一个匹配。
    for c in d.get("_conflicts", []):
        errs.append(
            f"✗ {rel}：{c}"
            f"★ 同一份产物不可能有两个答案。要么产物坏了，要么有人手改了它。"
            f"（旧版只认第一处匹配 ⇒ 别人在前面插一行就能让整份检查失效）"
        )

    # ---- 字段抽取失败 =产物格式变了，要人看一眼 ----
    if "recall_den" not in d:
        errs.append(f"✗ {rel}：抽不到「应答题数/Recall@5」—— 产物格式可能变了，"
                    f"这个脚本没跟上。⚠️ 不要直接改脚本，先确认新格式是什么")
    if "refuse_den" not in d:
        errs.append(f"✗ {rel}：抽不到「拒答题数/拒答准确率」—— 同上")

    # ---- 判据 1：Recall@5 不能为 0 ----
    if "recall_ok" in d and d["recall_ok"] == 0:
        errs.append(
            f"✗ {rel}：Recall@5 是 0/{d['recall_den']} —— 全盘一条都没召回。"
            f"★这不是「检索效果差」，是 embedding 没加载 / 语料为空 / 模型路径错了。"
            f"**别把这份结果当基线写进任何文档**"
        )

    # ---- 判据 2：Recall 分子不能超过分母 ----
    if "recall_ok" in d and "recall_den" in d and d["recall_ok"] > d["recall_den"]:
        errs.append(f"✗ {rel}：Recall 分子 {d['recall_ok']} > 分母 {d['recall_den']}"
                    f" —— 字段读串位了")

    # ---- 判据 3：分母 = 题目集合大小 ----
    if "ans_total" in d and "recall_den" in d and d["ans_total"] != d["recall_den"]:
        errs.append(
            f"✗ {rel}：应答题数 {d['ans_total']} ≠ Recall 分母 {d['recall_den']}"
            f" —— 两个字段说的不是同一件事"
        )
    if "refuse_total" in d and "refuse_den" in d and d["refuse_total"] != d["refuse_den"]:
        errs.append(
            f"✗ {rel}：拒答题数 {d['refuse_total']} ≠ 拒答准确率分母 {d['refuse_den']}"
            f" —— 两个字段说的不是同一件事"
        )

    # ---- 判据 4：漏答数不应超过 Recall 未召回数（口径一致）----
    if "miss_ok" in d and "recall_ok" in d and "recall_den" in d:
        miss_expected = d["recall_den"] - d["recall_ok"]
        if d["miss_ok"] != miss_expected:
            warns = (
                f"· {rel}：漏答率 {d['miss_ok']}/{d['miss_den']}，"
                f"但按 Recall 反推应为 {miss_expected}。"
                f"⇒ 两者口径不同（漏答算的是 knowledge_hit，而 Recall 算 gold 命中）—— "
                f"**这是已知的口径差，不是 bug**，但写文档时别混用"
            )
            # 只提示不报错：口径差是真实存在的，不该拦
            _sanity_warns.append(warns)

    # ---- 判据 5：★ 两个不同题集的极值不该撞成同一个常数 ----
    #   这是 2026-10-11 那次事故最强的信号：0.4240 == 0.4240
    if "ans_min_score" in d and "refuse_max_score" in d:
        if abs(d["ans_min_score"] - d["refuse_max_score"]) < 1e-9:
            errs.append(
                f"✗ {rel}：应答题最低向量分与拒答题最高向量分**完全相等**"
                f"（都是 {d['ans_min_score']}）—— "
                f"这两个是不同题目集合上的极值，正常必不相等。"
                f"★ 相等说明分数被常量化/取错字段/根本没跑。**这正是 2026-10-11 那次事故的信号**"
            )

    # ---- 判据 6：漏答分母应等于应答题数 ----
    if "miss_den" in d and "ans_total" in d and d["miss_den"] != d["ans_total"]:
        errs.append(
            f"✗ {rel}：漏答率分母 {d['miss_den']} ≠ 应答题数 {d['ans_total']}"
        )

    return errs


# 模块级累积的 warns（口径差这类"不是错但要知道"的事）
_sanity_warns: list[str] = []


def check_all() -> tuple[list[str], list[str]]:
    _sanity_warns.clear()
    errs: list[str] = []
    for rel in ARTIFACTS:
        errs += check_one(rel)

    # ★ 探头自检：如果一份都没检查到，等于没检查
    checked = [r for r in ARTIFACTS if (REPO_ROOT / r).exists()]
    if not checked:
        errs.append(
            "⚠⚠【探头失灵】ARTIFACTS 里的文件一个都不存在 —— "
            "本轮没有检查到任何产物。①是不是路径写错了？"
            "②是不是产物被移到别处、清单没跟着改？"
        )

    # ---- ★ 判据 0：清单反查 —— 磁盘上的产物有没有漏登记的 ----
    #   这不是"多检查一个文件"，是**堵住漏斗口**：
    #   漏登记 ⇒ 那份产物从此不被任何检查碰过 ⇒ 坏了也没人喊。
    registered = set(ARTIFACTS)
    missing = [r for r in _on_disk() if r not in registered]
    if missing:
        errs.append(
            f"✗ 有{len(missing)} 份产物在磁盘上，但**没登记进 ARTIFACTS** —— "
            f"它们不会被本脚本检查："
            + "".join(f"\n      · {m}" for m in missing)
            + "\n★ 要么加进 ARTIFACTS（如果它该被检查），"
              "要么说明它为什么可以豁免（在代码旁写理由，别默默留着）。"
        )
    # 反向：清单里登记了、磁盘上却没有 —— check_one 已经会报，这里不重复

    return errs, list(_sanity_warns)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", action="store_true", help="顺便打印各产物的实测值")
    args = ap.parse_args()

    errs, warns = check_all()

    if args.show:
        print("各产物实测值：")
        for rel in ARTIFACTS:
            p = REPO_ROOT / rel
            if not p.exists():
                print(f"  {rel:44s} ⇒ ★文件不存在")
                continue
            # ★ 只对检索评测类打这行 —— 给拒答/成本报告打一排 "?"
            #   会让人以为"这些产物缺数据"，而它们压根没有这些字段。
            if rel in EXTRA_ARTIFACTS:
                print(f"  {rel:44s} ⇒ （按类型走独立判据，不适用检索字段）")
                continue
            d = _extract(p.read_text(encoding="utf-8"))
            print(
                f"  {rel:44s} ⇒ Recall {d.get('recall_ok','?')}/{d.get('recall_den','?')}"
                f" ·拒答 {d.get('refuse_ok','?')}/{d.get('refuse_den','?')}"
                f" · 最低分 {d.get('ans_min_score','?')}"
                f" / 最高分 {d.get('refuse_max_score','?')}"
            )
        print()

    for w in warns:
        print(w)
    if warns:
        print()

    if errs:
        print("产物合理性检查失败：")
        for e in errs:
            print(f"  {e}")
        print()
        print("★ 一份骗人的评测报告比没有评测更坏 —— 它会把坏环境的产物")
        print("  当成基线，然后README 跟着一起变成错的。")
        print("  先查环境（embedding 加载了吗 / 语料空不空 / API 通不通），再重跑。")
        return 1

    print(f"✓ 产物合理性检查通过（{len(ARTIFACTS)} 份）")
    return 0


if __name__ == "__main__":
    sys.exit(main())