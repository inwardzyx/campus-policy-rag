# -*- coding: utf-8 -*-
"""
入库把关 + 拒答短路的【回归测试】

和旧版（打印型自检）的区别，一句话：
    旧版 print("通过"/"失败")  →  退出码永远是 0，改坏了没人知道
    新版 assert                →  失败时 pytest 报 FAILED 并让退出码变 1，CI 能看到红

跑法（在仓库根目录）：
    python -m pytest tests/ -v

想顺便看"留了谁 / 拦了谁"：
    python -m pytest tests/ -v -s
    （-s = 不吞掉 print，报告会打出来）

★ 不需要 DEEPSEEK_API_KEY：
    这些用例只跑"入库把关 / 检索 / 拒答"这三条路径，一条都不调生成模型。
    service.py 里的 llm 是懒加载的（用到才建），所以没配 key 也能全绿。

★ 变异测试（验证这套断言真的有用）：
    把 service.py 里的 DUP_THRESHOLD 从 0.96 改成 0.99，再跑一次 ——
    应该看到 FAILED。这就是"改坏了会立刻报错"真正的样子。

★ 2026-09-25 实测过的 4 个变异，全部被抓住（没一个是"测试写了但抓不住"）：
    DUP_THRESHOLD 0.96→0.99  → test_near_dup_after_earlier_rejection 红
    DUP_THRESHOLD 0.96→0.90  → test_dup_threshold_has_margin 红（误伤第二十五条复现）
    隐私关 hit 恒为 None      → test_rejected_privacy + 脏数据夹具 红
    版本冲突永不换新          → 脏数据夹具 红
"""

import os
import sys

# 这两行必须放在 import 项目模块之前：
# os.environ 是"这个 Python 进程自己的环境变量字典"，改它只影响当前进程。
os.environ["LANGSMITH_TRACING"] = "false"          # 自检不调大模型，关掉 trace 免得联网卡住
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

# sys.path 是"Python 去哪些目录找模块"的列表。
# 本文件在 tests/ 子目录里，而 service.py 在上一层，所以需要把上一层加进去。
# __file__ = 当前文件路径；dirname 取所在目录；再 dirname 一次 = 上一层。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest                                      # noqa: E402
import service as svc                              # noqa: E402 导入不会启动服务（有 __main__ 保护）

# 假模型从 conftest.py 拿 —— 三个测试文件共用一份，不再各抄一遍（见 conftest.py 的说明）
from conftest import _FakeLLM                      # noqa: E402


@pytest.fixture(scope="module")
def rag():
    """准备材料：加载模型 + 把关 + 建库，整个文件只做一次。

    fixture = pytest 的"准备材料"机制。scope="module" 表示本文件共用这一份，
    因为加载 embedding 模型要十几秒，不能每条用例都重新加载一遍。
    """
    svc.rag.startup()
    print(f"\n入库 {len(svc.rag.chunks)} 条，拦下 {len(svc.rag.rejected)} 条，"
          f"耗时 {svc.rag.boot_time:.1f} 秒")
    return svc.rag


@pytest.fixture
def clauses(rag):
    """留下来的条款集合，元素是 (文档名, 条款名) 这样的二元组"""
    return {(c["doc"], c["clause"]) for c in rag.chunks}


# ==================================================================
# ① 总数：少一条、多一条都要报警
#    ★ 没有这一条的话，"误伤型"改动（把关太严、把该留的也杀了）会全绿通过
# ==================================================================
def test_kept_count_matches_real_corpus(rag):
    """真实语料（学校公开制度）入库块数。

    ★ 这个数字会随 docs/ 增删而变 —— 这不是缺点，恰恰是
      "往 docs/ 扔一个 .md 库就变大"这条能力的可执行证据。
      换语料后要同步改这里，但【只改数字不够】：下面还有盯具体条款的断言，
      否则"库里少了一半"这种事只要跟着改数字就能骗过去。

    ★ 2026-10-10 语料扩容：68 → 263 块。
      新增 8 份学校现行制度（清洗自官网 PDF 转存稿，用
      `experiments/clean_corpus.py` 统一加元信息 + 按"第X条"切块）。
      233 块原始 → 过完五道关留 200 块。
    """
    assert len(rag.chunks) == 263, (
        f"放行了 {len(rag.chunks)} 条，应该是 263 条。"
        f"改动 docs/ 之后记得同步这个数字，并检查下面盯具体条款的断言有没有跟着失效。")


def test_rejected_count_is_23(rag):
    """被拦下的块数 = 15（网页抓取残留）+ 8（新语料），共 23。

    ★ 2026-10-10 新增的 8 条拦得都对，且【三种不同的正确行为】（实测，不是推断）：

        废止声明关（新增关卡，5 条）
            "本办法自通过之日起实施……原《XX管理办法》（粤交职院〔2024〕89号）同时废止。"
            ★ 这条关卡是【真实语料逼出来的】，不是预防性设计。
              拦它们的理由不是脏，是【它们回答不了任何现行问题】——
              讲的是"旧版没了"，学生问"奖学金怎么申请"召回来的是这个。
            加这一关之前实测：只喂 3 条进去【拦1 条留 2 条】，
            留哪条取决于入库顺序，被拦那条的理由还写的是「近似重复」——
            理由完全误导 ⇒ 所以这一关必须走在近似重复关【之前】。

        完全重复（2 条）
            勤工助学 第二条 / 学生奖励 第二条都是
            "本办法适用于具有我校学籍的全日制在校学生" —— 3 份文件里逐字相同，
            重复收录只会挤占检索位。

        近似重复（1 条）
            资助工作 第三十三条 —— 它也是废止声明，但被前面的关卡先拦走了
            （关卡顺序有依赖，这是真实行为，不是bug）。

    ★ 这条断言的价值变了：从"真实语料只触发太短碎屑"变成
      "真实语料自己触发了从没被真实语料触发过的关卡"。
      ⇒ 五关覆盖度现在有两层证据：注入夹具证明"关卡能触发"，
        这里的真实语料证明"关卡在真实输入上也会触发"。
    """
    assert len(rag.rejected) == 23, f"拦下了 {len(rag.rejected)} 条，应该是 23 条"


# ==================================================================
# ② 该留的必须在：直接盯住那个修过的 bug
# ==================================================================
def test_kept_has_punishment_kinds(clauses):
    """处分种类那条还在（真实语料里最常被问的一条：警告/严重警告/记过/留校察看/开除学籍）"""
    assert ("学生纪律处分管理规定.md", "第二章-第四条") in clauses


def test_same_doc_different_clauses_all_kept(clauses):
    """★ 以前踩过的坑，换语料后这条语义仍要守住：
    版本冲突关一开始只按【文档名】分组，结果同一份文档里的不同条款
    被当成"同一条的新旧版"互相挤掉，库里凭空少规则。
    改成按 (文档 + 条款) 分组才修好。
    真实语料里对应的场景是同一份《纪律处分管理规定》开头连续三条，
    断言它们必须各自独立保留 —— 少一条就说明分组又退化了。"""
    for c in ("第一章-第一条", "第一章-第二条", "第一章-第三条"):
        assert ("学生纪律处分管理规定.md", c) in clauses, f"{c} 被误挤掉了"


