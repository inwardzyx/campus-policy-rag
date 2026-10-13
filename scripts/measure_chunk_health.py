# -*- coding: utf-8 -*-
"""scripts/measure_chunk_health.py —— 数据层体检（只读，秒级）

回答一个问题：**现在这份知识库里，块健不健康？**

为什么单独要有这个脚本：

    检索层有一堆指标（Recall@5、拒答率、阈值间隔……），数据层却一直没有。
    「数据层怎么样」不该靠印象回答 —— 它得能像 `evalset/run_eval.py` 一样被量出来，
    否则每次改完切片规则，你只能说"感觉干净多了"。

★ 刻意不 import service：那会连带加载 embedding 模型（十几秒）。
  体检脚本必须是**秒级**的，否则你不会想跑它，它就废了。
  （代价是 max_chars / MIN_LEN 这两个阈值只能按约定写在这里，下面标了出处。
    防漂移的办法不是改这个脚本，而是去测试里断言"约定 == 真值"——
    两条各守一个真值，漂移时能直接看出是哪个数抄错了：
      max_chars → tests/test_loader.py::test_health_script_max_chars_is_not_drifted
      MIN_LEN   → tests/test_guard.py::test_health_script_min_len_is_not_drifted）

跑法（仓库根目录）：

    python scripts/measure_chunk_health.py
    python scripts/measure_chunk_health.py --samples 5    # 极端样本多看几条
"""

import logging
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kb.loader import (                                   # noqa: E402
    CLEAN_OP_FOOTER, CLEAN_OP_PREAMBLE, CLEAN_OP_TRUNCATE,
    _CHROME_RE, load_documents,
)
# ★ print 里的 ⇒/⚠/✔ 在 cp936 下编不出来（stdout 是管道时）⇒ 崩在打印上。本项目已犯 5 次。
#   ⚠️ 这里**必须**用 getattr 而不能直接调：本模块被 tests/test_guard.py 与
#      tests/test_loader.py 导入，而 pytest 早起的捕获层不一定有 reconfigure
#      ⇒ 直接调会让**所有测试**在收集阶段就崩（scripts/install_hooks.py 记过那次事故）。
_reconf = getattr(sys.stdout, "reconfigure", None)
if _reconf is not None:
    _reconf(encoding="utf-8", errors="replace")


DOCS_DIR = ROOT / "docs"

# 这两个阈值是「约定」不是「引用」—— 原因见文件头（不想为了拿两个数去加载模型）。
MAX_CHARS_HINT = 400      # 对应 kb/loader.load_documents(max_chars=400)
MIN_LEN_HINT = 15         # 对应 service.MIN_LEN，低于它会被当"太短碎屑"拦掉

# version 必须是 YYYY-MM-DD —— 否则版本冲突关比不出新旧（见 service._version_key）
DATE_OK = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# 正文里不可能合法出现"行首 ##" —— 真出现了，就说明有一个标题没被切开：
# 漏写 ## 后的空格时 `^##\s+` 匹配不上，那一段会**并进上一块**（块数/长度/身份键全都正常，
# 但上一块里混进了下一条的标题和条文 → 检索到了会张冠李戴）。
STRAY_HEAD = re.compile(r"(?m)^##")


# ==================================================================
# 什么时候该上「切多块」—— 三条判据（唯一的定义处）
# ==================================================================
# ★ 判据不是"块长到 400 了吗"，而是"**这一刀丢了多少字**"。
#   顶到 400 的块被切掉 1 个字，不值得为它写一个切分器；
#   真正该触发动作的是丢字量 —— 丢 1 字和丢 200 字不是一回事。
#
# ★ 为什么这个判据长在【体检脚本】而不是 loader 里：
#   判据要读的信号是"加载时每一刀丢了多少字"，那正是清洗账收的东西
#   （`CLEAN_OP_TRUNCATE` 的 `clean_chars`）。放在这里 = 和生产日志**同一个信号源**，
#   不重新算一遍（重新算就又是一处"判据和被测对象不同源"）。
SPLIT_TRIGGER_DROP = 100      # 主判据：任一单块丢字 ≥ 它（原文 ≥ 400+100=500 字）→ 当天上
SPLIT_TRIGGER_COUNT = 3       # 次判据：被截断的块 ≥ 它 → 该上（是粒度问题，不是单个例外）


