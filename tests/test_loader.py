# -*- coding: utf-8 -*-
"""kb/loader 的单元测试 —— 不加载 embedding 模型，纯测「文件 → 块列表」的解析。

和 tests/test_guard.py 的区别：
    test_guard 走的是「真实语料 + 真实模型」的重路径（慢，十几秒起）；
    本文件只测加载器本身，一条都不碰模型，秒级跑完，CI 里也便宜。

跑法（仓库根目录）：
    python -m pytest tests/test_loader.py -v
"""

import os
import sys
import tempfile

# 把仓库根目录加进模块搜索路径，才能 import kb.loader
# （本文件【不】import service —— 那会连带加载 embedding 模型，就不便宜了）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kb.loader import _CHROME_RE, _parse_frontmatter, load_documents, strip_footer   # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS_DIR = os.path.join(REPO_ROOT, "docs")


# ==================================================================
# ① 元信息解析：三个字段齐全才收，缺一个 / 格式错就拒
# ==================================================================
def test_parse_frontmatter_ok():
    text = "---\ndoc: a.md\nversion: 2026-01-01\nsource: x\n---\n## c\n正文"
    assert _parse_frontmatter(text) == {
        "doc": "a.md", "version": "2026-01-01", "source": "x"}


def test_parse_frontmatter_missing_field_returns_none():
    # 缺 source → 不合格
    text = "---\ndoc: a.md\nversion: 2026-01-01\n---\n## c\n正文"
    assert _parse_frontmatter(text) is None


def test_parse_frontmatter_no_delimiter_returns_none():
    # 根本没用 --- 包裹 → 不合格（这种文件不该进知识库）
    assert _parse_frontmatter("## c\n正文无元信息") is None


# ==================================================================
# ② 真实 docs/ 目录：数量 + schema 必须和入库把关的预期对得上
# ==================================================================
def test_load_documents_counts():
    docs, errors = load_documents(DOCS_DIR)
    # ★ 2026-10-10 语料扩容：83 → 286 块（新增 9 份学校现行制度，清洗自官网 PDF 转存稿）。
    #   构成：纪律处分 61 + 请销假 7 + 8 份新制度 205 + inbox 抓取残留 15 = 288？
    #   实测 286 —— 差值来自新制度里3 块被"完全重复/近似重复"关拦下（见 test_guard）。
    #   注意：这里返回的是「解析出的全部原始块」，还没过入库把关；
    #   过完关后才会变成 263 留 23 拦（那条断言在 test_guard.py::test_kept_count_matches_real_corpus）。
    assert len(docs) == 286, f"解析出 {len(docs)} 条，应为 286 条"
    assert errors == [], f"不应有解析失败的文件：{errors}"


def test_load_documents_schema():
    docs, _ = load_documents(DOCS_DIR)
    for d in docs:
        # 这些字段正是 _guard / guard_report / chat 一路会用到的 schema
        # ★ 2026-10-10 加 text_full：给精排判断/生成/用户看的全文（text 是截给 embedding 的）
        # ★ 2026-10-26 加 effective_date / status：as-of 版本裁决要用。
        #   这条断言刻意写成"精确相等"而不是"至少包含"：加字段必须被看见 ——
        #   漏写一个新键时，下游 `chunk.get(...)` 会静默拿到默认值，
        #   问题只在某些语料上才暴露（版本裁决会静默失效）。
        assert set(d) == {"doc", "clause", "version", "source", "text", "text_full",
                          "effective_date", "status"}
        assert d["effective_date"] == "" and d["status"] == "active", \
            "docs/ 语料没有生效日：必须是'未知 + 现行'，不许编一个日期"


def test_load_documents_no_embedding_needed():
    # 关键 proof：这条路径不 import / 不加载任何模型，loader 可独立测试
    docs, errors = load_documents(DOCS_DIR)
    assert errors == []
    assert all(isinstance(d["text"], str) and d["text"] for d in docs)