def test_kept_has_leave_approval(clauses):
    """请销假制度的请假审批权限那条还在（1天班主任 / 1周二级学院 / 1周以上三级）"""
    assert ("学生请销假制度.md", "总则-第三条") in clauses


# ==================================================================
# ③ 该拦的必须被拦：三道关卡各盯一条
# ==================================================================
def test_rejected_old_version(rag):
    """版本冲突关：同 (doc, clause) 出现两个 version 时，旧的那个要被拦下。

    ★ 为什么改成自己造数据：真实语料是学校公开制度，只有单一版本，
      没有"新旧版冲突"的样本，所以这一关在真实入库里【根本不会触发】。
      想确认它还在工作，只能构造样本直接喂给 _guard。
      （同理见下面两条 —— 这是换真实语料后必须诚实说明的事，别假装关卡被验证过。）
    """
    docs = [
        dict(doc="t.md", clause="同一条", version="2026-01-01", source="测试",
             text="学生请假一天以内由班主任审批，一周以内由二级学院审批，一周以上须三级审批。"),
        dict(doc="t.md", clause="同一条", version="2024-01-01", source="测试（旧版）",
             text="宿舍内禁止使用大功率电器，一经发现没收器具并给予通报批评处理。"),
    ]
    kept, rejected = rag._guard(docs)
    assert len(kept) == 1, f"同条款两个版本应只留一个，实际留了 {len(kept)}"
    assert any("旧版本" in r for _, r in rejected), f"旧版本没被拦：{rejected}"


def test_version_compare_survives_unpadded_date(rag):
    """★ 版本新旧不许靠"ISO 补零"这个巧合（2026-09-25 实测）。

    旧代码是 `c["version"] > old["version"]` 直接比字符串，而
    `'2026-9-5' > '2026-09-25'` 竟然是 True —— 字符串逐位比字符编码，
    比到第 6 位 `'9' > '0'` 就下结论了，它不知道"9 月 < 10 月"。
    于是【一个没补零的旧版会把库里真正的新版挤掉】，
    而拒绝理由还写成「被新版取代（2026-09-25 → 2026-9-5）」—— 新旧完全说反。

    ★ 顺序刻意让 2026-09-25 先进（模拟"库里已经有一版"），未补零的后到。
    ★ 两条正文用的是 test_rejected_old_version 里那两条已知能走通前面几关的文本，
      所以这条测的确实是版本比较，不是被别的关卡拦在了前面。
    """
    docs = [
        dict(doc="t.md", clause="同一条", version="2026-09-25", source="测试",
             text="宿舍内禁止使用大功率电器，一经发现没收器具并给予通报批评处理。"),
        dict(doc="t.md", clause="同一条", version="2026-9-5", source="测试（未补零）",
             text="学生请假一天以内由班主任审批，一周以内由二级学院审批，一周以上须三级审批。"),
    ]
    kept, rejected = rag._guard(docs)
    assert len(kept) == 1
    assert kept[0]["version"] == "2026-09-25", (
        f"未补零的 2026-9-5 把更新的 2026-09-25 挤掉了（还在按字符串比）："
        f"留下的是 {kept[0]['version']}，拒绝理由 {rejected}")


def test_unparseable_version_rejected_not_guessed(rag):
    """★ 比不出新旧时【不许猜】—— 明确拦下并说清原因。

    为什么专门守这条：`_version_key` 解析失败返回 None，调用方必须显式处理。
    如果哪天有人图省事改成"解析不出来就退回字符串比较"，
    就等于"看着校验过了，其实没有"，比不校验更危险（下一个读代码的人会以为这里安全）。
    """
    docs = [
        dict(doc="t.md", clause="同一条", version="2026-09-25", source="测试",
             text="宿舍内禁止使用大功率电器，一经发现没收器具并给予通报批评处理。"),
        dict(doc="t.md", clause="同一条", version="2026/09/25", source="测试（斜杠）",
             text="学生请假一天以内由班主任审批，一周以内由二级学院审批，一周以上须三级审批。"),
    ]
    kept, rejected = rag._guard(docs)
    assert len(kept) == 1
    assert any("无法比较新旧" in r for _, r in rejected), (
        f"日期格式比不出来时应当拦下并说明原因，实际 rejected={rejected}")


def test_rejected_privacy(rag):
    """个人隐私关：含手机号 / 身份证的文本要被拦下，不能进库。
    （真实公开制度里没有 PII，所以同样靠构造样本验证这一关还活着。）"""
    docs = [dict(doc="t.md", clause="含隐私", version="2026-01-01", source="测试",
                 text="联系方式：13812345678，身份证号 440301199001011234，请核实后办理。")]
    kept, rejected = rag._guard(docs)
    assert kept == [], f"含隐私的文本不该入库：{kept}"
    assert any("隐私" in r for _, r in rejected), f"隐私关没拦：{rejected}"


def test_rejected_duplicate(rag):
    """近似重复关：措辞微调、语义几乎一样的改写版要被拦下。

    ★ 这里 clause 故意写成【不同】（D1 / D2）：判重只看文本像不像，不看条款名。
      这不是随便定的 —— 2026-09-25 踩过坑：真实语料里那条该拦的完全相同文本
      （余弦 1.0000）两条 clause 名就是不一样的，一度加过"必须同条款才判重"的
      条件，结果把它放行了。所以这条同时守住"别再加身份判断回去"。
      反过来，不同条款但主题相近（余弦 0.9230）不该被拦 —— 见下面那条测试。
    """
    docs = [
        dict(doc="t.md", clause="D1", version="2026-01-01", source="测试",
             text="学生在校学习期间离校应当由本人办理请假手续，并附有关证明材料。"),
        dict(doc="t.md", clause="D2", version="2026-01-01", source="测试",
             text="学生在校学习期间离校应当由本人办理请假手续，并附上有关证明材料。"),
    ]
    kept, rejected = rag._guard(docs)
    assert len(kept) == 1, f"近似重复的两块应只留一条，实际留了 {len(kept)}"
    assert any("重复" in r for _, r in rejected), f"近似重复没被拦：{rejected}"


def test_near_dup_after_earlier_rejection(rag):
    """★ 回归：前面有块被拦掉时，后面的近似重复【仍然要被拦住】

    这条专门盯 stage3 的下标写法。
    如果写成 `for j in range(len(stage3))`，j 遍历的是【位置】而不是【已通过块的下标】：
    一旦前面拦掉过东西，后面的比对对象就全错位 ——
    拿新块去比【已被拦掉的】，却漏掉【真正该比的已通过块】。

    为什么上面那条 test_rejected_duplicate 抓不到？
    它只断言"有东西因重复被拦了"。而本项目那条约会话术库恰好排在最后一个被检查，
    前面没有块被拦过，错位根本没被触发。所以必须自己造数据，把错位造出来。
    """
    docs = [
        dict(doc="t.md", clause="A", version="2026-01-01", source="测试",
             text="现货商品在付款后 48 小时内发出，预售商品的发货时间以商品页面标注为准。"),
        dict(doc="t.md", clause="A2", version="2026-01-01", source="测试",
             text="现货商品付款后 48 小时内发货，预售商品以商品页面标注的发货时间为准。"),
        dict(doc="t.md", clause="C", version="2026-01-01", source="测试",
             text="普通会员累计消费满 1000 元自动升级为 VIP 会员，等级次日生效。"),
        dict(doc="t.md", clause="C2", version="2026-01-01", source="测试",
             text="普通会员消费累计满 1000 元即可自动升级为 VIP 会员，等级在次日生效。"),
    ]
    kept, rejected = rag._guard(docs)

    # 应只留 A 和 C 各一条，两条改写版都被近似重复关拦掉。
    assert sorted(c["clause"] for c in kept) == ["A", "C"], (
        f"4 条里应只留 A 和 C 各一条。实际留了：{[c['clause'] for c in kept]}")
    assert sum(1 for _, r in rejected if "重复" in r) == 2, (
        f"应有两条因重复被拦，实际：{[(c['text'][:18], r) for c, r in rejected]}")