def grade_truncation(drops):
    """按三条判据给"要不要上切多块"定级。**纯函数** —— 便于用合成输入单测。

    drops: 每个被截断块的丢字数（生产里来自 `CLEAN_OP_TRUNCATE` 的 clean_chars）。
    返回 (level, 一句话结论)，level ∈ {不动, 记账, 该上, 当天上}。

    ① **主判据（丢字量）**：任一 `N ≥ SPLIT_TRIGGER_DROP` → **当天就上**。
       N < 100 → 只记账继续观察：这种丢字对检索影响很小，切了反而把一条完整条款拆散。
    ② **次判据（粒度变了）**：被截断的块 ≥ `SPLIT_TRIGGER_COUNT` 个 → 该上。
       三个以上同时超长，说明进来的语料从"按条"变成了"按章" —— 那是粒度问题。
    ③ **反向判据（写给将来写切分器的人）**：`< max_chars` 的块**一个都不许切**。
       现在块的边界正好是条款边界，整条在一起；切完可能变成"…按旷课论处"在一块、
       "并记入学生档案"在另一块 —— 白丢上下文。

    ★ ③ 故意**没有**对应的测试：切分器还不存在，写了就是**空断言**
      （恒真、永远不会红，正是这个仓库最忌讳的东西）。
      等切分器真做出来，再在这里补"小块不许被切"的用例。
    """
    lost = [d for d in drops if d > 0]
    if not lost:
        return "不动", "没有被截断的块"
    worst = max(lost)
    if worst >= SPLIT_TRIGGER_DROP:
        return "当天上", f"有块丢了 {worst} 字（≥{SPLIT_TRIGGER_DROP}，原文 ≥{MAX_CHARS_HINT + SPLIT_TRIGGER_DROP} 字）"
    if len(lost) >= SPLIT_TRIGGER_COUNT:
        return "该上", f"被截断的块有 {len(lost)} 个（≥{SPLIT_TRIGGER_COUNT}）—— 粒度变了"
    return "记账", f"{len(lost)} 块被截断，最多丢 {worst} 字（都不到 {SPLIT_TRIGGER_DROP}）"


class _CleanLogCollector(logging.Handler):
    """收集 kb/loader 加载过程中"动过的刀"。

    ★ 为什么需要这一节：上面那些检查项**全是终态快照** —— 只问"现在的块长什么样"，
      看不见"加载时动了几刀"。而剥离页脚 / 截断 / 丢导语这三刀都会**永久改内容**。

      最坏的情况正是第二轮修掉的那个 bug 的形状：一条真条文被从行中切断、削掉 53 字，
      剩下的文本长度正常、页脚标记也随正文一起没了 ——
      **快照 6 项全绿，一个字都不会说。** 这一节补的就是这个盲区。

    ★ 为什么读 record.clean_op，而不是解析日志文案：
      解析文案 = 又一处"判据和被测对象不同源"，改个措辞就静默失效。
      loader 把动作写进 extra、体检 import 同一批常量 —— 两边同源。

    ★★ level 为什么必须是 DEBUG（2026-10-10 改，这里踩过一次）：
      loader 把逐条清洗明细降到了 DEBUG（正常路径不该刷 10 行 warning），
      末尾汇总那一行仍是 WARNING。而这里如果保持默认的 WARNING 收，
      清洗账会**整栏变成 0** —— 一份"全 0"的体检报告比没有更坏，
      它看起来像"从来没动过刀"，实测却是丢了 4195 字。
      （这个坑和 loader 里"体检脚本设了自己的 level"那句注释是同一件事的两面：
       降级的一方要保证升级的那一方仍能看到。）
      ⇒ 所以这里显式收 DEBUG，且下面挂 handler 后把 logger 本身也压到 DEBUG，
         否则 logger 级别会把 DEBUG 记录挡在 handler 之前。
    """

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.ops = {}                      # op -> {"n": 次数, "chars": 总字数, "values": [每个的 clean_chars]}

    def emit(self, record):
        op = getattr(record, "clean_op", None)
        if op is None:
            return                         # 不是清洗动作，不记账
        chars = getattr(record, "clean_chars", 0)
        slot = self.ops.setdefault(op, {"n": 0, "chars": 0, "values": []})
        slot["n"] += 1
        slot["chars"] += chars
        slot["values"].append(chars)       # ★ 保留【每一条】的量，不只是合计 ——
        #   「要不要上切多块」的判据要看"单块最多丢多少"，合计给不了这个数。

    def values(self, op):
        """某个动作每一刀的量（列表）。没发生过就返回空列表。"""
        return list(self.ops.get(op, {}).get("values", []))


