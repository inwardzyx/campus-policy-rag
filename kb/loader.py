# -*- coding: utf-8 -*-
"""kb/loader.py —— 知识库文件加载器

把 docs/ 树下的 .md 读成 RAG 用的「块（chunk）」列表。

一个 .md 文件 = 一篇文档（顶部 --- 之间写 doc / version / source 元信息），
文件里用 `## 条款名` 切分出的每一段 = 一条可被检索的「块」。

★ 为什么不再写死 RAW_DOCS？
    写死 = 面试官问「你的知识库多大、文档怎么进来的」只能答「我手敲了 10 条」。
    从文件读 = 加文档只要往 docs/ 扔一个 .md，入库把关照常拦脏数据，
    这才像一个「能接真实文档流」的系统，而不是一个写死的 demo。

★ 加载顺序：docs/kb/ 先于 docs/inbox/（详见 load_documents 里的排序 key）。
    原因：近似重复关是「先来的留、后到的像就拦」。干净库先入，
    待审核的脏副本（旧版 / 话术库改写版）作为重复或旧版被拦下；
    反过来若脏数据先入，它会把干净的条款挤掉（那个被删的恰恰是你真正要用的）。
"""

from __future__ import annotations

import collections
import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

REQUIRED_META = ("doc", "version", "source")

# ==================================================================
# 网页「页脚模板」剥离
# ==================================================================
# ★ 为什么要有这段：知识库来自网页抓取，网页的页脚（上一篇/下一篇、学院名单、
#   联系我们、地址、备案号）会和最后一条条款粘在一起被切成同一块。
#   实测两块中招：
#       《学生纪律处分管理规定》第五章-第三十条  原始 495 字 → 真条文仅 38 字（10%）
#       《学生请销假制度》总则-第七条            原始 137 字 → 真条文仅 31 字（23%）
#   这类块长度够、不重复、无手机号身份证、元信息齐全 → **现有 5 道把关一道都拦不住**。
#
# ★ 为什么标记分两组（这个区分是实测逼出来的，不是拍脑袋）：
#   强特征（备案号/版权）固然准，但它们在页脚的**最后几行**；
#   而 `content[:400]` 截断发生在页脚中途 —— 实测那一块被截后的 400 字里
#   **根本没有「粤ICP备」/「版权所有」/「网站管理」**。
#   所以规则必须也认靠前的弱特征（`上一篇`/`下一篇`/`联系我们`），否则
#   在「上游已经出错」的库上会二次失效。只写备案号是不够的。
CHROME_STRONG = ("粤ICP备", "粤公网安备", "版权所有", "网站管理", "网站维护", "扫码关注")
CHROME_WEAK = ("上一篇", "下一篇", "欢迎访问", "联系我们", "党政办电话", "招生咨询")

# 「清洗动作」的标识 —— 随 warning 一起写进日志的 extra 字段，
# 供 scripts/measure_chunk_health.py 按【结构化字段】统计"加载时动了几刀"。
# ★ 为什么不去解析日志文案：那是"判据和被测对象不同源"，改一个措辞就静默失效
#   （第二轮刚修过一颗同类的哑弹，见 service.py::_version_key 的注释）。
#   体检脚本 import 这几个常量 → 两边同源，改名字会一起改。
CLEAN_OP_FOOTER = "剥离网页页脚"
CLEAN_OP_TRUNCATE = "截断丢字"
CLEAN_OP_PREAMBLE = "丢弃第一个 ## 之前的正文"

# ★★ 逐条明细打DEBUG、汇总打 WARNING（2026-10-10 改，原因见下）
#
#   改之前：每条被清洗的块各打一行 warning。
#   实测：263 块的库里一次加载刷 10+ 行 —— 截断 8 条、剥页脚 2 条。
#   问题不是"报了"，而是【正常路径上的数据清洗本来就天天发生】，
#   把它报成 warning，等于每次都喊一遍"出事了"，狼来了。
#   pytest 里更糟：每次跑测试都刷一遍，没人看。
#
#   改之后：
#     · 逐条明细 → logger.debug（要看时开DEBUG 级日志，一行不少）
#     · 末尾一行汇总 → logger.warning，把本次动了几刀、丢多少字讲清楚
#   ⇒ 【不静默的承诺没有削弱】：丢字数和涉及哪些块仍然全部可查，
#     而且比刷 10 行更好读 —— 一行就能回答"这次入库有没有丢东西、丢在哪"。
#
#   ★ 为什么汇总必须带 clean_op 之外的字段：
#     measure_chunk_health.py 读的是【每一条】的 clean_chars（从 extra 里拿），
#     汇总行只是给人看的，不能替代逐条 extra，否则体检脚本的判据就断了。
QUIET_CLEAN_DETAIL = os.environ.get("RAG_QUIET_CLEAN") != "0"