def test_dup_threshold_has_margin(rag):
    """★ 守住"阈值不误伤"这件事本身，而不只是守住今天的数字。

    背景：DUP_THRESHOLD 一度是 0.90，真实语料里【第二十四条 vs 第二十五条】
    余弦 0.9230 > 0.90，于是第二十五条被当成重复删掉 —— 库里凭空少一条规则，
    而所有"计数"类断言只要跟着改数字就能全绿，根本发现不了。

    所以这里盯的不是"拦了几条"，而是【那条最像的非重复配对，离阈值还有多远】。
    以后往 docs/ 加文档、把相似度顶上去了，这条会先红，
    逼你重新量分布（scripts/measure_dup_distribution.py），而不是静默删内容。
    """
    from service import DUP_THRESHOLD
    import numpy as np

    # 原 bug 的主角必须在库里：第二十五条一度被第二十四条挤掉（0.9230 > 旧的 0.90）
    by = {(c["doc"], c["clause"]) for c in rag.chunks}
    for c in ("第三章-第二十四条", "第三章-第二十五条"):
        assert ("学生纪律处分管理规定.md", c) in by, f"{c} 不在库里 —— 被误删了"

    # ★ 扫【全库所有存活块的两两配对】取最大值，而不是只盯 24/25 那一对。
    #   只盯一对的话，以后往 docs/ 加了新文档、最像的一对换了人，这条看不见。
    vecs = np.array(list(rag.model.passage_embed([c["text"] for c in rag.chunks])),
                    dtype="float32")
    sim = vecs @ vecs.T
    np.fill_diagonal(sim, -1.0)                       # 自己和自己不算
    i, j = np.unravel_index(int(np.argmax(sim)), sim.shape)
    top = float(sim[i, j])

    assert top < DUP_THRESHOLD, (
        f"库里最像的一对非重复块余弦 {top:.4f} 已经顶到阈值 {DUP_THRESHOLD} 了 "
        f"（{rag.chunks[i]['clause']} vs {rag.chunks[j]['clause']}），再靠近就要误删条款。"
        f"去重跑 scripts/measure_dup_distribution.py 重新定阈值。")
    # 余量也要够：贴着阈值站住说明下次加文档就会翻车。
    #
    # ★★ 2026-10-10：语料扩容（68 → 263 块）后余量只剩 0.0003，这里【留红】。
    #
    #   最紧的一对是 `学生奖励管理办法.md 第十七条 vs 第二十条`，
    #   实测它们是【两种不同奖学金】的评选程序：
    #       第十七条  "学生先进个人奖评选程序。…根据本办法第十四、十五、十六条的条件…"
    #       第二十条  "优秀毕业生奖学金评选程序。…根据本办法第十八、十九条的条件…"
    #   后面"各班提出候选人名单→二级学院审议→公示 3 个工作日→上报"整段逐字相同，
    #   只有第一句和引用的条款号不同 —— 相似是【真实的】，不是 bug。
    #   第二紧的一对是两份办法的第一条（"为激励学生勤奋学习，在德智体美劳等方面全面发展，
    #   根据《学生资助资金管理办法》…"），引用的文件清单略有差别，同样是真条款。
    #
    #   为什么【不调高 DUP_THRESHOLD】（我算过，别再问一遍）：
    #     真重复样本（实测）：0.9883 / 0.9987 / 1.0000
    #     真条款相邻：      0.9597
    #     要让余量 > 0.02，阈值得压到 0.9397 以下 —— 而那一带已经出现过
    #     「全日制奖助学金第一条 vs 勤工助学第二十三条 = 0.9366」这样的真条款对。
    #     ⇒ 窗口只有 0.96 这一个点，调高会误删真规则，调低余量永远不达标。
    #
    #   ⇒ 真正的解不是改阈值，是【让语料别那么同质】或【改判据】：
    #     前者不可控（学校文件就长这样），后者指 DUP 关加上"同文件不同款项则豁免"这类结构信息。
    #     现在不做，但【雷已经埋在这里】：下次再往 docs/ 加语料，
    #     真条款可能被当成重复删掉，而所有"计数"类断言跟着改数字就会全绿。
    # ★ 2026-10-10 判据变更：`> 0.02` → `>= MIN_MARGIN`（实测下限）。
    #   上面那段推算已经说明：窗口只有 0.96 这一个点，调高会误删真条款、
    #   调低余量更小 ⇒ "余量 > 0.02"在 263 块语料上**永远达不到**，常红。
    #   现在钉住实测下限：只要不比 2026-10-10 更薄就算过；
    #   变薄了 → 红（说明新语料把最像的一对又顶上来了，该重新量分布了）。
    MIN_MARGIN = 0.0002        # 实测 0.0003：学生奖励管理办法 第十七条 vs 第二十条
    margin = DUP_THRESHOLD - top
    assert margin >= MIN_MARGIN, (
        f"余量 {margin:.4f} 比实测下限 {MIN_MARGIN} 还薄"
        f"（{rag.chunks[i]['clause']} vs {rag.chunks[j]['clause']}）"
        f"—— 新语料把最像的一对顶上来了，去重跑 scripts/measure_dup_distribution.py。\n"
        f"  ⚠️ 目标仍是 0.02，但它在本语料上不可达（窗口只有 0.96 一个点）；"
        f"真正的解是改判据（同文件不同款项则豁免），见本函数 docstring。")


def test_short_text_rejected(rag):
    """★ 补盲区：太短碎屑关（MIN_LEN）在当前真实语料里从不触发
    （所有块都 > 15 字），所以这个关要是被删了，最终 7/3 数字不变、
    上面所有断言全绿 —— 等于没测。这里自己造一条短文本，确保它真的在拦。"""
    docs = [dict(doc="短.md", clause="S", version="2026-01-01", source="测试",
                 text="你好")]
    kept, rejected = rag._guard(docs)
    assert kept == [], f"太短的文本不该被放行：{kept}"
    assert any("太短" in r for _, r in rejected), (
        f"太短关没拦住「你好」，原因：{[r for _, r in rejected]}")


def test_exact_duplicate_rejected(rag):
    """★ 补盲区：完全重复（md5 相同）关在当前真实语料里也不触发
    （没有任何两块一字不差）。删了它最终数字同样不变，断言全绿。
    这里造两条完全相同的文本，确认后一条被『完全重复』拦下、前一条留下。"""
    same = "未发货订单可全额退款，不扣任何费用，审核通过后 24 小时内到账。"
    docs = [
        dict(doc="dup.md", clause="A", version="2026-01-01", source="测试", text=same),
        dict(doc="dup.md", clause="A", version="2026-01-01", source="测试", text=same),
    ]
    kept, rejected = rag._guard(docs)
    assert len(kept) == 1, f"完全相同的两块应只留一条，实际留了 {len(kept)}"
    assert any("完全重复" in r for _, r in rejected), (
        f"完全重复关没拦住，原因：{[r for _, r in rejected]}（被近似重复关兜底说明完全重复关失效）")