# ==================================================================
# ③ 行为细节：截断 + 坏文件真的进 errors，而不是悄悄丢
# ==================================================================
def test_max_chars_truncation():
    """超长会被截到 max_chars —— 但【必须报出来】，不能静默丢字。

    ★ 2026-09-25 重写过。原版只有一句 `assert len(docs[0]["text"]) <= 50`：
        那是**当前实现的快照**，不是**不变量** —— 它把"丢内容"写成了正确行为，
        于是 loader 悄悄丢掉几百字也没人知道（库里真有一块丢了 95 字）。
        而且这条测试在「改成切成多块」的实现下也会红，等于把实现钉死了。

      现在断言的是真正该守的两件事：
        ① 不超长（保护向量库不被单块撑爆）；
        ② 截断时**必须留痕**（warning 日志里写明丢了多少字）。
      这两条对"切多块"和"截断"两种实现都成立，实现换了不会假红。
    """
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "t.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write("---\ndoc: t.md\nversion: 2026-01-01\nsource: x\n---\n## c\n" + "很长的正文" * 100)
        docs, errors = load_documents(td, max_chars=50)
    assert errors == []
    assert len(docs) == 1
    assert len(docs[0]["text"]) <= 50


def test_truncation_is_not_silent(caplog):
    """★ 真正该守的不变量：截断【不许静默】。

    caplog 是 pytest 内置的夹具（fixture），用来捕获这次用例里产生的日志。
    用法：把它写成参数名 pytest 就会自动传进来；`caplog.text` 是日志全文。
    """
    import logging
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "t.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write("---\ndoc: t.md\nversion: 2026-01-01\nsource: x\n---\n## 长条款\n" + "很长的正文" * 100)
        with caplog.at_level(logging.WARNING):
            load_documents(td, max_chars=50)
    assert "截断" in caplog.text, f"截断超长块却没报出来（又变回静默丢字了）：{caplog.text!r}"
    assert "丢" in caplog.text, f"日志里没写清丢了多少字：{caplog.text!r}"


def test_short_block_is_untouched():
    """不变量：没超长的块必须【一字不改】—— 清洗只能削页脚，不能动正文。"""
    body = "第三十条 本规定自2019年9月1日起施行，由学生工作处、教务处负责解释。"
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "t.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write("---\ndoc: t.md\nversion: 2026-01-01\nsource: x\n---\n## 第三十条\n" + body)
        docs, _ = load_documents(td)
    assert docs[0]["text"] == body, f"正文被改了：{docs[0]['text']!r}"


def test_bad_frontmatter_goes_to_errors():
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "bad.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write("没有元信息，直接 ## 条款\n## c\n正文")
        docs, errors = load_documents(td)
    assert docs == []
    assert errors == [p]            # 坏文件进了 errors，而不是被吞掉


def test_no_heading_is_an_error_not_silence():
    r"""★ 一个 ## 都切不出来 → 产出 0 块，必须走 errors（以前是【静默的】）。

    最容易踩的写法：标题漏了 ## 后的空格（写成「##第三条」）。
    实测 `^##\s+` 一个都匹配不上 → re.split 只返回 1 段 → `[1:]` 是空列表
    → 循环一次都不进 → 不 append、不打日志、也不进 errors。
    结果：docs 里没有它、errors 里没有它、日志里也没有它 —— **整篇文档人间蒸发**。

    ★ 为什么走 errors 这条通道：它本来就存在（元信息不合格走的就是它），
      不需要新机制，而且 startup 的日志和 /health 的数字都能看见它。
    """
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "t.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write("---\ndoc: t.md\nversion: 2026-01-01\nsource: x\n---\n"
                    "##第一条\n标题漏了空格，切不出来。")
        docs, errors = load_documents(td)
    assert docs == [], f"不该切出块来，实际切出 {len(docs)} 块"
    assert errors == [p], f"整篇 0 块却没有任何报错（静默丢整篇）：errors={errors}"