def _log_clean_detail(msg, *args, **kw) -> None:
    """打一条【清洗明细】。

    默认走 DEBUG（一行一条），RAG_QUIET_CLEAN=0 时退回 WARNING。
    留这个开关的原因：调试某份文档为什么被切短时，
    需要"逐条都打出来"这个模式，而不必去改代码再改回来。

    ★ extra 字段照旧挂在这条日志上，不管它落在哪个级别 ——
      scripts/measure_chunk_health.py 装了一个 handler 专门收集它们，
      级别降低不等于收集不到（它自己设level，见该文件）。
    """
    logger.log(logging.DEBUG if QUIET_CLEAN_DETAIL else logging.WARNING, msg, *args, **kw)

# 只有当页脚尾巴达到一定长度才切，避免正文里偶然出现"联系我们"就被削掉。
# 30 字是拍的，但很保守：真页脚动辄上百字，而误伤代价是真条文被削。
MIN_FOOTER_CHARS = 30

# ★ 只认【行首】的标记（行首允许有空格/制表符）—— 网页页脚永远自成一行。
#   为什么必须收紧（2026-09-25 实测）：原先用 `text.find(marker)` 是【全串查找】，
#   句中命中照样切断。实测一条 65 字的合法条款：
#       「第三十条 学生如有疑问请联系我们，联系电话 020-87024621。未按时办理销假
#         且超过准假时间的，按旷课论处，并记入学生档案。」
#   被从"联系我们"处切断，削掉 53 字 → 真条文「按旷课论处」永久丢失，
#   而日志写的是「剥离网页页脚 53 字」—— 把正文当页脚报了。
#   削错是真条文永久丢失，和误删条款是同一类不可逆损失（与 DUP_THRESHOLD 宁严勿松同理）。
#   实测真实语的切点（纪律处分『上一篇』、请销假『联系我们』）本来就在行首，
#   所以这条收紧【不改变任何现有结果】（剥离字数仍是 457 / 106），只是把误伤面收窄。
#
#   语法点：
#     `"|".join(...)` 用竖线把多个标记拼成一个正则分支，等价于"命中其中任意一个"；
#     `re.escape(m)` 把标记里的特殊字符转义成纯文本（这些标记没有，是常识性保险）；
#     `(?m)` 是【多行模式】，让 ^ 表示"每一行的开头"而不是"整个字符串的开头"。
_CHROME_RE = re.compile(
    r"(?m)^[ \t]*(?:" + "|".join(re.escape(m) for m in CHROME_STRONG + CHROME_WEAK) + r")")


def strip_footer(text: str) -> tuple[str, int]:
    """剥掉文本尾部粘着的网页页脚，返回 (清洗后文本, 被剥掉的字数)。

    没命中任何标记 → 原样返回，剥掉 0 字（不会误伤正常条款）。

    ★ 返回的是个「二元组」，`body, n = strip_footer(t)` 这样接。
      只写 `body = strip_footer(t)` 的话 body 会是整个元组，后面当字符串用会炸。

    ★ 2026-09-25 改造：从"全串 find 后手算最靠前"换成"行首锚定的正则 search"。
      两者取到的是【同一个位置】—— 正则的 search 本身就是最左优先的，
      所以这不是"加了规则"，而是用引擎自带的语义换掉了那个手写的 min 循环
      （改完代码反而更短）。实测剥离字数一字不差，但不再误伤句中的"联系我们"。
    """
    m = _CHROME_RE.search(text)     # search 返回【最靠左】的那次匹配，没有则 None
    if m is None:
        return text, 0

    pos = m.start()                 # 注意这是"行首"的位置，不是标记本身的位置
    # 尾巴太短（不值得切，也可能是误伤）→ 不动
    if len(text) - pos < MIN_FOOTER_CHARS:
        return text, 0

    return text[:pos].strip(), len(text) - pos