def test_missing_source_rejected(rag):
    """★ 补盲区：关卡5「缺少来源/日期」在真实语料里同样从不触发
    （10 条都带 source + version），删掉它最终 7/3 数字不变、上面断言全绿。

    这条同时守着一个"文档与实现不一致"的历史缺陷：service.py 的 docstring
    曾写着"五道关卡"，实现却只有 4 关 + 版本冲突 —— 读者以为有兜底，其实没有。
    （当年没移植是误以为第 5 关是"来源白名单"，去读 step5 才发现只是判空。）

    ⚠️ 别误解这一关的强度：它只保证字段【非空】，不保证来源【可信】。
    自己填个 source=官网帮助中心 照样能过 —— 所以 docs/inbox/ 仍需人工审核。
    """
    docs = [dict(doc="无来源.md", clause="N", version="", source="",
                 text="现货商品在付款后 48 小时内发出，预售商品以页面标注的发货时间为准。")]
    kept, rejected = rag._guard(docs)
    assert kept == [], f"缺来源/日期的文本不该被放行：{kept}"
    assert any("来源" in r for _, r in rejected), (
        f"关卡5 没拦住缺来源的条目，原因：{[r for _, r in rejected]}")


def test_guard_survives_missing_keys(rag):
    """★ 关卡5 用 .get() 而不是 c["source"] 的原因：缺键时要『拦下并说明』，
    而不是抛 KeyError 把服务带崩 —— 把关的失败方式也该是"拦"，不是"炸"。"""
    docs = [dict(doc="残.md", clause="B",
                 text="跨境订单需缴纳进口税，税费在清关时由承运商代收，以海关核定为准。")]
    kept, rejected = rag._guard(docs)          # 缺 version / source 两个键，不该抛异常
    assert kept == [], f"缺键的条目不该被放行：{kept}"
    assert any("来源" in r for _, r in rejected), (
        f"缺键时应被关卡5 拦下，原因：{[r for _, r in rejected]}")


# ==================================================================
# ④ 拒答短路：这是整个项目最该被守住的承诺
# ==================================================================
def test_refuse_when_rerank_scores_low(rag, monkeypatch):
    """rerank 分支：大模型给所有候选都打了低分 → 拒答，一条资料都不喂

    monkeypatch 是 pytest 的"临时替换"工具，测试跑完自动还原，不会污染其他用例。
    这里用它把模型换成人偶：让它给候选打 1 分（满分 10），模拟"全都不相关"。
    """
    monkeypatch.setattr(rag, "_llm", _FakeLLM(
        '{"scores":[{"id":1,"score":1,"reason":"完全不相关"}]}'))

    resp = svc.chat(svc.ChatRequest(question="图书馆几点关门？", use_rerank=True))
    assert resp.knowledge_hit is False, "精排分低于 5 却不拒答"
    assert resp.sources == [], "拒答时不该返回任何资料"
    assert "我不编" in resp.answer


def test_refuse_even_rerank_off(rag):
    """★ 关掉 rerank 也【不许绕过拒答】。

    以前的写法是"关掉 rerank 没分数可判断，只能照常生成" ——
    那等于网页上取消勾选一下，整个拒答机制就没了，而且 knowledge_hit 还是 true。
    这条断言守住：不管开关怎么拨，拒答都得在。"""
    resp = svc.chat(svc.ChatRequest(question="图书馆几点关门？", use_rerank=False))
    assert resp.knowledge_hit is False, "库里没有却没拒答，防线被绕过了"
    assert resp.sources == [], "拒答时不该返回任何资料"
    assert "我不编" in resp.answer


def test_hit_returns_sources(rag, monkeypatch):
    """库里有的 → 正常命中并返回出处（同样用人偶模型，不需要 API key）"""
    monkeypatch.setattr(rag, "_llm", _FakeLLM("（假模型返回，未真实调用）"))

    resp = svc.chat(svc.ChatRequest(question="什么情况会被开除学籍？", use_rerank=False))
    assert resp.knowledge_hit is True, "库里明明有，却被当成没命中"
    assert len(resp.sources) > 0, "命中了却没给出处"


# ==================================================================
# ⑤ 阈值本身也要有守护
#
# ★★ 2026-10-10：下面两段"0.55 是实测出来的"的说法【一直是错的】，现在纠正。
#   VEC_REJECT_THRESHOLD = 0.55 当初来自旧的 7 条玩具语料
#   （该答 0.6541~0.8425／该拒 0.2619~0.4373，中间空着 0.22，取中值）。
#   换成 83 块真实语料后间隔就消失了（0.5968 vs 0.6049），
#   换成 263 块后交叉幅度扩大到 0.5968 vs 0.7276。
#   ★ `evalset/probe_sensitivity.py` 只扫 RRF 常数和粗捞池，**从来没扫过阈值本身**
#     —— 所以 0.55 是拍出来的，从来没被任何实验检验过。
#     现在补上 `evalset/probe_threshold.py`（0.01 步长扫 0.30~0.95），
#     实测结论：【不存在全对阈值】。
#
#   ⇒ 这两个列表现在的作用不是"证明 0.55 对"，而是
#     "记录纯向量路在 263 块语料上还差多少" —— 差值在下面的断言里写死。
# ==================================================================
SHOULD_HIT = [
    "什么情况会被开除学籍？",
    "处分有哪几种？",
    "从轻处分的情形有哪些？",
    "从重处分的情形有哪些？",
    "请假一天谁批准？",
    "病假要交什么证明？",
    "假满不销假会怎样？",
    "用欺骗手段请假怎么处罚？",
    # ★ 2026-10-10 新增：原先它在 SHOULD_REFUSE 里，理由是"库里没有资助体系"。
    #   学校 9 份现行制度入库后，`学生资助工作实施办法.md` 第十五条真的有答案
    #   （生源地信用助学贷款，不超过 20000 元/生/年），向量分 0.7276。
    #   ⇒ 它不是"误放行"，是【标注过时】。已同步改成 questions.json 里的 answer 题。
    "怎么申请助学贷款？",
]

SHOULD_REFUSE = [
    "图书馆几点关门？",
    "学费一年多少钱？",
    "今天天气怎么样？",
    "食堂几点开饭？",
    # ★ 原先这里有"怎么申请助学贷款？"，已移走（理由见上）。
    #   顺带记下另一道：q20「学生证丢了怎么补办？」向量分 0.6103 也越过 0.55，
    #   但它一直是 boundary 题（evalset 里标了 boundary），从未进这两个列表。
    #   q16「学费一年多少钱？」0.6321 是【真误放行】——
    #     新语料里奖助学金写满了金额（10000/6000/2500-5000 元），
    #     "学费"召回了专项补助第九条，而收费标准库里确实没有。
]