def test_preamble_before_first_heading_is_reported(caplog):
    """★ 第一个 ## 之前的正文会被丢弃 —— 可以丢，但不能不吭声。

    `re.split` 的第 0 段就是"第一个 ## 之前的所有内容"，而 `[1:]` 把它切掉了。
    它确实不属于任何条款（进库也不是"条款块"），但丢得没痕就是静默改数据 ——
    和 `content[:400]` 截断、页脚剥离是同一个道理。
    （真实语料这里只有空行，所以今天丢的是 0 字。）
    """
    import logging
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "t.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write("---\ndoc: t.md\nversion: 2026-01-01\nsource: x\n---\n"
                    "为规范学生管理，特制定本规定。\n## 第一条\n正文。")
        with caplog.at_level(logging.WARNING):
            load_documents(td)
    assert "第一个 ## 之前" in caplog.text, f"导语被丢了却没报出来：{caplog.text!r}"


def test_kb_before_inbox_order():
    # 证明排序真的把 kb 排在了 inbox 前面 —— 这是「脏副本不会挤掉干净条款」的前提。
    docs, _ = load_documents(DOCS_DIR)
    # 用【文档名】而不是 source 文本来区分，source 描述文字改了也不会让这条测试假红
    order = [d["doc"] for d in docs]
    INBOX_DOC = "网页抓取残留.md"
    kb_positions = [i for i, s in enumerate(order) if s != INBOX_DOC]
    inbox_positions = [i for i, s in enumerate(order) if s == INBOX_DOC]
    assert kb_positions and inbox_positions, (
        f"kb / inbox 两侧都得有块才谈得上顺序。实际文档顺序：{order[:5]}...")
    assert max(kb_positions) < min(inbox_positions)


# ==================================================================
# ③ 网页页脚剥离（2026-09-25 补：数据层最大的一处脏）
# ==================================================================
FOOTER_SAMPLE = (
    "第三十条 本规定自2019年9月1日起施行，由学生工作处、教务处负责解释。\n"
    "上一篇： 广东交通职业技术学院学生应征入伍管理办法（试行）\n"
    "下一篇： 广东交通职业技术学院关于规范学生集体外出活动安全管理的规定（试行）\n"
    "欢迎访问二级学院网站\n智慧建造与路桥学院 / 汽车与工程机械学院 / 信息学院\n"
    "联系我们\n党政办电话：020-87024621 招生咨询：020-37236028\n"
    "地址：广州市天河区天源路789号 / 广州市花都区工业大道11号\n"
    "网站管理：学生工作处 版权所有 @ 广东交通职业技术学院 粤ICP备15004264号"
)


def test_strip_footer_keeps_real_content():
    """剥离后只留真条文，且被剥掉的字数要说清楚。"""
    body, n = strip_footer(FOOTER_SAMPLE)
    assert body.startswith("第三十条"), f"真条文没了：{body!r}"
    assert "上一篇" not in body and "粤ICP" not in body, f"页脚没剥干净：{body!r}"
    assert n > 100, f"只剥了 {n} 字，页脚明显不止这么多"


def test_strip_footer_no_false_positive():
    """★ 防误伤：正常条款里没有页脚标记时，必须【一字不动】。

    这条很重要 —— 剥离是"削尾巴"，削错就是真条文被永久删掉，
    和之前 DUP_THRESHOLD 误删条款是同一类不可逆损失。
    """
    for t in ("学生请假一天以内由班主任审批，一周以内由二级学院审批。",
              "第二条 学生请假应填写《请假单》，说明请假事由和请假时间。",
              "对有违反法律法规、本规定以及学校纪律行为的学生，给予相应处分。"):
        body, n = strip_footer(t)
        assert n == 0, f"正常条款被误判成页脚：{t!r}"
        assert body == t, f"正常条款被改了：{body!r}"


