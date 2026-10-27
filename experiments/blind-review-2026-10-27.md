# ZCode 盲审结论

> 任务卡: D:\dsh-work\_blind_review_task.md
> 工作区: D:\_review_rag-cs
> sessionId: sess_d3387cb3-a735-4fe6-8b24-e8a10827bbcc
> traceId: 7cb314a1-04ad-4cc9-ae0e-a152583ea2d4
> tokens: 1021267 (in=980849 out=40418 cacheRead=794112)

---
审查完毕。以下为盲审报告（只读，未改任何文件、未跑任何评测/服务；所有结论均基于现有代码与已提交的 JSON 产物）。

---

# 盲审报告：RAG 评测闭环与指标

**TL;DR**：门禁机制本身真实有效（11:05 那次 reject 是货真价实的拦截），但**“第一次真实 accept”的 +1 增益小于同日同配置基线自身的运行间波动（±2 题），且 McNemar p=1.0 未写入文档**；`answer` 率实为“gold 进上下文率”，头版套件没有任何答案级校验；`rollback()` 状态机有实际 bug 且 registry 没有任何服务端消费方。按任务卡 7 个问题的对应关系在各条目标注（Q1–Q7）。

## P0 —— 会让结论失真

**P0-1｜接受“第一次真实 accept”的证据站在噪声上（Q1/Q5/Q6）**
`evalset/loop-company-52/registry.json:20-88`：两次 gate 指向同一路径对的 before/after 报告，第一次（11:05）+6/−11 拒绝，第二次（11:27）+1/−0 接受、`mcnemar_p=1.0`。文件 mtime 证明 11:05 那次的输入已被 11:24/11:27 的重跑覆盖（baseline.json `generated_at 11:24:10`、candidate.json `11:27:44`）。关键在于：同配置（namespace=company、company-52、HTTP）的基线在同一天跑出过 **41/46**（`evalset/report-scenario-company-52.json`，10:57:32，`metrics.answer.passed=41`）和 **39/46**（loop baseline，11:24）——运行间波动 ±2 > 被 accept 的增益 +1。也就是说“候选有效”与“基线这次恰好跑低了”不可区分。
- 为什么有害：这是 commit `acf78c4`、HANDOFF.md:101 与 enterprise-kb.md:301-312 的头条结论；且 11:05 reject 的原始报告已被覆盖，整个证据链不可复现。
- 关于“默认 `alpha=None` 时 +1 就 accept 是否合理”（Q1）：在 `temperature=0`（service.py:800）下生成接近确定，但本仓库自己的产物已证明同配置仍会 ±2 漂移，所以**单次运行 + `min_gain=1` + 不显著也 accept 的默认组合不合理**——作为“记录候选、待复验”可以，作为“accept 并写 active”不行。`loopgate.py:41-42` 的注释自己也承认 p=1.0 是“诚实的结论”，但默认配置没有落实这个诚实。
- 验证：对比三个 JSON 的 `generated_at` 与 `metrics.answer`/`buckets.ok`；`grep -rn "41/46" *.md`（结果为空——那次运行在文档里不存在）；`git log --all -- evalset/loop-company-52/baseline.json` 确认 11:05 版本未入库。

**P0-2｜`answer` 率是纯检索代理，且头版套件没有答案级校验兜底（Q2）**
`kb/evalkit/metrics.py:317-318` 把指标描述为“应答类题目的正确率（bucket=ok）”，但 `bucket=ok` 的判据在 `kb/attribution.py:154-160`：gold 条款进了 included 块 + （若有引用报告）引用非空——**答案文本一个字都不看**。company-52 的 46 道 answer 题全部没有 `expect` 字段（`grep -c '"expect"' evalset/scenarios/company-52.jsonl` = 0），两份 loop 报告的 notes 都写着“没有配 expect 的 answer 题 ⇒ 不计算值命中率”，即 `answer_value` 对头版套件的贡献为零。文档里“应答 39/46”实为“检索命中率 39/46”。
- 具体反例（弱判据把错答案判对，用真实语料构造）：`evalset/fixtures/company_corpus/*.md:75-76` 第十四条【迟到、早退与旷工】同时含“迟到 30 分钟以内每次扣考勤分 2 分”与“旷工半日按日工资标准 200% 扣减”。对 c50-A1（“迟到二十分钟要扣多少钱？”），若模型答“旷工半日按日工资的 200% 扣”——答非所问，但 `values.match`（kb/values.py:76-81，命中任一期望值即 True）会因期望集含 `(200.0,'%')` 而判 True。`kb/values.py:60-64` 的注释也自认频率噪声（“40 小时/月”抽出 `(1,'月')`）。
- 对门禁的传导（Q1）：`loopgate.py:22-35` 的 `_ok` 只看 bucket，候选把检索弄对、把答案数值弄错照样 accept。门禁从不消费 `answer_value`，即使它非 None。
- 验证：`python -c "from kb.values import extract; print(extract(open('evalset/fixtures/company_corpus/…md').read()))"` 查看第十四条抽出的期望集；再看报告里 answer_value 全为 null。