def _parse_frontmatter(text: str) -> dict | None:
    """解析文件顶部 --- ... --- 里的元信息。格式不对 / 缺字段 → 返回 None。

    用字符串切分而不是真 YAML：知识库的元信息就三个固定字段，
    没必要引入 PyYAML 依赖，手写解析也更好测。
    """
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None                      # 文件根本没用 --- 包裹元信息
    fm = parts[1]
    meta: dict = {}
    for line in fm.splitlines():
        line = line.strip()
        if not line:
            continue
        if ":" not in line:
            return None                  # 元信息里出现无法识别的行
        k, _, v = line.partition(":")
        k, v = k.strip(), v.strip()
        if not k:
            return None
        meta[k] = v
    if not all(k in meta for k in REQUIRED_META):
        return None                      # 三个必填字段少一个都不收
    return meta


def load_documents(root: str | Path, max_chars: int = 400) -> tuple[list[dict], list[str]]:
    """扫描 root 下所有 .md，返回 (块列表, 解析失败的文件路径列表)。

    root 通常为项目里的 docs/ 目录，里面分 kb/（已审核）和 inbox/（待审核）。

    sorted 的稳定排序 + kb 优先：
        ① sorted 保证「加文件 / 改名」不会让「留谁拦谁」随机抖动，测试才能
           稳定断言「留 7 拦 3」；
        ② key 里 (目录名 != "kb") 让 kb 排在前、inbox 排在后 —— 见模块顶部说明。
    """
    root = Path(root)
    docs: list[dict] = []
    errors: list[str] = []
    # 本次加载的清洗账：{操作: 次数} 和 {操作: 累计丢字数}，
    # 末尾汇成一行 warning 报出去（逐条明细见 _log_clean_detail 的注释）
    _clean_stats: dict[str, int] = collections.defaultdict(int)
    _clean_chars: dict[str, int] = collections.defaultdict(int)

    entries = sorted(
        root.rglob("*.md"),
        key=lambda p: (p.parent.name != "kb", p),     # kb/ 先，inbox/ 后
    )
    for path in entries:
        try:
            # ★ 必须指定 utf-8：Windows 默认按 cp936（GBK）读，中文会直接乱码抛错
            text = path.read_text(encoding="utf-8")
        except Exception:
            errors.append(str(path))
            continue

        meta = _parse_frontmatter(text)
        if meta is None:
            errors.append(str(path))                   # 元信息不合格 → 当作坏文件跳过
            continue

        body = text.split("---", 2)[-1]                # 取关闭的 --- 之后的正文
        sections = re.split(r"(?m)^##\s+", body)       # 第 0 段 = 第一个 ## 之前的内容

        # ★ 一个 ## 都没切出来 = 这篇文档产出 0 块。以前这里是【静默的】：
        #   docs 里没有它、errors 里没有它、日志里也没有它 —— 整篇人间蒸发。
        #   最容易踩的写法：标题漏了 ## 后的空格（写成「##第三条」），
        #   实测这种写法 `^##\s+` 一个都匹配不上 → 返回 1 段 → [1:] 是空的 → 循环一次都不进。
        #   当成坏文件走 errors，和上面"元信息不合格"同一个通道，
        #   ★ 2026-10-12 修正：这里原本写"startup 日志和 /health 的数字都能看见它"，
        #     但当时 /health 只返回 chunks/rejected —— 那句话是**假的**
        #     （服务起来之后就没有任何接口看得见"有文件被跳过"）。
        #     ⇒ 现在 /health 真的加了 `load_errors` 与 `load_error_files`（见 service.py::health），
        #       所以这句话才成立。**先修产品，再修这句话**，而不是反过来把注释改软。
        if len(sections) == 1:
            errors.append(str(path))
            continue

        # ★ 第一个 ## 之前还有正文 → 它不属于任何条款，会被丢掉。
        #   丢可以（它进库也不是"条款块"），但不能静默 —— 和截断、剥页脚同一个道理。
        #   真实语料这里只有空行（实测 3 个文件 head 全是 0 字），所以今天丢的是 0 字。
        head = sections[0].strip()
        if head:
            _log_clean_detail("「%s」第一个 ## 之前有 %d 字正文，不属于任何条款，已丢弃：%r",
                              meta["doc"], len(head), head[:30],
                              extra={"clean_op": CLEAN_OP_PREAMBLE, "clean_chars": len(head)})
            _clean_stats[CLEAN_OP_PREAMBLE] += 1
            _clean_chars[CLEAN_OP_PREAMBLE] += len(head)

        for sec in sections[1:]:                       # 按「行首 ## 」切成一条条条款
            clause, _, content = sec.partition("\n")
            content = content.strip()
            if not content:
                continue

            # ★ 先剥页脚，再判长度 —— 顺序不能反。
            #   页脚会「垫高」块的长度，先判长度的话这类块一道都拦不住。
            content, stripped = strip_footer(content)
            if stripped:
                # 不静默清洗：剥了多少、哪一条，必须看得见（和下面截断是同一个道理）
                _log_clean_detail("「%s｜%s」剥离网页页脚 %d 字（剩 %d 字正文）",
                                  meta["doc"], clause.strip(), stripped, len(content),
                                  extra={"clean_op": CLEAN_OP_FOOTER, "clean_chars": stripped})
                _clean_stats[CLEAN_OP_FOOTER] += 1
                _clean_chars[CLEAN_OP_FOOTER] += stripped

            # ★ 截断前先留一份全文（见下面的 text_full）。
            full_text = content

            # ★ 超长不再静默截断：丢了多少字要报出来。
            #   为什么现在【不】改成"切成多块"：剥完页脚后全库超长块是 0 个，
            #   为不存在的需求写切分器是过度设计。真需要时再补。
            #   ★ "什么时候才真的该上切多块"有明确判据，只有一处定义：
            #     `scripts/measure_chunk_health.py::grade_truncation`
            #     （主判据 单块丢字 ≥100 → 当天上；次判据 被截断块 ≥3 → 该上；
            #       反向判据 <400 的块一个都不许切）。别在别处再写一套。
            #   （另：就算这里不截，bge-small-zh 也有 512 token 上限，
            #    会在 embedding 里把尾巴悄悄吃掉 —— 所以截断这道保护不能整个撤掉。）
            if len(content) > max_chars:
                _log_clean_detail("「%s｜%s」长 %d 字，超过 max_chars=%d，已截断（丢 %d 字）",
                                  meta["doc"], clause.strip(), len(content),
                                  max_chars, len(content) - max_chars,
                                  extra={"clean_op": CLEAN_OP_TRUNCATE,
                                         "clean_chars": len(content) - max_chars})
                _clean_stats[CLEAN_OP_TRUNCATE] += 1
                _clean_chars[CLEAN_OP_TRUNCATE] += len(content) - max_chars
                content = content[:max_chars]

            docs.append({
                "doc": meta["doc"],
                "clause": clause.strip(),
                "version": meta["version"],
                "source": meta["source"],
                # ★ 两个字段分工（2026-10-10 还的截断欠账）：
                #   text      = 截到 max_chars，**只给 embedding 用**（bge-small 只有 512 token）
                #   text_full = 截断前的全文，给精排判断 / 答案生成 / 返回给用户看
                #   为什么必须两个都要：语料里 8 块 ≥400 字（最长 1870 字，
                #   PDF 转存稿把表格拍平了），截掉的部分以前是**入库即丢** ——
                #   模型判断"这条能不能答"时看不到它，用户也看不到。
                #   切分器试过三种判据全失败（experiments/clean_corpus.py 顶部），
                #   所以不切；但"不切"不等于"只能丢"。
                "text": content,
                "text_full": full_text,
            })

    # ★ 一行汇总代替逐条 warning（逐条明细已降级到 DEBUG，见 QUIET_CLEAN_DETAIL）
    #   汇总里【必带总丢字数】——"动了几刀"看不出严重性，
    #   "丢了 5241 字"和"丢了 0 字"是两件完全不同的事。
    if _clean_stats:
        parts = [f"{op} {n} 处（丢 {chars} 字）"
                 for op, n in _clean_stats.items()
                 for chars in (_clean_chars.get(op, 0),)]
        logger.warning("加载时清洗了 %d 块：%s", sum(_clean_stats.values()),
                       "、".join(parts))
    return docs, errors