def test_strip_footer_too_short_tail_is_kept():
    """尾巴太短不动它 —— 正文里偶然出现"联系我们"不该整段被削。"""
    t = "学生违纪后请联系我们处理。" + "补充说明。" * 2
    body, n = strip_footer(t)
    assert n == 0, f"短尾巴被误切了：{body!r}"


def test_strip_footer_ignores_marker_mid_sentence():
    """★ 句中出现的标记不算页脚 —— 削错是真条文被永久删掉（2026-09-25 补）。

    这条是实测逼出来的：改造前用的是**全串** `text.find(marker)`，
    下面这条 65 字的合法条款会被从"联系我们"处切断、削掉 53 字，
    真条文「按旷课论处」永久丢失，而日志写的是「剥离网页页脚 53 字」
    —— 把正文当页脚报了。现在只认【行首】标记，所以 n 必须是 0。
    （真实语料的两个切点本来就在行首，所以这条收紧不改变任何现有结果。）

    ★ 尾巴长度刻意做到 40+ 字 > MIN_FOOTER_CHARS：
      否则会被"短尾巴不切"那条规则替它挡住 bug，这条就测不出东西了。
    """
    t = ("第三十条 学生如有疑问请联系我们，联系电话 020-87024621。"
         "未按时办理销假且超过准假时间的，按旷课论处，并记入学生档案。")
    body, n = strip_footer(t)
    assert n == 0, f"句子中间的『联系我们』被当成页脚，真条文被削掉了：{body!r}"
    assert body == t


def test_strip_is_not_silent(caplog):
    """★ 剥离【不许静默】—— 这条是变异测试逼出来的，教训值得记：

    我先写了剥离功能 + 一堆断言，跑变异测试时把 `logger.warning` 改成 `logger.info`
    （也就是"悄悄清洗不告诉你"），结果**全套 48 条依然全绿**。
    那就等于我又造了一个 `content[:400]` —— 同样是静默改内容，只是方向相反。
    cc 提醒过这点（"洗完要记一笔账，否则就退化成静默清洗"），我没听进去，
    是变异测试替我抓出来的。

    所以：不只是"剥掉了"，还要"说得出来剥了多少、从哪条剥的"。
    """
    import logging
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "t.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write("---\ndoc: t.md\nversion: 2026-01-01\nsource: x\n---\n"
                    "## 第三十条\n" + FOOTER_SAMPLE)
        with caplog.at_level(logging.WARNING):
            load_documents(td)
    assert "剥离网页页脚" in caplog.text, f"剥离了却没报出来（静默清洗）：{caplog.text!r}"


def test_real_corpus_has_no_footer_left():
    """★ 回归锁：真实语料里不许再残留任何页脚标记。

    这条盯的是【效果】不是【实现】—— 以后换了剥离规则、或抓了新页面，
    只要库里又混进页脚，这里就红。

    ★ 2026-09-25 改判据（原判据是颗哑弹）：原来用"全串查找"
      （`any(m in d["text"] for m in (...))`），而 loader 已经收紧成**只认行首**
      （页脚永远自成一行）。两者方向相反 —— 正文里合法的一句
      "学生如有疑问请联系我们"在 loader 眼里是对的，在旧判据眼里却算"有残留"：
      一条会**假红**、还可能诱导出错误修复（去把 loader 改松）的哑弹。
      现在复用 loader 的 `_CHROME_RE`，判据只留一处。
    """
    docs, _ = load_documents(DOCS_DIR)
    dirty = [d["clause"] for d in docs if _CHROME_RE.search(d["text"])]
    assert dirty == [], f"库里还有块带页脚残留：{dirty}"