## P1 —— 会让指标偏差

**P1-1｜`loopgate._ok` 对 deny 题的判据恒为 False（Q1/Q7）**
`kb/loopgate.py:32-35`：`typ in ("refuse","deny")` 时要求 `bucket=="refuse_ok"`。但 deny 题的正确桶是 `"ok"`（`kb/attribution.py:115-118`），`refuse_ok` 在 deny 行上永远不会出现——`metrics.py:321-322` 的 deny 指标口径也证明这一点。后果：deny 题在 `decide()` 里永远“不对”，**修复越权泄漏不计入 gains**（+0 只能 reject），deny 行整体退出 gains/losses/McNemar 统计。当前 company-52 无 deny 题所以未爆发，对 hr-deny 套件跑闭环即触发。测试没拦住它：`tests/test_eval_loop.py:81-86` 给 deny 行造的 before bucket 是 `refuse_ok`——生产中不可能的形状，把错误假设固化进了 fixture（见 Q7 小结）。
- 验证：`decide([{"id":"d1","type":"deny","bucket":"ok"}], [{"id":"d1","type":"deny","bucket":"ok"}], min_gain=1)` 外加一个 answer miss→ok，看 gains 是否只含 answer 行；deny 修复永远不出现。

**P1-2｜`context_precision` 名实不符：分母是 gold 数不是 included 数（Q4）**
`kb/evalkit/metrics.py:133-134`：`precision = hits / len(gold_keys)`——这算的是“gold 命中比例”（多 gold 版 recall），而 :325-326 的描述写的是“喂进去的块里 gold 的占比”。现成反例：candidate.json 的 c50-E1 行 `included=2`、其中 1 块就是 gold，`context_precision=0.25`（4 条 gold 命中 1 条）——included 全是 gold 也得 0.25。聚合值 0.5933（report-scenario-company-52.json:241）因此系统性偏低且无法解释。`tests/test_multi_gold.py:48-57` 用断言 `== 0.5` 把这个语义锁死了。
- 不自洽点（Q4）：“命中任一即 ok”（:133 `recall = hits > 0`）与 `hits/len(gold)` 组合下，一道 4-gold 题命中 1 条即得满分 bucket，但 precision 记 0.25——同一题的两个“召回类”数字互相打架；真正的精度（included 里的 gold 纯度）没人算。
- 更合适的口径：recall = hits/|gold|（即现在的“precision”改名）、precision = |gold∩included|/|included|、多跳 ok 可双列（any-hit 与 all-hit）。
- 验证：取 candidate.json c50-E1 行比对 `included`、`context_precision` 与 gold 条数。

**P1-3｜`rollback()` 状态机坏，且“一键回滚”没有服务端通路（Q5）**
`scripts/eval_loop.py:154`：每次 accept 都把 history entry 置 `status="active"` 且从不清除 → 多个 entry 同时 "active"（registry.json 现状即如此）。`scripts/eval_loop.py:163-177` 的 rollback 只 pop 列表末项、把前一个重新置 active，被弹出的那项**仍标 active**：对 [A,B] 双 active 连续调用两次 rollback，第二次仍 `accepted=[A,B]`→pop B→active=A，**永远卡在 A，退不回空**，也无法连退两步；`reg["active"]` 与 history 的 status 从此互相矛盾。更根本的：registry.json 没有任何服务端消费方（全仓 grep 只有 eval_loop 自身与 check_file_refs.py），服务只认启动时环境变量 `EXPAND_LEXICON`（service.py:806），而 `.env`/`.env.example`/docker-compose/DEPLOY.md 里 **EXPAND_LEXICON 零接线**。所以 accept/rollback 改的只是一个没人读的 JSON，`kb/expand.py:8` 的“能一键回滚”与 eval_loop.py:17 的“不用重新部署”目前不成立。
- 验证：照 registry.json 的形状造双 active 临时文件，连调两次 `rollback()` 看第二次返回值；`grep -rn EXPAND_LEXICON .env.example docker-compose.yml DEPLOY.md`（为空）。