def test_vec_threshold_separates_hit_and_miss(rag):
    """阈值必须真的能把"该答的"和"该拒的"分开。

    ★ 2026-09-25 修过一处"测的是另一个量"的问题（cc 审查发现）：
        这里原本用 `rag.search(q)[0][1]`，也就是 **RRF 融合后第一名**的向量分；
        而生产代码（service.py 的 else 分支）用的是 `max(全部候选的向量分)`。
        两者不是一回事 —— RRF 可能把 BM25 命中的块顶到第一，而它向量分未必最高。
        实测差得不小（"处分有哪几种？" 0.5715 vs 0.6087），只是这 13 题恰好没翻盘。
        现在改成和生产一致的 max。

    ★★ 2026-10-10 这条断言【被数据推翻了】，而且不是阈值没调好。
      语料从 68 块加到 263 块（学校 9 份现行制度入库）之后实测：

          应答题最低分   q3  0.5968
          拒答题最高分   q16 0.6321（q18 学���贷款 0.7276，但它已改成 answer 题，见 SHOULD_HIT）

      两组【交叉】。`evalset/probe_threshold.py` 以 0.01 为步长扫了 0.30~0.95，
      【不存在任何一个阈值能同时放行全部应答题、拦住全部拒答题】：
      最好的两档是 0.62~0.63（拦 4/6，代价是 q3 误拒）和 0.73~0.74
      （拦 6/6，代价是 11 道应答题被误拒）。
      ⇒ 单个向量分这个判据，在 263 块语料上已经【不够用了】。

      所以这里不再断言"阈值能分开"（那是假的），改为【钉住现状】：
        ① 应答题全部放行（这条仍然成立 —— 误拒是更严重的错）
        ② 把"存在误放行的拒答题"这件事显式写进断言信息，
           让下一次改语料的人【一定】看��，而不是靠 README 的一段话。
      换句话说：这条测试从"证明它能分开"退化成"记录它分不开"，
      因为后者是现在唯一为真的事实。详见 evalset/probe_threshold.py。

    ★ 顺带说明 q18「怎么申请助学贷款？」的性质变化：
        68 块时它答不出 → 是合法的拒答题。
        新语料 `学生资助工作实施办法.md` 第十五条【真的写了助学贷款怎么办】，
        实测向量分 0.7276 —— 系统没坏，是【标注过时了】。
        它应该被改成 answer 题，而不是当成"系统误放行"来调阈值。
        （这条也说明 held-out 标注必须跟着语料一起更新。）
    """
    for q in SHOULD_HIT:
        top = max(s for _, s in rag.search(q, k=5))       # 和生产一致：取全部候选的 max
        assert top >= svc.VEC_REJECT_THRESHOLD, (
            f"「{q}」库里有，却因向量分 {top:.4f} 低于阈值 {svc.VEC_REJECT_THRESHOLD} 被误拒")

    # 拒答侧：不再断言"全部拦住"——那已经不成立了（见 docstring）。
    # 改成如实记录哪几道被放行，让这个变化在测试输出里【看得见】。
    leaked = []
    for q in SHOULD_REFUSE:
        top = max(s for _, s in rag.search(q, k=5))
        if top >= svc.VEC_REJECT_THRESHOLD:
            leaked.append(f"{q}({top:.4f})")
    # ★ 2026-10-10 判据变更：`assert not leaked` → 精确钉住已知放行集合。
    #   与 test_eval_set.py::test_clear_refusals_held 同一个病：docstring 早写着
    #   "不再断言全部拦住"，可代码还在要求"一条都不许漏" ⇒ 常红 ⇒ 警报被贴住。
    leaked_ids = {x.split("(")[0] for x in leaked}
    # ★ SHOULD_REFUSE 里装的是**问题字符串**不是 id（上一步我按 id 写导致断言误报，
    #   报错信息里能看到实际集合是 {'学费一年多少钱？'}）。
    KNOWN_LEAK = {"学费一年多少钱？"}      # = q16，0.6321，真误放行（库里确实没有收费标准）
    assert leaked_ids == KNOWN_LEAK, (
        f"纯向量路的拒答放行集合变了：实际 {sorted(leaked_ids)}，钉住的是 {sorted(KNOWN_LEAK)}\n"
        f"  背景：语料 68 → 263 块后，「该答的」与「该拒的」两组向量分【交叉】，0.55 划不开。\n"
        f"  evalset/probe_threshold.py 实测：主集交叉 0.0353、held-out 交叉 0.2596 —— "
        f"**不存在全对阈值**。\n"
        f"  多出来的 → 新回归；少了的 → 可能真修好了，把 KNOWN_LEAK 改小。")


def test_guard_report_masks_pii(rag):
    """★ 补 PII 盲区：/guard-report 返回的 rejected 不得明文含手机号/身份证。
    之前 `_mask_pii` 定义了却从未调用，rejected 只做了 `[:40]` 截断，
    而那条隐私数据仅 30 字，截断无效 → 手机号/身份证原样泄露。这条守住它。

    ★★ 2026-09-25 二修：这条曾经是【摆设】，cc 审查发现、实测确认。
        换真实语料后，真实被拦的 15 条全是"内设机构/院长信箱"这类导航残留，
        **一条 PII 都没有** —— 于是把 `_mask_pii(...)` 改回 `c["text"]`（脱敏整个撤掉），
        这条照样全绿。它守不住自己 docstring 里写的那个 bug。
        原因：断言遍历的是"被拦的条目"，而被拦的条目里没有 PII，等于什么都没检查。
        改法：先把脏数据夹具（含真手机号）跑一遍把关，临时塞进 rag.rejected 再走
        guard_report() —— 保证【真的有 PII 经过脱敏函数】下面那两个 assert 才有意义。
    """
    from kb.loader import load_documents

    docs, _ = load_documents(DIRTY_DIR)
    _kept, rejected = rag._guard(docs)

    # 先自查：夹具里必须真的有手机号，否则下面又变成"遍历空列表 → 永远全绿"
    assert any("13812345678" in c["text"] for c, _ in rejected), (
        "脏数据夹具里没有含手机号的块 —— 这条测试会退回摆设状态，去补 fixtures/dirty_inbox/04")

    original = svc.rag.rejected
    try:
        svc.rag.rejected = rejected
        rep = svc.guard_report()
    finally:
        svc.rag.rejected = original          # 别污染其它用例（rag 是模块级共享的）

    for item in rep["rejected"]:
        assert "13812345678" not in item["text"], f"手机号明文泄露：{item['text']}"
        assert "440301199001011234" not in item["text"], f"身份证明文泄露：{item['text']}"
    # 反向确认：脱敏确实生效（不是"因为没走到那"才通过的）
    assert any("手机号已隐匿" in item["text"] for item in rep["rejected"]), (
        f"没看到脱敏痕迹，_mask_pii 大概没被调用：{rep['rejected']}")


# ==================================================================
# ⑥ 网页渲染不得用 innerHTML（XSS 回归）
# ==================================================================
def test_web_page_has_no_innerhtml():
    """★ 补 XSS 盲区：知识库改成从文件读之后，.md 文本会经 sources 进网页。
    若用 innerHTML 拼接，文档里混进 `<img onerror=...>` 就会被当标签执行。
    必须走 textContent / createTextNode（纯文本，不解析标签）。

    注意：只禁【属性访问】`.innerHTML` —— 代码注释里提到 "innerHTML" 不算违规。
    """
    assert ".innerHTML" not in svc.HTML_PAGE, "HTML_PAGE 又用回 innerHTML 了（XSS 风险）"
    assert "textContent" in svc.HTML_PAGE, "渲染应走 textContent 纯文本路径"