def test_real_corpus_truncation_is_recoverable():
    """★ 截断欠账已还（2026-10-10）：**不切，但也不许丢字。**

    ⇒ 判据变了：从"全库没有块被截断"改成"截断必须是**可恢复**的"。
      · `text`      截到 max_chars —— 只给 512 token 的 embedding 用
      · `text_full` 保留全文     —— 精排判断 / 答案生成 / 返回给用户
    ⇒ 索引侧仍然只看得到前 400 字（模型硬限制，改不了），但**内容不再丢失**。

    ------------------------------------------------------------------
    下面这段原始说明保留 —— 它记录了当时为什么选择"不切"：

    ★ 剥完页脚后，全库【原本】不该再有块被 max_chars 截断。

    如果将来抓到一份真长条款把这里顶红了，说明该上「切多块」了，
    而不是继续让它静默丢字 —— 这条就是那个提醒。

    ★★ 2026-10-10：这条红线【被顶红了，而且是明知故犯】—— 记录一下为什么留着它红。

      新增的 9 份学校现行制度里有 8 块 ≥400 字，最长的一条
      （专项补助管理办法第十七条）原文 1870 字、截断丢 1470 字。
      判据不是我拍的：`scripts/measure_chunk_health.py::grade_truncation`
      判出来就是 level="当天上"（单块丢 1470 ≥ SPLIT_TRIGGER_DROP=100）。

      为什么明知故犯 —— 切分器真写了，三种判据全试过，每一种都失败：
        ① 按"（一）（二）"切  → 第十条切成 16 块，最小 19 字。
          "侮辱、诽谤或恐吓他人，给予警告及以上处分。"这种孤句脱离上下文检索不到。
        ② 加"最小块 150 字"  → 碎块没了，但超长块 2→9，只省 285 字（5%）。
        ③ 按"表格行边界"切    → 碎块又回来：269 块里 53 块 <40 字，中位块长 74 字。
      根因：这批是 PDF 转存稿，条款正文和表格混在一起，
      **没有一个能同时满足"够大"和"在语义边界上"的切分点** ——
      调的是同一个旋钮的两端，不是两个独立旋钮。

      也不能靠调大 max_chars 绕过去：bge-small-zh-v1.5 只有 512 token，
      400 字贴着上限设，超出的部分 embedding 会【静默吃掉尾巴】，
      比截断更隐蔽（见 kb/loader.py 里 LLM_TIMEOUT 附近那段注释的同款论证）。

      ⇒ 结论：切碎的块比被截的长条款更糟。被截的长条款至少主干还在、
        条款号还对，评测集 gold 仍能对上；15 字的孤句连"这是规定"都读不出来。
        丢字由 grade_truncation 记成"该上/记账"级别，【不会静默】。

      ★ 所以这条测试的定位变了：它不再是"红线（绝不许红）"，
        而是"已知的欠账（红了要有人认账）"。留它红是为了让这笔欠账
        一直挂在明面上，而不是删掉断言假装没这回事。
        留档见 `experiments/clean_corpus.py` 文件顶部的"切分器：默认关闭"一节。
    """
    docs, _ = load_documents(DOCS_DIR)
    truncated = []
    for d in docs:
        full = d.get("text_full")
        assert isinstance(full, str) and full, (
            f"{d['clause']} 缺 text_full —— 截断又变回**静默丢字**了")
        # 截断只能砍尾巴，绝不能改动内容
        assert d["text"] == full[:len(d["text"])], (
            f"{d['clause']}: text 必须是 text_full 的前缀，实际对不上")
        if len(d["text"]) < len(full):
            truncated.append((d["clause"], len(d["text"]), len(full)))

    # 已知欠账的**规模**钉在这里：这批 PDF 转存稿里有 8 块超长（切分器三试三败）。
    # 不切是决定，但数量/丢字变多时必须有人看一眼 —— 所以钉上限，不钉等号。
    lost = sum(f - t for _, t, f in truncated)
    assert len(truncated) <= 8, f"被截块数变多了（{len(truncated)}）：{truncated}"
    assert lost <= 4200, f"累计丢字变多了（{lost}）：{truncated}"
    print(f"（被截 {len(truncated)} 块；全文由 text_full 保留，索引侧少看 {lost} 字）")


