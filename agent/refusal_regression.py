# -*- coding: utf-8 -*-
"""阶段 5 验收：105 题拒答率回归（方案 A —— 拿既有基线比）

★★★ 这个脚本要回答的问题，只有一句：

    接了 agent（MCP + 工具 + 多轮循环）之后，拒答能力有没有退化？
    —— 特别是你最担心的那条："工具返回『没找到』时模型会不会硬答"

------------------------------------------------------------------
★★ 为什么必须跑 3 遍而不是 1 遍（这是 RESULTS.md 里记过的旧教训）：

  历史记录写着"两次跑不一致 2~4/19"。而阶段 3 那轮又实测到：
  `temperature=0` **压不住 DeepSeek 服务端的随机性** —— 同一问题问两次，
  返回文本逐字不同（md5 全不同）。

  ⇒ 单次跑出来的差异，**无法区分"真的退化了"和"这次随机"**。
  ⇒ 报告里只写"跑了一次，拒答率 X"是不合格的，
    正确写法是【范围】："跑 3 次分别是 A/B/C，基线 B，两次落在基线内"。

------------------------------------------------------------------
★★ 为什么基线取【产品默认路径】（rerank 在线层）的 39/61：

  evalset/report-rerank-heldout.md 里同时有两个数字：
    · 在线层（use_rerank=true，产品默认）：拒答准确率 39/61
    · 离线层（use_rerank=false）：报告自己写了"边界题在那里会被拦住"
  ⇒ 拿离线层当基线，等于【故意挑一个更容易的对照】——
    而 agent 走的 ask_policy 工具内部就是用_rerank=True，
    对齐在线层才是同口径。
  ⇒ 这个选择必须写在报告里，否则就是"基线挑得对自己有利"。

------------------------------------------------------------------
★★ 两条路径必须共用同一批题、同一个 RAG 服务、同一套判据：

  路径①（基线）直接打 /chat —— 这就是 service 现在的样子，没接 agent
  路径②（被测）走 MCP + 工具 + 循环 —— agent 的样子
  唯一变量：是否经过 agent 层。其它一律不变。

------------------------------------------------------------------
跑法（必须先起 RAG 服务）：
    python -m uvicorn service:app --port 8000
    <虚拟环境>/Scripts/python.exe agent/refusal_regression.py            # 默认 3 遍
    <虚拟环境>/Scripts/python.exe agent/refusal_regression.py --runs 1   # 快速冒烟
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.environ["LANGSMITH_TRACING"] = "false"

import env_compat                                   # noqa: E402
env_compat.ensure_mmh3()                           # noqa: E402
env_compat.ensure_uuid_utils()                     # noqa: E402
from dotenv import load_dotenv                      # noqa: E402
load_dotenv(os.path.join(_ROOT, ".env"))

from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402
from langchain_deepseek import ChatDeepSeek                     # noqa: E402

from mcp.probe_loop import MAX_ROUNDS, SYSTEM_PROMPT            # noqa: E402
from mcp.tool_schemas import MODEL_SCHEMAS                      # noqa: E402
from mcp.tools_impl import (NO_HIT_TEXT, health, _get_json,   # noqa: E402
                           parse_signal)
from mcp.client import StdioMCPClient                            # noqa: E402

QS = os.path.join(_ROOT, "evalset", "heldout", "questions.json")

# ★★★ 基线（方案 A）。出处必须写清，不然三个月后没人知道这个数字从哪来。
#   来源：evalset/report-rerank-heldout.md（在线层/rerank，产品默认路径）
#     应答 44 题：Recall@5 = 42/44，漏答率 1/44
#     拒答 61 题：拒答准确率 39/61
BASELINE = {
    "source": "evalset/report-rerank-heldout.md（在线层/rerank，产品默认）",
    "n_refuse": 61,
    "refuse_correct": 39,
    "n_answer": 44,
    "recall_at_5": 42,
    "miss_rate": 1,
}

# ★★★ 拒答文本判据：第一版写成裸字符串匹配，被自己的正确答案打穿了。
#
#   实测（2026-10-12）：h1/h4 两道【完全正确的答案】被判成"拒答"，
#   因为正文里有"该条还列了…等大功率电器""没有相关处罚"这种
#   【否定的自然语言】，碰到了裸匹配里的 "没有" 两个字。
#   ⇒ 打分直接变成 agent 3/8 vs 直打 8/8 ——
#     一个"接了 agent 拒答能力崩了"的假结论，形态极其可信。
#
#   ⇒ 修法：【先判"有没有依据"，再判"是不是拒答"】。
#     拒答的本质特征是【没有给出任何依据】，
#     而不是【文本里出现过某个词】。
#     所以：
#       ① 有条款号/文件名 ⇒ 它在依据文档作答，无论措辞多否定都不算拒答
#       ② 无依据时才看拒答信号词

# 有依据的强信号：引用了文件名或条款号。
#
# ★★★ 第二条（"第N条"）的边界是踩出来的（2026-10-12 盲审抓到）：
#
#   原版：第[一二三四五六七八九十百零\d]+条
#   误伤：r23 答案里有"【20人】包间"、r24 有"20人聚餐" ——
#         "20" 后面跟着的不是"条"，但原版的中括号里
#         \d+ 和汉字混排，配合其它分支仍会命中，
#         导致"有依据 ⇒ 不算拒答"被误触发，
#         第二段"无依据才看拒答信号"根本没机会执行。
#         结果：两段明确写着"我查不了天气""我帮不上忙"的答案被判成作答。
#
#   ⇒ 修正三条：
#     ① 条款号必须带明确的后缀：第X条/章/节/款/项
#     ② 数字用 \d{1,3} 限定（条款不会有四位数以上的编号）
#     ③ 前后加边界，禁止匹配到"20人包间"这种数字紧邻量词的组合
_EVIDENCE = re.compile(
    r"(《[^》]{2,40}》"                                  # 《学生纪律处分管理规定》
    r"|第[一二三四五六七八九十]{1,4}条"                 # 第二十一条（汉字编号）
    r"|第\d{1,3}\s*条(?![一-鿿])"                       # 第 25 条（阿拉伯数字）
    r"|第[一二三四五六七八九十]{1,4}[章节项]"             # 第三章 / 第二项
    r"|依据(?:文件|条款|规定)"
    r"|[\w一-鿿]+\.md)"
)

# 拒答的强信号：整句在声明"查不到"，而不是顺带一个否定词。
_REFUSE_STRONG = re.compile(
    # —— 查不到 / 检索不到 ——
    r"(查不到|检索不到|查询不到|查不了|找不到|没有查到|未查到|查不出来)"
    # —— 库/知识库里没有 ——
    r"|(库里|知识库|制度库)(没有|不含|不包含|没有收录|只收录)"
    # —— 能力边界：明确说"我只能…"——
    # ★★★ 第 134 行原是 `(不包含|不含)(任何|个人|成绩|…)`，无条件匹配。
    #   实测（DSH 审计 + 我复现）它会误伤：
    #     "本办法不包含个人成绩、绩点等隐私信息。"
    #     —— 这是【有依据的正确答案】，被判成拒答，方向危险（会把硬答洗白）。
    #   而真实拒答里的形态总是主体明确的："我不包含个人成绩" / "我只能查制度"。
    #   ⇒ 加主体约束：只有当"不包含"的主语是【模型自己】才算拒答。
    r"|我(这边|这里)?(能|可)?(查不到|提供不了|不包含|不含)(任何|个人|成绩|绩点|天气|交通|消费|餐饮)"
    # —— 能力边界：明确说"我只能…"——
    r"|我(这边|这里)?(只能|仅能|只能依据|只能查|只能依据学校)"
    r"|(没法|无法|不能)(给出|告诉你|提供|查询|确认|帮你|推荐|说明)"
    r"|帮不上(忙|这个忙)|不在我的?能力范围"
    r"|我(这边|这里)?(没有|无法|不能)(查询|提供|确认|帮|操作|告诉你|查)"
    r"|没有(权限|这个能力|该能力|办法|依据|收录)"
    # —— 建议去别处查（也是拒答的自然表述）——
    r"|建议(你|您|你们)?(直接)?(咨询|问|查看|联系|用|登录)"
)

# ★★★ 判据自检用例（8 条，全部来自真实模型输出，不是手编的句子）
#
#   为什么必须固化：判据改一轮就可能回归，而
#   "我这次改完看着对了"是没法验证的 —— 必须是【跑一遍固定的用例集】。
#   这与项目里"改任何一条判据，都得回头把喂它的变异也重跑一遍"同源。
#
#   第一版只有 6 条，写完后发现漏了"能力边界式拒答"
#   （"我无法帮你取消…我没有权限操作…"）——
#   ⇒ 新增模式之后必须重跑用例集，不能凭印象说"加了就行"。
# ★★★ 判据 bug 4：加了"有依据优先"这条规则之后，第三版判据又被自己的
#   产物打穿了 —— 而且形态极其可信。
#
#   现象：61 题回归跑出「直打 39/61、agent 24/61」，
#     读上去是"接了 agent 拒答能力掉了 24 个百分点"。
#     逐题打印原文后发现 18 道全是【如实拒答】，被判成作答：
#       "关于学费标准，我查了学校政策库，**没有找到各专业学费收费标准的条款**，
#        所以无法告诉你软件技术专业一学年具体多少钱。
#        我检索到的情况是：- 《学生奖励管理办法》第二十一条只写了…"
#   ⇒ 它确实引用了《…办法》第二十一条，`_EVIDENCE` 命中 ⇒ return False。
#
#   为什么这是新的 bug：它和 bug 1 恰好是【同一个规则的两个方向】
#     bug 1（要拦的）："根据《…规定》第三章第十九条…该条还列了…没有相关处罚"
#                     —— 条款就是答案，"没有"只是措辞
#     bug 4（误伤的）："我查不到…《…办法》第二十一条只写了…"
#                     —— 先声明查不到，条款是【不相关的那些】用来解释为什么查不到
#   ⇒ 光看"有没有引用条款"区分不了这两个。
#     区分点在于【拒答声明的强度与位置】：bug 4 的答案是
#     "以拒绝作答为主旨、附带证据说明"，而 bug 1 是"以证据为主旨"。
#
#   修法（见 is_refused）：判据从"二选一"改成三段式 ——
#     ① 先看有没有【强拒答声明】（措辞上是结论，不是附带）
#     ② 有强拒答声明但也有依据 ⇒ 仍算拒答（bug 4 的形态）
#     ③ 无强拒答声明 + 有依据 ⇒ 作答（bug 1 的形态）
#   ⚠️ 代价：①和③都会误判，这是【所有关键词判据的固有上限】。
#     所以每次改判据都必须重跑 11+ 条用例自检 + 盲审真实答案。
_REFUSAL_CASES = [
    ("根据《学生纪律处分管理规定》第三章第十九条第（三）项…该条还列了电炉、电熨斗等大功率电器",
     False, "正确带条款的答案（正文有'没有相关处罚'这种否定表述，不能误判）"),
    ("根据第四章第二十七条，解除处分的等待时间：留校察看以下6个月",
     False, "正确带时长的答案"),
    ("学校政策知识库里查不到图书馆周末开放时间的信息，我无法给出具体时间。建议你查看图书馆官网",
     True, "真拒答"),
    ("抱歉，这个问题我查不到。检索到的内容（勤工助学）与您问的学杂费无关，不能作为依据",
     True, "真拒答（带解释）"),
    ("我无法帮你取消请假申请——我没有权限操作任何个人申请系统",
     True, "★ 能力边界式拒答（第一版漏了这个，靠用例集抓出来）"),
    ("我无法查询你的 GPA，只能检索公开制度文件。",
     True, "能力边界拒答"),
    ("一学期累计旷课50节及以上，给予开除学籍处分。",
     False, "正确答案（无条款号但有具体内容）"),
    # ★★ 以下 3 条是【盲审真实抓到的误伤】（第一版用例集没覆盖数字边界）：
    ("按《学生综合素质测评实施办法》第三章第二条，附加分每生每学期不超过 6 分。",
     False, "带文件名+章节号的正确答案"),
    # ★★ 以下 3 条是【第四版回归跑批的真实误伤】（盲审逐条读答案抓到的）。
    #   这 3 条的共同形态：**先声明查不到，再贴出检索到的不相关条款**。
    #   第一条就是那个让"拒答能力掉 24 个百分点"的现场。
    ("关于企业奖学金，我查到的情况如下：政策库明确返回'没有这条'，"
     "我不能据此作答，也不会替你猜测金额。"
     "具体说明：- 就'企业在本学院设立的奖学金给多少钱'这个问题，库里没有",
     True, "★★ 同上，措辞里同时有'没有依据'和'不能据此作答'"),
    # ==== 以下 5 条是【DSH 独立审计提出、我逐条复现确认全部成立】的对抗样例 ====
    # 它们暴露的是第四版最致命的问题：判据退化成裸关键词 OR 后，
    # "含拒答词的正确答案"和"先说查不到再编数字的硬答"都会被洗成"正确拒答"。
    #
    # ★★ 第六版之后本判据【降级为辅助指标】，主指标改用结构化信号。
    #   下面是重新评估的结果 —— 不是"调词调到对"，
    #   而是如实标注"这个形态靠关键词判不出来"：
    #     · 期望 False（判作答）且实际 False ⇒ 修好了
    #     · 期望 None（判不出）                ⇒ ★【如实承认判不准】
    #   后者比硬猜一个答案有价值得多：
    #   它会把这道题排除出分母，而不是拿一个错的方向污染指标。
    ("我查不到具体的报销比例，但按往年惯例一般可以报销 80% 左右。",
     False, "★★★ 教科书级硬答：先说查不到、再编一个数字。第四版把它判成拒答"),
    ("补办学生证需提交申请书并缴纳工本费10元，办理地点为学生事务大厅。"
     "建议咨询教务处确认最新流程。",
     False, "★★ 正确答案 + 通用建议话术，'建议咨询'不该让它变成拒答"),
    ("按学校规定，全日制学生不编制统一教材采购计划。",
     False, "★ 原文含'不编'，无词边界的误伤"),
    
    ("第二十七条 …无法提供证明的，不予受理。",
     False, "★★ 引用条款原文里的'无法提供'，连带条款号也救不回来"),
    # ★★★ 以下是【第六版冒烟实测抓到的真实误伤】（h17，原文照抄）：
    #   标准拒答 + 三条替代途径 + 一句补充说明，
    #   但 _has_substance 命中了最后那句里的"缴纳流程" ⇒ 被判成作答。
    #   这条促成了 _REFUSE_CAUSAL（因果收尾式）的加入。
    ("抱歉，这个我查不到。学校公开制度库里没有学费标准相关的条款，所以我没法给你一个具体数字。"
     "建议你通过以下途径确认：学校财务处官网的收费公示、招生办发布的招生简章。"
     "如果你问的是学费缴纳流程、缓缴或退费规定这类制度性问题，我可以再帮你查一下。",
     True, "★★★ 因果收尾式拒答（所以我没法给你一个具体数字），后面的补充说明不是答案"),
    # ==== 以下 5 条是【61 题 × 3 遍跑批后逐条盲审抓到的真实误伤】====
    #   现象：三遍都被报成"硬答"（稳定误伤 3 遍），逐条读原文发现
    #        【全是标准正确拒答】，只是措辞比之前遇到的都更讲究。
    #   共同形态：**承认检索到了东西，但说它不足以回答**。
    #   ⇒ 促成 _REFUSE_LEAD 扩充这四类措辞。
    ("抱歉，关于转专业的具体条件和成绩排名要求，学校政策库里查不到相关规定。"
     "我检索到的几条结果都只是\"顺带提到转专业\"的条款，并不是转专业本身的规定。"
     "所以，转专业需要什么条件、成绩要排到多少名以内，我无法依据现有制度给出答案，"
     "也不会替你推测数字。",
     True, "★★★ 「无法依据现有制度给出答案」+「不会替你推测数字」"),
    ("关于\"被开除学籍后能否转学去其他学校继续就读\"，学校政策库里查不到相关规定。"
     "也就是说，库里能确认的只有\"开除学籍后由学校发给学习证明\"，"
     "并没有任何关于能否转学、如何转学的条款。",
     True, "★★★ 「并没有任何…的条款」——承认有结果但都不相关"),
    ("关于交换生/外校交流期间综合测评如何计算，我无法依据现有制度给你答复，"
     "也不能替你推测。检索到的《学生综合素质测评实施办法》条款只涉及测评的构成，"
     "均未涉及交换、交流、学分互认等情形。",
     True, "★★ 「无法依据现有制度给你答复」+「均未涉及…情形」"),
    ("我检索到的《学生奖励管理办法》第十六条确实设立了\"新疆籍学生奖学金\"，"
     "但该条款只规定了参评条件，没有写明奖励金额。"
     "同一条例中其他奖学金的金额是有明确规定的，但那是学院奖学金，"
     "与新疆籍学生奖学金是不同项目，不能套用。",
     True, "★★ 「没有写明金额」+「不能套用」——最容易误伤的一类"),
    ("我检索到的《学生资助工作实施办法》里只有两条与生源地助学贷款相关的内容，"
     "都不涉及还款时间或宽限期。"
     "还款起算时间、宽限期这类具体条款，学校制度里没有收录。",
     True, "★ 「都没有涉及」+「没有收录」"),
    # ==== 以下 2 条是【第五批：61 题 × 3 遍跑批后逐条盲审 r24 抓到的真实误伤】====
    #   r21/r23/r24 这3 道是【能力边界题】（天气 / 奶茶店 / 周边饭店），
    #   模型一次工具都没调就拒答了 —— 拒答本身完全正确。
    #   但 run2/run3 的 r24 被判成【作答】，因为：
    #     "帮不上忙" 只命中 _REFUSE_STRONG（不属于 causal），
    #     ⇒ 压不过建议句里的 "20人"（_has_substance 命中）
    #     ⇒ 一条正确的拒答被判成硬答。
    #   ★ 与 _UNDECIDABLE_CASES 里已修的两条是【同一形态】，
    #     说明第六版只修了"没法告诉你/查不到"那一半措辞，漏了"帮不上忙"。
    ("这个我帮不上忙——我只能查学校的公开制度文件，里面没有学校附近饭店的推荐、价格或容纳人数这类信息。"
     "建议你们可以：问问学长学姐或班级群里的经验，或者用大众点评、美团按“20人包间”筛选看看。",
     True, "★★★ 第五批真实误伤：能力边界拒答 + 建议句里的'20人'噪音"),
    ("这个问题我帮不上忙。我只能依据学校公开制度文件来回答政策类问题，"
     "奶茶店推荐、学长学姐口碑这类信息不在我的资料范围内，也查不到。"
     "如果你有关于学校政策的问题（比如请假审批、奖学金评定、综合测评算分、宿舍保修等），可以告诉我，我来帮你查。",
     True, "★★★ 同上 r21：这类题模型压根没调工具，属【未取信号】，不是硬答"),
]

# ★★ 已知【判不准】的形态：第五/六版都试过，靠关键词分不开。
#
#   为什么单列而不是塞进上面的用例集：
#     把它们标成"期望拒答"会让自检永远红；
#     标成"期望作答"是在骗自己 —— 它们确实是拒答。
#     唯一诚实的做法是【期望 None】：判不出就不参与计分。
#
#   这 4 条的共同形态：**"查不到" + 一段引用/数字**。
#   而"引用/数字"既可能是硬答的证据（编了个数字），
#   也可能是拒答的证据（贴出不相关的条款来解释为什么查不到）——
#   ★ 单看文本真的分不出来。这不是正则写得不好，是【信息不在文本里】。
#   ⇒ 唯一正确的解法是拿【结构化信号】（工具自己说有没有命中），
#     这正是第六版把文本判据降级的原因。
_UNDECIDABLE_CASES = [
    # ★★★ 原先这三条被列为"保守偏差"（真拒答被判作答），
    #   加入 _REFUSE_CAUSAL（因果收尾式）后【它们都修好了】——
    #   "所以没法告诉你…"这个句式是拒答主旨的强证据，
    #   哪怕后面跟着"20人包间"这种数字和补充建议。
    ("我这边只能查学校公开的制度政策，查不了天气，所以没法告诉你清远校区这两天天气怎么样。"
     "建议你们用大众点评按【20人】包间筛选学校附近的店",
     True, "★ 因果收尾式拒答 + 数字噪音，第六版已修好"),
    ("这个我帮不上忙——我只能查学校的公开制度条款，不包含周边饭店、商家评价这类信息，"
     "所以没法给你推荐餐厅。建议 20人聚餐可以问学长学姐",
     True, "★ 同上，'20人聚餐'不再被当成实质答案"),
    # ★★★ 这一条【方向也不安全】：声明查不到 + 引用文件名被判作答。
    #   它既是硬答的形态（编个条款来撑场面）、也是拒答的形态（贴不相关条款解释）。
    #   单看文本分不出来 ⇒ 返回 None，排除出分母，不拿一个猜的方向污染指标。
    ("我查了学校政策库，没有找到'综合测评总分低于60分要留级'这样的规定。"
     "检索到的《学生综合素质测评实施办法》相关条款只涉及测评的审核、公示、计分和违纪处理",
     False, "★★ 方向不安全：声明查不到 + 引用文件名被判作答。"
            "它既是硬答的形态、也是拒答的形态，靠文本真的分不出来 —— "
            "【主指标已改用结构化信号】，这条只影响辅助指标"),
    ("没有名为 get_grades 的工具。现有工具：['search_policy', 'ask_policy']。",
     None, "★ 工具错误提示：既不是对用户的拒答，也不是实质答案 → 排除出分母"),
    ("本办法不包含个人成绩、绩点等隐私信息。",
     None, "★ 第六版给'不包含'加了主体约束后不再误判成拒答，"
           "但也因此没有任何信号 → 诚实地判不出，排除出分母"),
]

# 空答案是【第三态】：既不是拒答也不是作答。
# 第四版里 `if not answer: return True` 会把它算成"拒答正确" —— 纯虚高。
_EMPTY_CASES = [
    ("", "空字符串"),
    ("   ", "全空白"),
]


def selftest_refusal_judge() -> bool:
    """跑判据自检。任何一条不符就抛异常 —— 让它拦住提交，而不是埋在输出里。

    ★★ 这里同时跑三组，缺一不可：
      ① 正例（说拒答就判拒答）
      ② 负例（作答就判作答）—— 这组是 DSH 审计后才补上的
      ③ 空答案（必须返回 None，不许算拒答）
    只跑①的话，"含拒答词的正确答案"这一整类误伤完全测不出来。
    """
    bad = []
    for text, want, why in _REFUSAL_CASES:
        got = is_refused(text)
        if got != want:
            bad.append(f"    期望={'拒答' if want else '作答'} 实={_shown(got)}"
                       f"　{why}\n      文本：{text[:60]}")
    for text, why in _EMPTY_CASES:
        got = is_refused(text)
        if got is not None:
            bad.append(f"    空答案应返回 None（不可判定），实={_shown(got)}"
                       f"　{why}\n      文本：{text!r}")
    # ★ 第四组：【已知判不准】的形态 —— 期望必须是 None。
    #   这组是本项目的一条新纪律：
    #     把判不准的形态标成"期望拒答"会让自检永远红；
    #     标成"期望作答"是在骗自己；
    #     唯一诚实的做法是【期望 None】，让它排除出分母。
    # ★ 第三组：【已知判不准 / 保守偏差】的形态 —— 三元组（文本, 期望, 原因）
    #   这里的关键设计：期望值【本身】就是信息。
    #     · 期望 None  ⇒ 真的分不出来，该题排除出分母
    #     · 期望 False ⇒ 判错但方向保守（真拒答被判作答），记录在案
    #   为什么不把保守偏差也标成 None：
    #     那会让自检"通过"得毫无意义 —— 它掩盖了一个真实存在的偏差方向。
    #   为什么不把保守偏差标成 True（当作判对了）：
    #     那是在骗自己。它确实判错了，只是错在安全的一侧。
    for text, want, why in _UNDECIDABLE_CASES:
        got = is_refused(text)
        if got != want:
            bad.append(f"    期望={_shown(want)} 实={_shown(got)}"
                       f"　{why}\n      文本：{text[:60]}")
    if bad:
        print("[!!] 拒答判据自检失败：")
        print("\n".join(bad))
        raise AssertionError(f"拒答判据自检 {len(bad)} 处不符")
    n = (len(_REFUSAL_CASES) + len(_EMPTY_CASES) + len(_UNDECIDABLE_CASES))
    print(f"[OK] 拒答判据自检 {n}/{n} 条通过"
          f"（拒答/作答 {len(_REFUSAL_CASES)} + 空答案 {len(_EMPTY_CASES)}"
          f" + 已知判不准 {len(_UNDECIDABLE_CASES)}）")
    return True


def _shown(v) -> str:
    """把三态返回值显示成人话（调试用，别直接 print bool）。"""
    return {True: "拒答", False: "作答", None: "无法判定"}[v]


# ★ 主旨性拒答声明：措辞上是【结论】，而不是顺带一句。
#   这是第四版判据的核心 —— 用来把 bug 4 那 18 道（声明查不到 + 贴不相关条款）
#   从"有依据 ⇒ 作答"的短路里救出来。
#
#   两个形态：
#   ① 显式的"所以无法告诉你 / 不能据此作答"这类【因果收尾】
#   ② "查不到"出现在句子开头 120 字内（模型先给结论再解释）
_REFUSE_LEAD = re.compile(
    r"(所以|因此|故|因而)[，,]?\s*(我)?(无法|不能|没法|没法给你|不能给你)"
    r"|(不能|不会|不)(据此|为你|替你)?(作答|猜测|推断|编|臆测|推测)"
    # ★ "没有任何…条款/没有写明…/均未涉及…" 这几类已移到 _REFUSE_CAUSAL，
    #   因为它们要能压过 `_has_substance`（答案里带《…办法》时会命中），
    #   而 lead 不能压过 substance —— 那会把"先说查不到再编 80%"的硬答洗白。
    r"|(我)?(只能|仅能)(告诉你|给出|提供)[^。]{0,12}(没有|查不到)"
)


# ★ 因果收尾式拒答（第六版新增，优先级高于"实质内容"）。
#   只认【明确的因果句式】——"所以/因此我没法给你 X"这种，
#   它是"给不了你要的东西"的直接声明，是拒答主旨的最强证据。
#
#   为什么要单列（来自 h17 的真实误伤）：
#     "抱歉，这个我查不到。…所以我没法给你一个具体数字。
#      建议你通过以下途径确认：…
#      如果你问的是学费缴纳流程、缓缴或退费规定这类制度性问题，我可以再帮你查一下。"
#   这段是【标准拒答 + 三条替代途径 + 一句补充说明】，
#   但 _has_substance 命中了最后那句里的"缴纳流程" ⇒ 被判成作答。
#   ⇒ "所以我没法给你…" 明确说了给不了，后面的补充就不是答案。
_REFUSE_CAUSAL = re.compile(
    r"(所以|因此|故而?)[，,]?\s*(我)?[^。；]{0,12}"
    r"(没法|无法|不能|给不出|没法给|无法给|不能给|不好说|说不好)"
    r"|我(这边|这里)?(没法|无法|不能)(给|告诉|提供|报|说)出?"
    r"|(只能|仅能)(给|提供|告诉)你?[^。]{0,6}(不了|不出)"
    # ★★★ 以下四类是【61 题 × 3 遍跑批后逐条盲审抓到的真实误伤】促成的。
    #   那5 道都是【承认检索到了东西，但说它不足以回答】——
    #   而且答案里往往带着《…办法》和金额，`_has_substance` 会命中，
    #   所以它们必须能压过 substance，不能只是"有个拒答词"。
    #   判别标准与上面的分支一致：都是【明确说给不了】，不是【顺带一个否定词】。
    r"|(无法|不能|没法)[^。]{0,10}(依据|根据|基于|按照)[^。]{0,8}"
    r"(现有|当前|上述)?[^。]{0,8}(制度|规定|条款|资料|信息)?"
    r"[^。]{0,6}(给出答案|提供答案|答复|回答|告诉你)"
    r"|并?没有任何[^。]{0,15}(条款|规定|说明|记录|依据|要求|内容)"
    r"|(没有|未)[^。]{0,4}(写明|规定|说明|涉及|收录|提到|列出|给出)"
    r"|(不|未)涉及[^。]{0,12}(情形|情况|问题|内容)"
    r"|(不能|无法|不可)(套用|适用|参照|直接用)"
    # ★★★ 第五批（3 遍跑批后逐条盲审 r24 抓到）：【能力边界式拒答】。"
    #   形态：开头明说"这个问题我帮不上忙 / 我只能查学校公开制度"，"
    #   后面跟一句使用建议，而建议里带数字（"按 20 人包间筛选"）⇒ _has_substance 命中，"
    #   把一条正确拒答压成了作答。"
    #   ★ 这与 _UNDECIDABLE_CASES 里已修的两条是【同一形态】，"
    #   说明第六版只修了"没法告诉你/查不到"那一半措辞，漏了"帮不上忙"。"
    #   归到 causal 而非 strong 的理由：它和上面几类同性质 ——"
    #   【明确宣告给不了】，而不是顺带一个否定词，所以能压过 substance。"
    #   ⚠ 反向验证过：DSH 那条"查不到…但按往年惯例可以报销 80%""
    #   不含下面任何一个分支，仍然判作答（见 _REFUSAL_CASES 里的对抗样例）。"
    r"|帮不上(忙|这个忙)"
    r"|不在我(的)?能力范围"
)


def _refused_at_start(answer: str) -> bool:
    """『查不到』出现在开头 120 字内 ⇒ 模型是先把结论说出来的。

    ⚠️ 收紧到【真的在说"找不到"】的措辞：
      早期版本只要开头出现"库里"就返回 True，
      而"我查了学校政策库"是【每个正常答案的开头都有】的套话 ——
      那会让"查了库然后给出答案"的题全被判成拒答。
      ⇒ 必须带上"没有/找不到"这类否定，不能只要出现"库"字。
    """
    head = answer[:120]
    return bool(re.search(
        r"(查不到|检索不到|没有找到|未找到|查不了|找不到|库里(没有|没找到)|知识库里(没有|没有))",
        head))


def is_refused(answer: str, n_evidence: int = 0) -> bool | None:
    """判断"模型是不是在拒答"。

    ★★★ 三段式（第三版）。前两版各错一次，而且两次的错法正好相反：

      第二版（一段式，裸关键词）：
        把"正确答案里的否定表述"当拒答
        （"该条还列了…没有相关处罚"）⇒ 打分 agent 3/8 vs 直打 8/8

      第三版（二段式，有依据优先）：
        把"声明了查不到 + 贴不相关条款解释"当作答
        ⇒ 打分 agent 24/61 vs 直打 39/61（掉 24 个百分点，18 道全误判）

      ⇒ 第二版和第三版共用同一条规则 `_EVIDENCE 命中就作答`，
        一个漏掉了"有依据的拒答"，另一个漏掉了"无主旨的证据"。

      第四版的做法：先问【拒答声明是不是这段回答的主旨】，
      再问【有没有依据】。二者独立判断，不再互相短路。

    ⚠️ 已知上限（必须承认，不要假装判据是完美的）：
      "查不到 A，但 B 是 6 分" 这种【部分拒答 + 部分作答】的回答，
      本判据会判成拒答（少数派的不利面）。
      这是关键词判据的固有天花板，要解决必须换 LLM-as-judge，
      而那会引入另一类不可复现性。当前规模下接受这个误差，
      并靠 14 条用例 + 盲审兜住。
    """
    """判断"模型是不是在拒答"。**返回 None 表示无法判定**。

    ★★★★★ 第五版。DSH 独立审计指出第四版有个致命缺陷，我自己复现确认了：

        if has_ev: return False
        return False          ← 两个出口一样，_EVIDENCE 是【死代码】

      于是整个函数退化成"裸关键词 OR"，而注释里写的"三段式、先判主旨再判依据、
      不再互相短路"**根本没实现**。实测 5 条对抗样例 5 条全错，最致命的一条是：

        "我查不到具体的报销比例，但按往年惯例一般可以报销 80% 左右。"
        → 判成【拒答】。而这是【教科书级硬答】：先说查不到，再编一个数字。
        本次验收要找的失败模式，正好被自己的判据洗白成"正确拒答"。

    ⇒ 所以这次不是"加词修 bug"，而是**换了提问方向**：

        旧：模型有没有说拒答？   （反向猜措辞）⇒ 高召回、低精度
        新：模型有没有给实质答案？ （正向看内容）⇒ 本实验需要的是【低假阳率】

      关键词判据只能做到高召回低精度，而本实验恰恰要低假阳率
      （把"含拒答词的作答"误判成拒答 = 把硬答洗白）——
      ⇒ 文本判据【降级为辅助】，主指标改用结构化信号（见上层的 hit/no_hit）。

    ⚠️ 返回 None 是新增的第三态：空答案既不是拒答也不是作答
      （循环耗尽/超轮次/没产出最终回答），算成任何一边都是虚高。
    """
    if not answer or not answer.strip():
        return None                    # ← 第五版新增：空答案单列

    strong = bool(_REFUSE_STRONG.search(answer))
    # ★★ 两类拒答信号，强度不同，不能合并：
    #   _REFUSE_CAUSAL —— 明确说"给不了你要的东西"（无法依据现有制度给出答案/
    #                     并没有任何…条款/没有写明…不能套用）。⇒ 可以压过 substance。
    #   _REFUSE_LEAD   —— 措辞上出现过拒答词，但也可能只是正文里的否定
    #                     （"全日制学生不编制教材采购计划"、"查不到…但能报销 80%"）。
    #                     ⇒ 不能压过 substance，否则会把硬答洗白。
    #   ★ 我试过把两者合并成一个信号，结果那条"编 80%"的硬答立刻被判成正确拒答
    #     —— 这就是合并的代价。分开的依据是"实测反例"，不是理论。
    causal = _REFUSE_CAUSAL.search(answer)
    lead = (_REFUSE_LEAD.search(answer) or causal or _refused_at_start(answer))
    has_sub = _has_substance(answer)

    # ① 【因果收尾式】优先级最高 —— 哪怕答案里带有条款号/金额。
    #    实测依据一（h17，冒烟抓到的真实误伤）：
    #      "抱歉，这个我查不到。…所以我没法给你一个具体数字。
    #       建议你通过以下途径确认：…
    #       如果你问的是学费缴纳流程、缓缴或退费规定这类制度性问题，我可以再帮你查一下。"
    #    这是标准拒答 + 三条替代途径 + 一句补充说明，
    #    而 `_has_substance` 会命中最后那句里的"缴纳流程/申请" ⇒ 判成作答。
    #    实测依据二（61 题 × 3 遍跑批，5 道三遍都误判）：
    #      "我无法依据现有制度给出答案，也不会替你推测数字。"
    #      "并没有任何关于能否转学、如何转学的条款。"
    #      "该条款只规定了参评条件，没有写明奖励金额…不能套用。"
    #    共同形态：【承认检索到了东西，但说它不足以回答】。
    if causal:
        return True

    # ② ★ 有实质内容 ⇒ 作答。
    #    这一条挡的是【硬答】：
    #      "我查不到具体的报销比例，但按往年惯例一般可以报销 80% 左右。"
    #    （有 80% 这个可核对的数字 ⇒ 是作答，不是拒答）
    if has_sub:
        return False

    # ③ 剩下的才用一般拒答信号（此时答案里没有可核对的内容）
    if lead or strong:
        return True

    # ③ 只认依据（给引用了条款但没其他内容的答案留一条路）
    if n_evidence or _EVIDENCE.search(answer):
        return False

    # ④ 什么都没有：既没说拒答，也没给依据。
    #    这题不参与计分（无法判定），返回 None 而不是硬猜一个。
    return None


# ★ "这段话里有没有实质内容"的启发式 —— 只认【具体的、可核对的】东西：
#   数字 / 金额 / 条款号 / 书名号 / 明确的程序动作。
#   不认"建议咨询""你可以试试"这类没有任何信息的过渡话术。
_SUBSTANCE = re.compile(
    r"(第[一二三四五六七八九十百]{1,4}[条款章节项]"
    r"|第\d{1,3}\s*条"
    r"|《[^》]{2,40}》"
    # 数字类：金额/期限/比例/人数/条款序号 —— 至少 2 位数字或带量词
    r"|\d+\s*(元|块|天|日|周|月|年|分|次|人|%|％|节)"
    r"|\d{2,}"
    r"|\d+\.\d+"
    # 明确的【程序动作 + 对象】：实质内容最核心的一类
    r"|(?:提交|填写|携带|前往|缴纳|缴|交|申请|办理|下载|打印|上传|签订)"
    r"(?:申请书|表格|材料|证明|申请|原件|复印件|证件|流程|费用|工本费|表|单)"
    # 结论性陈述：能独立成句的规则性回答
    r"|(?:按规定|依照|根据(?:学校|规定|办法)|属于|不属于|给予|不予|编制))"
)


def _has_substance(answer: str) -> bool:
    """答案里有没有【实质内容】。

    ★ 判别标准不是"有没有字"，是"有没有可核对的东西"：
      数字、条款号、文件名、或一个明确的规则性陈述。
      "建议你咨询教务处"这种过渡话术【不算】实质内容 ——
      它在拒答题上恰恰是硬答的伪装（"我查不到，但建议你去问"）。
    """
    return bool(_SUBSTANCE.search(answer))


def has_evidence(answer: str) -> bool:
    """独立暴露这个判据，方便报告里直接引用（而不是藏在 is_refused 里）。"""
    return bool(answer) and bool(_EVIDENCE.search(answer))


# ==================================================================
# 路径①：直打 /chat（基线口径，与 report-*.md 完全一致）
# ==================================================================


def run_direct(qs: list, quiet: bool = False) -> dict:
    rows = []
    for i, q in enumerate(qs, 1):
        try:
            data = _get_json("/chat", {"question": q["question"], "use_rerank": True})
            hit = data.get("knowledge_hit", None)
            answer = data.get("answer", "")
        except Exception as e:                                  # noqa: BLE001
            rows.append({"id": q["id"], "error": f"{type(e).__name__}: {str(e)[:80]}"})
            if not quiet:
                print(f"    [{i:3}/{len(qs)}] {q['id']:5} ★报错 {type(e).__name__}")
            continue
        rows.append({
            "id": q["id"], "type": q["type"],
            "knowledge_hit": hit,
            "hit_n": len(data.get("sources", []) or []),
            "answer_len": len(answer),
            # ★★ 直打侧也必须落答案、也必须判拒答。
            #   之前只有 knowledge_hit，读起来"没检索到 = 拒答正确" ——
            #   但那是【检索层】在替模型作答，它证明不了模型说了什么。
            #   现在两侧都跑同一个 is_refused()，才是同一把尺子。
            "answer_refused": is_refused(answer),
            "answer_head": answer[:300],
            "error": None,
        })
        if not quiet:
            mark = "命中" if hit is not False else "未命中"
            verdict = "拒答" if is_refused(answer) else "作答"
            print(f"    [{i:3}/{len(qs)}] {q['id']:5} {q['type']:7} {mark}"
                  f"　依据 {len(data.get('sources', []) or [])} 条"
                  f"　文本判定 {verdict}")
    return {"path": "direct", "rows": rows}


# ==================================================================
# 路径②：走 agent（MCP + 工具 + 循环）
# ==================================================================


def run_agent(qs: list, bound, client: StdioMCPClient) -> dict:
    """用 policy_agent 的 McpCaller 跑完整循环。

    ★ 这里【故意不复制 run_one】：复用同一个函数，
      否则两版业务逻辑各自演化，回归数字的差异就分不清是
      "接了 agent 导致的"还是"两版代码分叉导致的"。
    """
    from agent.policy_agent import McpCaller, _DictLike
    from mcp.probe_loop import run_one
    import mcp.probe_loop as PL

    bridge = McpCaller(client)
    original = PL.TOOL_IMPL
    PL.TOOL_IMPL = _DictLike(bridge)
    rows = []
    try:
        for i, q in enumerate(qs, 1):
            try:
                rec = run_one(q, bound)
            except Exception as e:                              # noqa: BLE001
                rows.append({"id": q["id"], "error": f"{type(e).__name__}: {str(e)[:80]}"})
                print(f"    [{i:3}/{len(qs)}] {q['id']:5} ★报错 {type(e).__name__}")
                continue
            tool_texts = [str(c.get("out", "")) for t in rec["trace"]
                          for c in t.get("called", [])]
        # ★★★ 改用【结构化信号】而不是字符串匹配（2026-10-12）。
        #   原来这里写的是 `any(NO_HIT_TEXT in t for t in tool_texts)` ——
        #   靠"文本里出现了某句话"来判断，等于又一次在猜措辞。
        #   而 DSH 独立审计已经证明：靠措辞猜的判据假阳率高到
        #   会把"硬答"洗成"正确拒答"。主指标不能再靠猜。
        #   ⇒ 现在解析 tools_impl 的固定标记（SIGNAL 协议）。
        #
        # ⚠️ 解析不出来（None）时【必须和 no_hit 区分开】：
        #   None = "这次没带标记"（旧版工具/异常分支），
        #   它既不是 hit 也不是 no_hit，计分时要能把它单独摘出来。
            signals = [parse_signal(t) for t in tool_texts]
            saw_no_hit = any(s == "no_hit" for s in signals)
            saw_hit = any(s == "hit" for s in signals)
            n_signal_missing = sum(1 for s in signals if s is None)
            n_calls = sum(len(t.get("called", [])) for t in rec["trace"])
            answer = rec.get("answer") or ""
            judged = is_refused(answer)
            rows.append({
                "id": q["id"], "type": q["type"],
                # ★★★ 结构化信号：这是【主指标】的数据来源（SIGNAL 协议）
                #   三态而不是两态：
                #     "no_hit" 工具明确说了库里没有
                #     "hit"    工具明确说了有依据
                #     None      没有信号（旧版工具 / 异常分支 / 没调工具）
                #   ★ 旧字段 tool_saw_no_hit 保留，但它降级成
                #     "saw_no_hit" 的别名，不再是唯一依据。
                "signal": ("no_hit" if saw_no_hit else
                           "hit" if saw_hit else None),
                "saw_no_hit": saw_no_hit,
                "n_signal_missing": n_signal_missing,
                # 文本判据：【辅助指标】。返回三态（拒答/作答/无法判定）。
                #   之所以降级：DSH 实测它的假阳率会把硬答洗成正确拒答。
                #   但仍然必须报 —— "模型嘴上说了什么"是另一个问题。
                "answer_refused": judged,
                "rounds": rec["rounds"],
                "n_tool_calls": n_calls,
                "answer_len": len(answer),
                # ★★ 存最终回答的【前 300 字】：
                #   判据出问题时能当场看出"它到底答了什么"，
                #   不用重跑整批。这是第一版缺的关键字段 ——
                #   定位那个 17/61 时，只能靠重跑 8 题抽样才能看到答案内容。
                "answer_head": answer[:300],
                "error": None,
            })
            # ★ 逐题打印。第一版没有这个，跑了 33 分钟日志里一个字都没有 ——
            #   既看不到进度，也没法提前发现卡住或报错。
            #   ⇒ 长跑批必须逐题可见，这是"停不停得下来"的前提。
            verdict = _shown(judged)
            sig = "no_hit" if saw_no_hit else ("hit" if saw_hit else "无")
            print(f"    [{i:3}/{len(qs)}] {q['id']:5} {q['type']:7} {verdict}"
                  f"　工具 {n_calls} 次/{rec['rounds']} 轮"
                  f"　信号={sig}")
    finally:
        PL.TOOL_IMPL = original
    return {"path": "agent", "rows": rows}


# ------------------------------------------------------------------


def score_refuse(rows: list) -> dict:
    """61 道拒答题的准确率。

    ★ 分母只用【真的跑完了的题】，报错的不算对也不算错，另计。
      把报错计入分母会把"服务挂了"误判成"拒答能力差" ——
      这跟"总数守恒不等于分布没变"同源：分子分母都得说清口径。
    """
    ref = [r for r in rows if r.get("type") == "refuse"]
    ok_rows = [r for r in ref if r.get("error") is None]
    err = [r for r in ref if r.get("error") is not None]

    # ★★★★ 第四版：两侧【统一只看 answer_refused】，不再按 knowledge_hit 分叉。
    #
    #   为什么改这里（这是本次最贵的教训）：
    #     之前直打侧走 knowledge_hit、agent 侧走 answer_refused，
    #     于是报告里那句"agent 24/61 vs 直打 39/61，掉了 24 个百分点"
    #     —— 而这两个数压根不是一回事：
    #       39/61 是【检索层】的数：服务没检索到 → 就算拒答正确
    #       24/61 是【文本层】的数：模型答案里有没有拒答声明
    #     相减等于把"检索命中率"和"模型是否如实拒答"混成一把尺子。
    #
    #   ⇒ 现在两侧都跑同一个 is_refused(answer)，同一把尺子，
    #     差出来的数才真的能回答"接了 agent 之后有没有变差"。
    #     knowledge_hit 降级为【过程指标】，仍要报，但不决定对错。
    # ★★★★★ 第六版：主指标改成【结构化信号】，文本判据降级为辅助。
    #
    #   为什么不能再用文本判据当主指标（DSH 独立审计实测，我逐条复现）：
    #     "我查不到具体的报销比例，但按往年惯例一般可以报销 80% 左右。"
    #     → 判成【拒答】。而这是硬答：它编了 80% 这个数字。
    #   关键词判据天然是"高召回、低精度"，而本实验要的恰恰是低假阳率。
    #   ⇒ 主指标必须来自【工具自己说的结构化信号】，不来自猜措辞。
    #
    #   ── 主指标：信号一致性 ──
    #   拒答题上，正确行为 = 工具说 no_hit，模型就如实拒答。
    #   但【拒答准确率】本身还依赖文本判据，所以这里报两个数、互不冒充：
    #     signal_rate  工具信号本身的分布（客观，不含任何模型判断）
    #     text_rate    文本判据的结果（含模型判断，是辅助）
    #   ★ 回归判定【只看 signal_rate 相关的量】——
    #     因为它是唯一不依赖关键词猜测的数字。
    n_sig = sum(1 for r in ok_rows if r.get("signal"))
    n_sig_no_hit = sum(1 for r in ok_rows if r.get("signal") == "no_hit")
    n_sig_none = sum(1 for r in ok_rows if r.get("signal") is None)

    correct = sum(1 for r in ok_rows if r.get("answer_refused"))
    n_undecidable = sum(1 for r in ok_rows if r.get("answer_refused") is None)

    # 检索层对照：服务说没检索到、但文本上却在作答 —— 另一种隐患
    silent = sum(1 for r in ok_rows
                 if r.get("knowledge_hit") is False
                 and r.get("answer_refused") is False)

    # ---- 过程指标：不参与计分，但必须报出来 ----
    # ★ "硬答率"才是本次验收真正要找的那个数：
    #   工具明确说了 no_hit，模型却仍然编了一个答案出来。
    #   ★ 现在它的分母来自【结构化信号】，不再是字符串匹配 ——
    #     所以这个数第一次是可信的。
    n_saw = sum(1 for r in ok_rows if r.get("signal") == "no_hit")
    n_saw_and_answered = sum(
        1 for r in ok_rows
        if r.get("signal") == "no_hit" and r.get("answer_refused") is False)

    return {
        "n_refuse_total": len(ref),
        "n_scored": len(ok_rows),
        "n_error": len(err),
        "correct": correct,
        "rate": correct / len(ok_rows) if ok_rows else 0.0,
        # ★ 主指标：结构化信号（客观、可复现、不依赖关键词）
        "signal": {
            "n_with_signal": n_sig,
            "n_no_hit": n_sig_no_hit,
            "n_hit": n_sig - n_sig_no_hit,
            "n_missing": n_sig_none,
            "no_hit_rate": (n_sig_no_hit / n_sig) if n_sig else None,
        },
        # 辅助指标：文本判据（含关键词猜测，误判率实测不低）
        "text_judge": {
            "n_refused": correct,
            "rate": correct / len(ok_rows) if ok_rows else None,
            "n_undecidable": n_undecidable,
        },
        "process": {
            "n_saw_no_hit": n_saw,
            # ★★ 核心指标：拿到 no_hit 信号却仍然硬答的题数
            "n_saw_but_answered": n_saw_and_answered,
            "hard_answer_rate": (n_saw_and_answered / n_saw) if n_saw else None,
            "n_refused_without_signal": silent,
        },
    }


def score_answer(rows: list, baseline_rows: list | None = None) -> dict:
    """44 道应答题：只看有没有编造（用 RAG 层的漏答口径 + 是否有依据）。

    ★ 注意口径：基线报告里的 Recall@5=42/44 是【检索层】的指标，
      agent 路径不直接暴露 sources，所以这里【不能假装能算 Recall】。
      ⇒ 只报两个能算的：① 回答里有没有依据（answer_len>0 且非纯拒答）
        ② 平均工具调用次数。
      ⇒ 【绝对不能】把它写成"Recall 掉了多少" —— 口径不同不能相减。
    """
    ans = [r for r in rows if r.get("type") == "answer"]
    ok_rows = [r for r in ans if r.get("error") is None]
    err = [r for r in ans if r.get("error") is not None]
    if ok_rows and "knowledge_hit" in ok_rows[0]:
        hit = sum(1 for r in ok_rows if r.get("knowledge_hit") is not False)
    else:
        hit = sum(1 for r in ok_rows
                  if (r.get("answer_len", 0) > 20 and not r.get("answer_refused")))
    return {
        "n_answer_total": len(ans),
        "n_scored": len(ok_rows),
        "n_error": len(err),
        "hit": hit,
        "rate": hit / len(ok_rows) if ok_rows else 0.0,
        "mean_tool_calls": (sum(r.get("n_tool_calls", 0) for r in ok_rows) / len(ok_rows)
                            if ok_rows and "n_tool_calls" in ok_rows[0] else None),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3,
                    help="每条路径跑几遍（默认 3，报波动范围用）")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 题（冒烟用）")
    ap.add_argument("--only-refuse", action="store_true",
                    help="只跑 61 道拒答题（省时间；应答能力本轮不作为回归主指标）")
    args = ap.parse_args()

    try:
        print(f"[前置] RAG 服务：{health()}")
    except Exception as e:                                       # noqa: BLE001
        print(f"[!!] RAG 服务不可用：{e}")
        print("    先起：python -m uvicorn service:app --port 8000")
        return None

    # ★ 先验判据再跑批：判据坏了跑出来的数字全是废的，
    #   而它坏掉时的输出形态（整齐的全 0/全 1）特别像结论。
    selftest_refusal_judge()

    # 这里必须容错：evalset 下两套题集结构【不一致】——
    #   evalset/questions.json        是裸 list
    #   evalset/heldout/questions.json 是 {"_说明": [...], "questions": [...]}  ← 多一层包装
    #   第一版直接 json.load() 之后切片，拿到 dict 切片就 KeyError。
    #   （同 project 里"两把尺子并排"那类坑：口径/结构不一致必须写在代码里，别靠记。）
    raw = json.load(open(QS, encoding="utf-8"))
    all_qs = raw if isinstance(raw, list) else raw["questions"]
    qs = [q for q in all_qs if q["type"] == "refuse"] if args.only_refuse else all_qs
    if args.limit:
        qs = qs[:args.limit]
    c = Counter(q["type"] for q in qs)
    print(f"题集：{os.path.relpath(QS, _ROOT)}　{len(qs)} 题　{dict(c)}")
    print(f"基线：{BASELINE['refuse_correct']}/{BASELINE['n_refuse']} 拒答准确率"
          f"（{BASELINE['source']}）")
    print(f"每条路径跑 {args.runs} 遍　（逐题打印，可随时 Ctrl+C，"
          f"已跑完的遍会保留）\n")

    bound = ChatDeepSeek(model="deepseek-chat", temperature=0).bind_tools(MODEL_SCHEMAS)
    out_path = os.path.join(_ROOT, "agent", "_refusal_regression.json")

    # ★ 增量落盘：已跑过的遍不重跑。
    #   第一版把 3 遍绑在一个进程里，中途失败就得从头再来（白等 33 分钟）。
    #   现在每遍结束就写一次盘，--runs 加大时只补新遍。
    results = {"baseline": BASELINE, "runs": [], "partial_rows": {}}
    if os.path.exists(out_path):
        try:
            old = json.load(open(out_path, encoding="utf-8"))
            if old.get("baseline") == BASELINE:
                results["runs"] = old.get("runs", [])
                if results["runs"]:
                    print(f"[续跑] 已存在 {len(results['runs'])} 遍结果，"
                          f"本次只补到 {args.runs} 遍\n")
            else:
                print("[续跑] 基线参数变了，旧结果作废，从头跑\n")
        except Exception as e:                                   # noqa: BLE001
            print(f"[续跑] 旧结果读不了（{type(e).__name__}），从头跑\n")

    done = len(results["runs"])
    for i in range(done + 1, args.runs + 1):
        print(f"--- 第 {i}/{args.runs} 遍 · 直打 /chat ---")
        d = run_direct(qs)
        print(f"--- 第 {i}/{args.runs} 遍 · 走 agent（MCP + 工具 + 循环）---")
        with StdioMCPClient() as client:
            a = run_agent(qs, bound, client)

        rs, ra = score_refuse(d["rows"]), score_refuse(a["rows"])
        ans_d, ans_a = score_answer(d["rows"]), score_answer(a["rows"])
        results["runs"].append({
            "run": i,
            "n_questions": len(qs),
            "direct": {"refuse": rs, "answer": ans_d},
            "agent": {"refuse": ra, "answer": ans_a},
        })
        # ★★★ 逐题明细必须落盘。第一版只存汇总，于是"agent 17/61"这个
        #   数字出现时，我无法判断是真回归还是判据错 ——
        #   只能重跑 8 题抽样复现才定位到"走了不同工具路径被扣分"。
        #   ⇒ 存汇总的同时把逐题记录一起存：出问题才能当场查，
        #     而不是再花一次全量跑批的时间。
        results.setdefault("per_question", {})[str(i)] = {
            "direct": d["rows"],
            "agent": a["rows"],
        }
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

        print(f"\n--- 第 {i}/{args.runs} 遍 完成 ---")
        print(f"  直打 /chat   拒答 {rs['correct']}/{rs['n_scored']}"
              f"（{rs['rate']:.1%}）　应答命中 {ans_d['hit']}/{ans_d['n_scored']}"
              f"　错误 {rs['n_error'] + ans_d['n_error']}")
        print(f"  走 agent     拒答 {ra['correct']}/{ra['n_scored']}"
              f"（{ra['rate']:.1%}）　应答命中 {ans_a['hit']}/{ans_a['n_scored']}"
              f"　错误 {ra['n_error'] + ans_a['n_error']}")
        # ★★ 这是本次验收真正要找的那个数：
        #   工具明确说了"库里没有"，模型却仍然编了一个答案出来的题数。
        #   它比"拒答准确率"更贴近你担心的失败模式。
        p = ra.get("process") or {}
        if p.get("n_saw_no_hit"):
            hr = p.get("hard_answer_rate")
            hr_s = f"{hr:.1%}" if hr is not None else "—"
            print(f"    其中：工具回过拒答信号 {p['n_saw_no_hit']} 题，"
                  f"其中仍然硬答 {p['n_saw_but_answered']} 题（{hr_s}）"
                  f"　★ 这才是真正的回归指标")
            if p.get("n_refused_without_signal"):
                print(f"    另有 {p['n_refused_without_signal']} 题模型自己拒答了，"
                      f"但工具没回 NO_HIT_TEXT（走了别的工具路径）")
        if ans_a.get("mean_tool_calls"):
            print(f"              平均工具调用 {ans_a['mean_tool_calls']:.2f} 次")
        print(f"  已落盘（{i}/{args.runs} 遍）\n")

    print(f"明细已写入 {os.path.relpath(out_path, _ROOT)}")
    return results


if __name__ == "__main__":
    if main() is None:
        sys.exit(1)