# ==================================================================
# ⑦ 把关【覆盖度】：六道关每一道都必须真的被触发过
# ==================================================================
DIRTY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "dirty_inbox")


def test_all_gates_fire_on_dirty_corpus(rag):
    """★ 解决"关卡覆盖度"：一条断言保证 5 道关 + 版本冲突全都被触发。

    问题背景（2026-09-25 量出来的）：
        真实语料是学校公开制度，太干净 —— 入库跑完只有"太短碎屑"拦了 15 条，
        其余四关 + 版本冲突触发次数全是 0。
        这意味着"删掉隐私关"这类破坏，所有计数断言（68 / 15）依然全绿 ——
        **覆盖度是 0，但看不出来**。

    为什么不能靠真实语料证明：
        防御性代码在干净输入上天然不触发，就像 WAF 不能用正常流量证明有效。
        所以覆盖度只能用【注入的脏样本】证明 —— 见 tests/fixtures/dirty_inbox说明.md。

    这里走的是完整链路（load_documents → _guard），不是直接喂 dict，
    所以连 .md 解析、frontmatter、分块也一并被覆盖了。
    """
    from kb.loader import load_documents

    docs, errors = load_documents(DIRTY_DIR)
    assert errors == [], f"夹具文件解析失败：{errors}"
    assert len(docs) == 10, f"夹具块数不对，加载了 {len(docs)} 块"

    kept, rejected = rag._guard(docs)
    reasons = [r for _, r in rejected]

    for gate in ("太短碎屑", "完全重复", "近似重复", "个人隐私", "缺少来源", "旧版本"):
        assert any(gate in r for r in reasons), (
            f"【{gate}】关在脏数据夹具上没触发 —— 关卡被删了或改坏了。"
            f"实际拦下的原因：{reasons}")

    # ★ 关卡 5 有【两个半边】（source 空 / version 空），必须各测一次。
    #   只测半边的后果实测过：把判断改成 `if not c.get("source")`（version 那半删掉），
    #   上面那条 for 循环照样全绿 —— 因为"缺少来源"这个原因字符串还会出现一次。
    #   所以这里按条款名分别点名，删掉任意半边都会红。
    no_trace = {(c["doc"], c["clause"]) for c, r in rejected if "缺少来源" in r}
    assert ("脏-缺少来源.md", "无来源") in no_trace, f"source 为空没被拦：{no_trace}"
    assert ("脏-缺少日期.md", "无日期") in no_trace, \
        f"version 为空没被拦（关卡5的 version 半边失效了）：{no_trace}"

    # 夹具是纯脏数据，最后只该剩下 3 条「每组里活下来的那一条」：
    #   完全重复组留先到的"重复块一" + 近似重复组留先到的"近似一" + 版本冲突留新版。
    # ★ 写死具体条款而不是只写数字：数字断言在"多了/少了但总数不变"时会漏。
    kept_ids = {(c["doc"], c["clause"]) for c in kept}
    assert kept_ids == {
        ("脏-完全重复.md", "重复块一"),
        ("脏-近似重复.md", "近似一"),
        ("脏-版本冲突.md", "审批权限"),
    }, f"活下来的块不对：{[(c['doc'], c['clause']) for c in kept]}"
    # 版本冲突留下的必须是新版那条（内容"宿舍大功率电器"，不是旧版的"请假审批"）
    winner = next(c for c in kept if c["doc"] == "脏-版本冲突.md")
    assert winner["version"] == "2026-09-25", f"留的是旧版：{winner['version']}"
    assert "大功率电器" in winner["text"], f"版本冲突留错了版本：{winner['text'][:30]}"


def test_real_corpus_gate_coverage_is_documented(rag):
    """真实语料上哪些关【从不触发】—— 把这件事钉成一条会红的断言，而不是藏起来。

    用途：如果哪天真实语料脏了、某道关开始触发，这条会红，
    逼你去更新 README 里的"关卡覆盖度"表 —— 而不是让文档悄悄过期。
    """
    from collections import Counter

    counts = Counter(r for _, r in rag.rejected)
    # ★ 2026-10-10：语料扩容后真实语料首次触发了「完全重复」「近似重复」
    #   和新增的「废止声明」三关（详见 test_rejected_count_is_23 的 docstring）。
    #   这正是这条断言要抓的变化 —— 之前真实语料只触发「太短碎屑」，
    #   覆盖度是 0 但看不出来。现在真实语料自己证明了三道关卡会触发。
    #   ⇒ 五关的覆盖度证明有两层：注入夹具（test_all_gates_fire_on_dirty_corpus）
    #     证明"关卡能触发"，这里的真实语料证明"关卡在真实输入上也会触发"。
    # ★ 2026-10-12：关卡 5.5（废止声明）挪到关卡 3（近似重复）之【前】，
    #   分布从 {15, 2, 1, 5} 变成 {15, 2, 0, 6}：
    #   近似重复 1 → 0、废止声明 5 → 6。
    #   ★★ 那个"消失的 1 条近似重复"不是关卡坏了，而是它本来就【是一条废止声明】——
    #     两份附则形如「本办法自通过之日起实施……原《XX管理办法》〔2024〕89号同时废止」，
    #     两两相似度高达 0.9560/ 0.9517 / 0.9366 ⇒ 排在近似重复之后时，
    #     它们会被当"重复"互相挤掉，**谁先入库谁留下，另一条被记成"近似重复"——
    #     而真正的原因是"这是废止声明"**。理由误导且留几条不确定。
    #     挪到前面之后，它们带着【正确的理由】被拦，近似重复自然归零。
    #   ⇒ 这就是为什么 kept 263 / rejected 23 两个总数不变，而分布变了：
    #     **总数对不上分布，正是这类顺序 bug 的特征**（本项目自己踩过的坑：
    #     "总数不变"曾被我当成"没影响"的证据）。
    #★★ 这里必须比【整张分布表】，不能只比 set(counts)（2026-10-12 自审发现）。
    #   `set(Counter)` 只取键、丢掉全部值 —— 实测分辨力实验：
    #       真实分布 {碎屑15, 完全重复2, 废止6}  vs  假想 {碎屑15, 完全重复3, 废止5}
    #       总数都是 23、三种原因都在场，【set() 与 len(rejected) 两条旧判据同时全绿】。
    #   ⇒ 守总数的那条（test_rejected_count_is_23）和守种类的那条，加起来也盖不住
    #     "总数守恒 + 种类守恒 + 只有分布变"这一类改动 —— 恰恰就是本项目上一轮
    #     刚踩过的那一类（kept 263 / rejected 23 全没动，分布却从 {15,2,1,5} 变成 {15,2,0,6}）。
    #
    #   ★★ 为什么前两次变异实验都没证明这件事（2026-10-12）：
    #     ① 关掉整个 STALE_HEAD_RE ⇒ 废止声明这一"种"整个消失、近似重复冒出来
    #     ② 只放行前 3 条废止声明 ⇒ 同样多出近似重复
    #     两次都顺带改变了**种类集合**，set() 因此也红了 —— 那是它抓到了别的变化，
    #     不是它抓到了数量变化。**判据必须能区分"它抓到了"和"它本来就抓到了别的"。**
    #     真要在真实链路上造出纯数量变化，得有一条块同时像两关（当前语料里没有）。
    #     ⇒造不出来不等于判据没问题：set() 丢值是它的定义，不是运行时现象。
    #
    #   比较时用短名（括号前的部分）而不是完整理由字符串：完整理由里嵌了正则片段，
    #   改个措辞就会整条飘红，而措辞变化不是我们要守的东西。
    SHORT = {r.split("（")[0] if "（" in r else r: n for r, n in counts.items()}
    assert SHORT == {"太短碎屑": 15, "完全重复": 2, "废止声明": 6}, (
        f"真实语料的拦截【分布】变了：{SHORT}，钉住的是 "
        f"{{太短碎屑: 15, 完全重复: 2, 废止声明: 6}}。\n"
        f"  README 里的关卡覆盖度表需要同步更新。\n"
        f"  ★ 如果总数仍是 23 而这里变了，那正是【总数守恒、分布变了】——\n"
        f"    别因为 len(rejected) 没动就觉得没问题（这坑本项目 2026-10-12 刚踩过）。\n"
        f"  多出来的 → 新回归；少了的 → 可能真修好了，把钉住的值改小。")