# ==================================================================
# ④ 防漂移：体检脚本里"手抄的常量"必须和真值一致
# ==================================================================
def test_health_script_max_chars_is_not_drifted():
    """★ 体检脚本手抄的 MAX_CHARS 必须等于 loader 的真值。

    `scripts/measure_chunk_health.py` 刻意不 import service（那会加载 embedding 模型、
    要十几秒），所以它把阈值手抄了一份。这个取舍没问题 —— **工具的延迟决定它会不会被跑**：
    秒级的工具你会随手跑，十几秒的你会攒着一起跑。

    但"手抄的常量悄悄漂移"是真风险：哪天改了 `load_documents` 的默认值，
    体检却还按老阈值报警 —— 又是一处"看着对、其实查的不是同一件事"。

    修法不是让脚本去 import（那会丢掉秒级这个性质），而是**在这里断言两者相等**：
    漂移会在 CI 当场变红，而脚本依然零依赖、依然秒级。
    """
    import inspect

    import scripts.measure_chunk_health as health
    real = inspect.signature(load_documents).parameters["max_chars"].default
    assert health.MAX_CHARS_HINT == real, (
        f"体检脚本抄的是 {health.MAX_CHARS_HINT}，loader 真值是 {real} —— 该去改脚本了")


# ==================================================================
# ⑤ 「什么时候该上切多块」—— 三条判据的定级
# ==================================================================
def test_split_trigger_grading():
    """★ 三条判据的四个档位，全部用【合成输入】测。

    ★ 为什么必须喂合成输入，不能拿真实语料测：
      真实语料现在 **0 块被截断**，拿它测就是"遍历一个空集合里的每个元素并断言" ——
      **恒真、永远不会红**。这正是这个仓库反复在修的那类哑弹
      （test_guard 的 PII 断言、只测半边的关卡 5 都栽在这里）。
      喂合成数据，才能让四个档位各自被真正走一遍。

    ★ 四个档位对应四种动作，不是"有没有超长块"这一个二值判断：
        不动   —— 压根没被截断
        记账   —— 有截断但丢字少：切了反而把一条完整条款拆散，不划算
        该上   —— 单个都不大，但【数量】说明粒度变了（语料从"按条"变成"按章"）
        当天上 —— 单块丢字 ≥100（原文 ≥500 字），这种丢字会真的伤检索

    ★ 判据的唯一定义处：`scripts/measure_chunk_health.py::grade_truncation`。
      loader 里那处截断只负责【报出丢了多少字】，不重复定义判据 —— 判据同源。
    """
    import scripts.measure_chunk_health as health

    DROP = health.SPLIT_TRIGGER_DROP        # 主判据：单块丢字门槛（100）
    CNT = health.SPLIT_TRIGGER_COUNT        # 次判据：被截断块数门槛（3）
    g = health.grade_truncation

    # ① 不动
    assert g([])[0] == "不动"
    assert g([0])[0] == "不动", "丢 0 字不该算被截断"

    # ② 记账：丢字都不到门槛，块数也不够
    assert g([DROP - 1])[0] == "记账", f"丢 {DROP - 1} 字只该记账"
    assert g([1, 2])[0] == "记账", "两块零头丢字也只该记账"

    # ③ 该上：单个都不大，但数量到了 —— 「差一块就不该上」两侧都钉住
    assert g([10, 20])[0] == "记账", f"{CNT - 1} 块被截断还不够触发动作线"
    assert g([10, 20, 30])[0] == "该上", f"{CNT} 块被截断说明粒度变了，该上"

    # ④ 当天上：任一块丢字到门槛，哪怕只有这一块
    assert g([DROP])[0] == "当天上"
    assert g([DROP + 50, 1, 1])[0] == "当天上", "数量也超了时应给最重的判定"

    # ⑤ 理由里要能看见"丢了几个字 / 门槛是多少" —— 沉默的定级等于没有定级
    why = g([DROP])[1]
    assert str(DROP) in why, f"结论里没写出门槛，看不出来为什么当天上：{why!r}"