**P1-4｜文档漏报两件自家工具已经算出来的事（Q6）**
- 39/46 vs 41/46：两次都是真实运行（见 P0-1），文档（HANDOFF.md:101、enterprise-kb.md:303-305）只写 39/46→40/46，**未说明同日另有一次 41/46**，运行间差异没有被如实披露。
- significance：registry.json:8 记录 accept 的 `mcnemar_p=1.0`，enterprise-kb.md:305 的门禁一行只写“净收益 1、无回退…accept”，未提“不显著”。
- 另一处口径幻觉：enterprise-kb.md:303 表格里“应答 39/46、recall 0.8478”并排呈现，但 recall=True ⟺ bucket=ok（attribution.py:154-160 与 metrics.py:133 同判据），0.8478 = 39/46 是**同一个量算了两遍**，看起来像两个独立证据。
- 验证：对照三个 JSON 的 generated_at/metrics 与上述文档行。

## P2 —— 可维护性 / 边角风险

**P2-1｜mine() 的候选是逐题补丁，泛化性零评估（Q5）**
`scripts/eval_loop.py:114-118`：match 用整句原文，候选只在用户**逐字重复**评测题时触发；add 词取自 gold 条款的高频通用词（candidates.json:11-15 与 71-80 显示 c50-A15 与 c50-E1 的 add 完全相同——“实行/依法/合规”），无任何区分度校验。门禁只在同一题集复测，闭环没有任何 held-out 步骤，所以 accept 后的泛化性**没有任何机制评估**；且候选从本次基线运行的失败里挖（:255-257），基线噪声造成的假失败也会被挖成补丁（选择偏差）。enterprise-kb.md:303-304 把 40/46 列为“候选”收益，未说明该候选只对 7 条逐字问题生效——这是“背题”，不是检索能力修复。顺带：:96-97 对 `budget_cut`/`rank_shift` 行也挖候选，扩展词救不了被预算裁掉的 gold，白占 12 个名额。
- 验证：比对 candidates.json 的 match 字段与 company-52.jsonl 的 question（逐字相同）；对一份全 budget_cut 的报告跑 `eval_loop.py mine` 仍产 entries。

**P2-2｜固定端口 + 健康检查不验进程身份**
`scripts/eval_loop.py:205-216`（8071/8072）与 ：182-192：端口被占用时 `_wait_health` 连上的是**别的服务**（比如一个已加载候选词表的服务），基线/候选身份互换，门禁结论直接作废且无告警。
- 验证：手动起一个 `EXPAND_LEXICON=candidates.json` 的 8071 服务再跑 `eval_loop.py run`，看“基线”报告是否带扩展痕迹。

**P2-3｜gate 对报告完整性零校验**
`kb/loopgate.py:18-19` 静默丢弃无 id 行；:63-112 只取 id 交集，`n_common` 记录但不与两侧题数比对。缺 10 行的 after 报告照样能 accept。缓解因素已存在：429/503 会中断（scripts/run_scenario_eval.py:137-140，experiments/eval-latency-and-limits.md:32-43），但非 200 错误行（400 → knowledge_hit=False → retrieval_miss）仍可能制造幻影 gain。
- 验证：删掉 candidate.json 任意 10 行后跑 `eval_loop.py gate`，verdict 照出、n_common=42、无告警。

**P2-4｜refuse_ok 可被“对冲式回答”绕过（Q3）**
`kb/attribution.py:92-101,123-128`：只要答案文本含任一拒绝标记就算正确拒答。反例：拒答题答“一般应为每课时 200 元。资料中没有的话我也不知道。”→ `looks_like_refusal=True` → refuse_ok，编造内容与拒绝标记共存时不被发现；门禁的“拒答回退”（loopgate.py:94-96）因此对这类回答失效。注释（:90-91）承认了取舍，但门禁层没有第二道防线（如 used_ids/长度校验）。
- 验证：用上述 answer + `knowledge_hit=True` + `citation.used_ids=[1]` 调 `attribution.classify`，得 refuse_ok。