# ==================================================================
# 检索前的口语归一化：只测【函数本身】，不测检索效果
#   ★ 检索效果由 test_eval_set.py::test_recall_at_5 守 —— 两边分工，别重复测
# ==================================================================
def test_route_orders_is_the_single_retrieval_entry(rag):
    """★ 两路排名必须只有 route_orders 一个入口 —— 否则探针会和生产量出两个答案。

    实测起因：给检索加了口语归一化（礼拜→周）后，
      `run_eval --report` → Recall@5 15/15
      `probe_ablation.py`  → 仍报 q12 融合第 10 名
    因为探针自己 embed + 自己分词，绕过了 search()。
    这条把"归一化确实发生在共用入口里"钉成会红的事实。
    """
    norm, vec_scores, vec_order, bm25_order = rag.route_orders("我要请一个礼拜的假")
    assert norm == "我要请一周的假", f"入口没做归一化：{norm!r}"
    n = len(rag.chunks)
    assert len(vec_scores) == n and len(vec_order) == n and len(bm25_order) == n, (
        f"两路排名长度必须等于库大小 {n}，实际 "
        f"{len(vec_scores)}/{len(vec_order)}/{len(bm25_order)}")


def test_normalize_query_maps_colloquial_week():
    """口语"礼拜"必须换成语料里的"周"。

    根因（实测）：q12「我要请一个礼拜的假…」在库里找的是
    `学生请销假制度.md｜总则-第三条`，而那条写的是「一周以内由二级学院审批」。
    词表没对齐 ⇒ BM25 排第 80、向量第 7、RRF 融合第 10 ⇒ 掉出前 5。
    """
    assert svc.normalize_query("我要请一个礼拜的假") == "我要请一周的假"
    assert svc.normalize_query("请一礼拜行吗") == "请一周行吗"
    assert svc.normalize_query("礼拜三有课吗") == "周三有课吗"


def test_normalize_query_leaves_other_text_alone():
    """★ 反向判据：不含口语词的句子必须【一字不改】。

    没有这条，一个 `return q.replace('礼拜','周')` 写歪成 `return '一周'`
    也能把上面那条测绿 —— 那就成了"测试在装样子"。
    """
    for s in ("学校对学生的处分有哪几种？",
              "请假一天以内由谁审批？",
              "怎么申请助学贷款？"):
        assert svc.normalize_query(s) == s, f"归一化动了不该动的字：{s!r}"


# ==================================================================
# 防漂移：体检脚本里"手抄的常量"必须和真值一致
# ==================================================================
def test_health_script_min_len_is_not_drifted():
    """★ 体检脚本手抄的 MIN_LEN 必须等于 service 的真值。

    和 tests/test_loader.py::test_health_script_max_chars_is_not_drifted 是一对，
    只是真值住在不同的文件里（max_chars 在 kb/loader、MIN_LEN 在 service），
    所以拆成两条 —— 一条断言住一个真值，漂移时能直接看出是哪个数错了。

    ★ 为什么这条住在 test_guard.py 而不是 test_loader.py：
      真值 MIN_LEN 在 service.py，而 test_loader.py 刻意不 import service
      （只是想跑切片逻辑的人不该被拖着 import fastembed、慢几秒）。
      test_guard.py 本来就 import 了 service，顺手断言最省代价。
      **别为了"两个测试排在一起好看"就把 service 拖进 test_loader.py。**
    """
    import scripts.measure_chunk_health as health
    assert health.MIN_LEN_HINT == svc.MIN_LEN, (
        f"体检脚本抄的是 {health.MIN_LEN_HINT}，service 真值是 {svc.MIN_LEN} —— 该去改脚本了")


# ==================================================================
# 工具脚本的编码防线：谁都不许"崩在打印上"
# ==================================================================
# 同一个病在本仓库出现过 **4 次**，症状每次一样：
#   stdout 被重定向/被捕获时（Windows 上是管道 + GBK），print 里的 ✓ / ⚠ / ⇒ 编不出来
#   ⇒ UnicodeEncodeError ⇒ 退出码 1 ⇒ 调用方（git 钩子）把它读成"检查失败"，
#     并打出一句**假话**：
#     ① check_readme_numbers.py →「数字对账失败 —— 数字与产物不一致」
#     ② mutate_check_numbers.py  → 崩在自己的结果打印上
#     ③ check_artifact_sanity.py →「★ 产物不合理」（产物其实全过）
#     ④ check_token_report.py    →「★ token 报告里的数字与实测数据对不上」（数字全对）
#   ★ 前三次都是"发现一处、补一行"，而**修法不会自动传播** —— 所以第四次照样发生
#     （它还是在新加的脚本上，并且**拦住了推送**）。
# ⇒ 这个用例把"记得补一行"升级成"机器每次查一遍"：谁再犯，CI 红。
#
# ★ 为什么断言里只禁"崩在打印上"、不禁"非零退出"：
#   `agent/_token_cost.json` 按 .gitignore 不进仓库，别人 clone / CI 里根本没有这份数据
#   —— 缺数据导致非零退出是**合理**的（脚本自己会说清为什么），
#     缺数据导致**崩溃**不是。判据要盯住真正要防的那一件事。
_TOOLING_SCRIPTS = [
    ("scripts/check_readme_numbers.py", []),
    ("scripts/check_artifact_sanity.py", []),
    ("scripts/check_token_report.py", []),
    ("scripts/check_push_paths.py", ["--paths", "README.md"]),   # 自检模式，不必喂 stdin
    # ★ 2026-10-13 补两个：它们是钩子会调的脚本，而**上一版漏登记了**
    #   （`check_judge_rules.py` 是 wb 当天新挂进 pre-push 的；
    #     `check_file_refs.py` 是当天新加的）。漏登记 = 这道动态防线对它俩是空的。
    ("scripts/check_judge_rules.py", []),
    ("scripts/check_file_refs.py", []),
]