def pct(values, q):
    """分位数（q 取 0-1）。不引 numpy，手算就行 —— 体检脚本要零依赖。"""
    if not values:
        return 0
    s = sorted(values)
    return s[min(len(s) - 1, int(len(s) * q))]


def main(argv):
    samples = 5
    if "--samples" in argv:
        samples = int(argv[argv.index("--samples") + 1])

    print("=" * 70)
    print("数据层体检（只读）—— docs/ 里的块健不健康")
    print("=" * 70)

    collector = _CleanLogCollector()
    loader_log = logging.getLogger("kb.loader")
    # ★ 必须同时压 logger 本身：handler 收DEBUG 没用，logger 级别是 WARNING 的话
    #   记录在到达 handler 之前就被挡掉了（实测：清洗账整栏变 0）。
    _prev_level = loader_log.level
    loader_log.setLevel(logging.DEBUG)
    loader_log.addHandler(collector)
    try:
        docs, errors = load_documents(DOCS_DIR)
    finally:
        loader_log.removeHandler(collector)
        loader_log.setLevel(_prev_level)   #还原，别把调用方的日志级别改了

    # ★ 空库要早退：min()/max() 对空序列是**抛异常**（不是返回 None —— 和 sum([]) 返回 0
    #   不一样，这俩常被记混）。一个专门回答"数据层健康吗"的脚本，恰好在答案是
    #   "库里什么都没有"的时候崩掉，那就太讽刺了 —— 而那是**最该被报出来的状态**。
    if not docs:
        print("\n【规模】块数 0 —— 库是空的（docs/ 下一个可解析的 .md 都没有）")
        if errors:
            print("        解析失败的文件 %d 个：%s" % (len(errors), errors))
        print("\n结论：★ 数据层是空的。这比任何单项指标都严重，先让它有内容。")
        return 1

    lens = [len(d["text"]) for d in docs]
    checks = []          # (检查项, 是否通过, 数量) —— 结论里数的是这一份，和 ✔ 行一一对应

    # ---------- 规模 ----------
    by_doc = Counter(d["doc"] for d in docs)
    print("\n【规模】")
    print("  文档数            %d" % len(by_doc))
    print("  块数              %d" % len(docs))

    # ---------- 长度分布 ----------
    print("\n【长度分布】单位：字")
    print("  最短 %-5d 中位 %-5d p90 %-5d 最长 %d"
          % (min(lens), int(statistics.median(lens)), pct(lens, 0.9), max(lens)))
    over = [d for d in docs if len(d["text"]) >= MAX_CHARS_HINT]
    short = [d for d in docs if len(d["text"]) < MIN_LEN_HINT]
    checks.append(("超长块（≥%d 会被截断丢字）" % MAX_CHARS_HINT, not over, len(over)))
    print("  ≥%d（会被截断）    %d  %s"
          % (MAX_CHARS_HINT, len(over), "✔ 没有" if not over else "★ 有（看下面的切多块判据）"))
    print("  <%d（当碎屑拦）    %d  （不算问题：把关本来就会拦掉它们）"
          % (MIN_LEN_HINT, len(short)))

    # ---------- 切多块判据 ----------
    # ★ 这里读的是清洗账里 CLEAN_OP_TRUNCATE 的【每一条】丢字数，
    #   也就是启动日志里那句「已截断（丢 N 字）」的同一个数 —— 判据同源，不重算。
    level, why = grade_truncation(collector.values(CLEAN_OP_TRUNCATE))
    print("\n【切多块判据】看「这一刀丢了多少字」，不是「到 400 了吗」")
    print("  判定：%s —— %s" % (level, why))
    print("  主判据 单块丢字 ≥%d → 当天上 ｜ 次判据 被截断块 ≥%d → 该上"
          % (SPLIT_TRIGGER_DROP, SPLIT_TRIGGER_COUNT))
    print("  反向判据 <%d 的块一个都不许切（现在块的边界正好是条款边界）" % MAX_CHARS_HINT)

    # ---------- 脏数据 ----------
    # ★ 判据必须和 loader 同源：这里以前写的是"全串查找"（any(m in text ...)），
    #   而 loader 第二轮已经收紧成**只认行首**。两者方向相反 ——
    #   正文里合法的一句"学生如有疑问请联系我们"在 loader 眼里是正确的，
    #   在旧判据眼里却是"★ 有残留"，还可能诱导出一个**错误的修复**（去把 loader 改松）。
    #   现在直接复用 `_CHROME_RE`，判据只有一处。
    dirty = [d for d in docs if _CHROME_RE.search(d["text"])]
    blank = [d for d in docs if not d["text"].strip()]
    cr = [d for d in docs if "\r" in d["text"]]
    checks.append(("页脚残留块", not dirty, len(dirty)))
    checks.append(("空块", not blank, len(blank)))
    print("\n【脏数据】")
    print("  页脚残留块        %d  %s" % (len(dirty), "✔ 干净" if not dirty else "★ 有残留"))
    print("  空块              %d  %s" % (len(blank), "✔ 没有" if not blank else "★ 有"))
    # 提示项，不计入结论：read_text 的 universal newlines 已经把它保证成 0
    print("  含 \\r 的块        %d  （提示项，不计入结论：换行已被 read_text 归一）" % len(cr))

    # ---------- 结构 ----------
    stray = [d for d in docs if STRAY_HEAD.search(d["text"])]
    checks.append(("块内出现行首 ##（标题没切开）", not stray, len(stray)))
    print("\n【结构】")
    print("  块内行首 ## 残留  %d  %s"
          % (len(stray), "✔ 没有" if not stray else "★ 有条标题漏了 ## 后的空格，并进了上一块"))

    # ---------- 元数据 ----------
    bad_date = [d for d in docs if not DATE_OK.match(str(d.get("version", "")))]
    key = Counter((d["doc"], d["clause"]) for d in docs)
    dup_clause = {k: v for k, v in key.items() if v > 1}
    checks.append(("version 非 YYYY-MM-DD", not bad_date, len(bad_date)))
    checks.append(("同文档内 clause 重名", not dup_clause, len(dup_clause)))
    print("\n【元数据 / 身份键】")
    print("  version 非 YYYY-MM-DD   %d  %s"
          % (len(bad_date), "✔ 都能比较新旧" if not bad_date else "★ 会让版本比较失效"))
    print("  同文档内 clause 重名    %d  %s"
          % (len(dup_clause),
             "✔ 身份键唯一" if not dup_clause else "★ 会踩版本冲突关（同 key 只留一个）"))

    # ---------- 解析失败 ----------
    checks.append(("解析失败文件", not errors, len(errors)))
    print("\n【解析】")
    print("  解析失败文件      %d  %s"
          % (len(errors), "✔ 没有" if not errors else "★ 有（见 errors 列表）"))

    # ---------- 清洗账 ----------
    print("\n【清洗账】加载时动过的刀（不可逆 —— 这几个数应该长期稳定）")
    #★★ 自检：明细收不到就【报错】，不许安静地打「没动过刀」。
    #   这个坑实测踩过：loader 把明细降到 DEBUG 后，collector 没跟着降级别，
    #   清洗账整栏变成 0 —— 而"0块 0 字"读起来和"确实没动过刀"一模一样。
    #   而事实上那次丢了 4195 字。**一份骗人的体检报告比没有体检更坏**，
    #   因为它会把"探头失灵"读成"数据干净"。
    #   判据用【期望非零】而不是"只要有记录就行"：真实语料必然有截断/剥页脚，
    #   全 0 就等于探头失灵，这个假设由下面的 EXPECTED_CLEAN_OPS 承担。
    EXPECTED_CLEAN_OPS = (CLEAN_OP_FOOTER, CLEAN_OP_TRUNCATE)
    blind = [op for op in EXPECTED_CLEAN_OPS if not collector.ops.get(op)]
    if blind:
        print("  ⚠⚠ 探头失灵：以下动作一条明细都没收到，但真实语料必然发生它们——")
        for op in blind:
            print("      %s（loader 打在 DEBUG 级，collector 没收到）" % op)
        print("      ⇒ 下面的 0 是【假的】，不要当成'没动过刀'。")
        print("      修法：确认 _CleanLogCollector 是 DEBUG 级，且 logger 本身被压到 DEBUG。")
        blind_ops = True
    else:
        blind_ops = False
        print("  （没动过刀）")
    for op in (CLEAN_OP_FOOTER, CLEAN_OP_TRUNCATE, CLEAN_OP_PREAMBLE):
        slot = collector.ops.get(op, {"n": 0, "chars": 0})
        print("  %s：%d 块，共 %d 字%s"
              % (op, slot["n"], slot["chars"],
                 "⚠ 失灵" if (blind_ops and op in blind) else ""))
    print("  ★ 哪天这里的数字变了，就是清洗规则又被动了 —— 这是全绿快照给不了的信息。")

    # ---------- 每个文档 ----------
    print("\n【按文档】")
    for name, n in sorted(by_doc.items()):
        sub = [len(d["text"]) for d in docs if d["doc"] == name]
        print("  %-28s %3d 块   最短 %-4d 中位 %-4d 最长 %d"
              % (name, n, min(sub), int(statistics.median(sub)), max(sub)))

    # ---------- 极端样本 ----------
    print("\n【最短的 %d 块】（最容易是碎屑/标题党）" % samples)
    for d in sorted(docs, key=lambda x: len(x["text"]))[:samples]:
        print("  %-4d字  %s｜%s" % (len(d["text"]), d["doc"], d["clause"]))

    print("\n【最长的 %d 块】（最可能混进无关内容）" % samples)
    for d in sorted(docs, key=lambda x: -len(x["text"]))[:samples]:
        print("  %-4d字  %s｜%s" % (len(d["text"]), d["doc"], d["clause"]))

    # ---------- 结论 ----------
    bad = [(name, n) for name, ok, n in checks if not ok]
    print("\n" + "=" * 70)
    # ★ blind_ops（探头失灵）也算 bad —— 体检工具自己坏了，
    #   却返回 0 说"没查到问题"，这是最坏的一种失败。
    if blind_ops:
        print("结论：★ 体检工具自身失灵（清洗账探头收不到明细），本次结果【不可信】。")
    if bad:
        print("结论：%d 项没通过 —— %s"
              % (len(bad), "；".join("%s（%d）" % (name, n) for name, n in bad)))
    else:
        print("结论：以上 %d 项全部通过，没查到已知类型的数据层问题。" % len(checks))
        print("      （注意这只说明「没有已知类型的病」，不等于「语料够用」——")
        print("        语料规模的天花板见 README 的「🔒 数据边界」一节）")
    print("=" * 70)
    return 0 if (not bad and not blind_ops) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