**P2-5｜`_norm_source` 的互顶风险 + 条款精确串匹配的脆弱性（Q3）**
`kb/attribution.py:67-82`：后缀归一使 `a.md` 与 `a.txt` 的同名条款在 `_key` 下同键，gold(a.md,第三条) 会被 a.txt 的第三条顶替命中。当前语料单文档、企业存储不带后缀，归一是**必要的**（gold 带 .md、sources 不带），实害未发生，但结构风险成立。另外条款匹配是归一后的字符串精确相等——gold“第十四条【迟到、早退与旷工】”必须与存储 clause 逐字一致（含【】标题），格式漂移即静默 retrieval_miss（mine 的 `_find_clause` 在 eval_loop.py:81-88 做了模糊匹配，指标层却没做——两处口径不一）。
- 验证：`classify` 构造 gold=("手册.md","第三条")、blocks=[{source:"手册.txt","clause":"第三条"}] → bucket=ok。

**P2-6｜expand.already 的字符集写法冗余易误改（Q5 边角）**
`kb/expand.py:48-61`：`already = set(norm)` 是**字符**集合，跨条目去重实际靠 add 后的整串元素与 `n in norm` 两个条件；单字符时 `n in already` 与 `n in norm` 完全等价。当前能工作（幂等有测试），但语义混淆，后人“清理”时容易把跨条目去重弄坏。
- 验证：两个 entry 加同一个词，apply 一次后检查 added 只出现一次（现行为正确）；删掉 `already.add` 再试（会翻车）。

**P2-7｜gate 复用路径，覆盖即毁证**
registry 的 before/after 记的是固定路径（baseline.json/candidate.json），每次 `run` 覆盖重写——11:05 那次 reject 的输入已不存在，history 里的 verdict 变成不可复现的孤证。建议每轮落盘带时间戳文件名。
- 验证：`git log --all -- evalset/loop-company-52/baseline.json`（只有一个 commit）。

## Q7 测试可信度小结

- **真实有效**：`tests/test_answer_value.py:74-86` 是合格的反向验证（错值判 False）；`tests/test_grouping_significance.py:47-58` 正确覆盖了 McNemar 数值与 `apply_significance` 的 accept→reject；`tests/test_eval_attribution.py` 各桶判据均为真实行为测试。
- **fixture 失真**：`tests/test_eval_loop.py:81-86` 用生产不可能的 deny+refuse_ok 形状（见 P1-1），leak 规则虽仍被测到（losses 规则也会拦），但 `_ok` 的 deny bug 被永久掩盖。
- **假绿风险最高**：`tests/test_eval_loop.py:96-108` 的 rollback 测试——fixture 本身就是双 active 的坏状态（把 bug 当输入），只断言返回指针，不断言 history 一致性，不测第二次 rollback（按 P1-3 会暴露卡死）。
- **锁死错误语义**：`tests/test_multi_gold.py:57` 把 hits/|gold| 断言为 precision，测试正确、语义错误，改名/改口径时它会“变红”提醒，但也说明该数字从来不是精度。

## 我认为目前没有证据支撑的文档结论

1. **“候选使应答 39/46 → 40/46”作为收益证据（HANDOFF.md:101、enterprise-kb.md:301-312）**：增益 +1 小于同日同配置基线自身波动（41↔39），McNemar p=1.0，候选只对 7 条逐字问题生效且未接入任何部署配置（EXPAND_LEXICON 零接线）。**有证据的结论只是**：“门禁会拦坏候选”（11:05 reject + evalset/loop-hard/registry.json 的 +0 reject）与“闭环机制能端到端跑通”——这两条成立；“闭环能产生真实收益”目前不成立。
2. **“应答 39/46 / 41/46”作为正确率**：是检索命中率，答案正确性在头版套件上无任何度量（P0-2）。
3. **“能一键回滚 / 不用重新部署”（expand.py:8、eval_loop.py:17,23）**：rollback 只写一个无消费方的 JSON，服务生效需手改环境变量并重启（P1-3）。
4. **enterprise-kb.md:303 的 recall 0.8478**：与“应答 39/46”是同一判据的两次计算，不构成独立证据（P1-4）。
5. **“基线 39/46”的单一口径表述**：未披露同日 41/46 的另一次运行，运行间差异未如实说明（P1-4，任务卡点名核对项：两次都是真实运行，文档只写其一）。