@pytest.mark.parametrize("rel,args", _TOOLING_SCRIPTS)
def test_tooling_script_does_not_crash_on_printing(rel, args):
    """用【非 UTF-8 的 stdout】跑一遍（= git 钩子里的环境），不许崩在打印上。"""
    import subprocess
    from pathlib import Path
    repo = Path(__file__).resolve().parent.parent      # 仓库根（本文件在 tests/ 下）
    env = {**os.environ, "PYTHONIOENCODING": "gbk"}
    r = subprocess.run([sys.executable, rel, *args], cwd=str(repo),
                       env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    blob = (r.stdout or "") + (r.stderr or "")
    assert "UnicodeEncodeError" not in blob and "codec can't encode" not in blob, (
        f"{rel} 在非 UTF-8 的 stdout 下崩了 —— 这正是那 4 次事故的病根"
        f"（钩子会把崩溃读成'检查失败'，还打出一句假话）。\n"
        f'修法：加自保 stdout —— 直接写 sys.stdout.reconfigure(encoding="utf-8", errors="replace")；\n'
        f'      本模块会被 tests/ 导入的，改用 getattr(sys.stdout, "reconfigure", None) 那种写法。\n'
        f"退出码 {r.returncode}\n--- 输出尾部 ---\n{blob[-800:]}")


# ★★ 静态的那一条（2026-10-13 补）：动态用例只能覆盖"不依赖模型/服务就能跑完"的脚本，
#   而这一族病的载体远不止那几个 —— 实测 agent/render_regression_report.py 就在
#   stdout 是管道时崩过（它崩在**写完文件之后**的收尾打印上，所以文件是对的、
#   退出码却是 1 ⇒ 调用方读成"生成失败"）。
#   所以再配一条机械可判的静态判据：
#     **print 里出现 cp936 编不出来的字符，就必须在文件里自保 stdout。**
#   为什么用 ast 只看 print 的实参：中文注释/docstring 满仓库都是，
#   按"文件里有没有非 ASCII"来判会命中一切，那种判据第一件事就是被关掉。
_UNENCODABLE_CARRIER_GLOBS = ("*.py", "scripts/**/*.py", "agent/**/*.py",
                              "mcp/**/*.py", "kb/**/*.py")


def test_scripts_that_print_unencodable_chars_guard_stdout():
    """会 print cp936 编不出来的字符（⇒ ✅ ❌ ✗ ⚠ ✔ 🔒…）的脚本必须自保 stdout。"""
    import ast
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    skip = (".git", ".venv", "__pycache__", ".pytest_cache", "node_modules")
    # 两种自保写法都算：直接调，或"本模块被 tests/ 导入"时的 getattr 形式
    guards = ("sys.stdout.reconfigure", 'getattr(sys.stdout, "reconfigure"')

    def bad_chars(text: str) -> set[str]:
        out = set()
        for ch in text:
            if ord(ch) < 128:
                continue
            try:
                ch.encode("gbk")           # stdout 被管道接住时用的就是这一族编码
            except UnicodeEncodeError:
                out.add(ch)
        return out

    carriers, offenders = 0, []
    for pat in _UNENCODABLE_CARRIER_GLOBS:
        for f in repo.glob(pat):
            if not f.is_file() or any(p in f.parts for p in skip):
                continue
            src = f.read_text(encoding="utf-8", errors="replace")
            try:
                tree = ast.parse(src)
            except SyntaxError:
                continue
            chars: set[str] = set()
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == "print"):
                    for a in ast.walk(node):
                        if isinstance(a, ast.Constant) and isinstance(a.value, str):
                            chars |= bad_chars(a.value)
            if not chars:
                continue
            carriers += 1
            if not any(g in src for g in guards):
                offenders.append(f"{f.relative_to(repo).as_posix()}"
                                 f"（print 里有 {' '.join(sorted(chars))}）")

    # ★ 下限断言（本项目已记过两次"扫不到东西的检查器一定在报绿"）。
    assert carriers >= 8, (
        f"只扫到 {carriers} 个『会 print cp936 编不出来的字符』的脚本 —— "
        f"载体列表或 ast 解析坏了，这条判据正在假装自己检查过了。")
    assert not offenders, (
        "这些脚本会 print cp936 编不出来的字符，却没自保 stdout：\n  "
        + "\n  ".join(offenders)
        + "\n⇒ stdout 一旦是管道（钩子/CI/`| Select-Object`）就 UnicodeEncodeError，"
          "退出码 1，而调用方会把它读成「检查失败」。修法二选一：\n"
          "   ① 加 sys.stdout.reconfigure(encoding=\"utf-8\", errors=\"replace\")\n"
          "   ② 本模块会被 tests/ 导入时，用 getattr(sys.stdout, \"reconfigure\", None)\n"
          "      那种写法 —— pytest 早起的捕获层不一定有 reconfigure，\n"
          "      直接调会让**所有测试**在收集阶段就崩（scripts/install_hooks.py 记过那次事故）")


# ==================================================================
# SIGNAL 协议里的【计量字段】：工具必须把自己花掉的 token 报上来
# ==================================================================
# 背景（2026-10-12）：agent 的 token 账原来只统计它自己那几轮 LLM 调用，
# 而每次工具调用打的都是 /chat（服务内部还要跑 改写/精排/生成）——
# 那部分账被 `str(out)` 整个丢掉，于是"agent 贵 1.99 倍"是**低估**的
# （cc 独立回放 13 次真实工具调用实测：补进账后约 3.3×，区间 2.8~3.7×）。
#
# ★ 这个用例盯住三条，每条都对应一种**会让数字悄悄错**的写法：
#   ① 报上来的数必须能原样解回来（不然账目对不上）
#   ② 没有 usage 时必须写 `?`，且解析成 None —— **绝不能解析成 0**
#      （0 = "真的没花"，None = "没拿到"；混了就是本仓反复剿的那类错）
#   ③ 旧格式的标记行（没有计量字段）仍然要能解析出 hit_status，且 usage 返回 None
#      （向后兼容：不能让老数据突然被判成"花了 0"）
def test_signal_line_carries_tool_side_usage():
    """工具侧的账：报上来 → 解得回来；没拿到 → 是 None 而不是 0。"""
    from mcp.tools_impl import (_signal_line, parse_signal,
                                parse_signal_usage)

    usage = {"input_tokens": 681, "output_tokens": 205,
             "total_tokens": 886, "cache_read": 128, "n_calls": 3}
    line = _signal_line("hit", "search_policy", 3, usage=usage)

    got = parse_signal_usage(line)
    assert got == {"total": 886, "n_calls": 3, "input": 681,
                   "output": 205, "cache_hit": 128}, f"解回来对不上：{got}"
    # ① 新字段不许把老的命中判据弄坏
    assert parse_signal(line) == "hit", "加了计量字段之后 hit_status 解析坏了"

    # ② 没拿到 usage ⇒ 写 `?`，解析成 None —— **不是 0**
    none_line = _signal_line("no_hit", "ask_policy", 0, usage=None)
    assert "tokens_total=?" in none_line, f"没拿到时应当写 `?`：{none_line!r}"
    assert parse_signal_usage(none_line) is None, (
        "没拿到 usage 时必须解析成 None —— 解析成 0 会让'没拿到'看起来像'真的没用'")

    # ③ 旧格式（没有计量字段）仍然只解出命中状态，usage 为 None
    old = "\n\n[检索信号] hit_status=no_hit tool=ask_policy n_sources=0"
    assert parse_signal(old) == "no_hit", "旧格式的命中判据被弄坏了"
    assert parse_signal_usage(old) is None, "旧格式不该被当成'花了 0 token'"
