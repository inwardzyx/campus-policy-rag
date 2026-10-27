# 场景题集（scenarios）

## 为什么按场景分文件

同一句"报销多久内提交"，在 **HR**、**合规**、**客服** 三个场景里判据不同：

- HR：能不能按制度办（考勤/假期/报销时限）；
- 合规：有没有越权、有没有引用到不该看的条款；
- 客服：答得对不对、拒得干不干脆。

把三类题混在一个文件里，会得到一个"整体 78%"的漂亮数字，然后不知道该修哪。
所以：**一个场景一个文件，各自冻结、各自出报告**。

## 文件格式（JSONL，一行一题）

```json
{"id": "hr-01", "type": "answer", "scenario": "hr",
 "question": "年假可以结转到明年吗？",
 "gold": ["考勤与休假管理办法.md", "第三章-第十二条"],
 "acl": ["public"]}
{"id": "hr-14", "type": "refuse", "scenario": "hr",
 "question": "公司年会在哪办？", "gold": null}
{"id": "hr-18", "type": "deny", "scenario": "hr",
 "question": "董事会的薪酬决议怎么写的？", "gold": null, "acl": ["hr"]}
```

- `type`：`answer` / `refuse` / `deny`（与 `kb/eval_runner.py`、`evalset/enterprise_eval_template.md` 同口径）；
- `gold`：`[文档名, 条款标题]`，**必须**能在语料里找到 `## <条款标题>`（有脚本查，见下）；
- `deny` 题的 `acl` 是**提问者身份**，不是文档的 acl —— 它模拟"这个人不该看到这些"。

## 现状

| 场景 | 状态 | 题集 | 语料 |
|---|---|---|---|
| `company` | ✅ 可跑（**真实公司制度**：50 条，通用版/已脱敏） | `company.jsonl` 20 题（15 答 + 5 拒，含 3 道 boundary） | `evalset/fixtures/company_corpus/`（1 份，需先跑 `normalize_clause_headings.py` 把加粗条款转 ## 标题）|
| `campus` | ✅ 可跑（复用老题集，20 题：15 答 + 5 拒） | `evalset/questions.json` | `docs/kb/`（11 份校规） |
| `hr` / `compliance` / `support` | `hr` ✅ 可跑（**合成**语料，非真实公司文件）；`compliance` / `support` ⏳ 待真实语料（HANDOFF P1） | `hr.jsonl` 24 题 / `hr-deny.jsonl` 3 题 | `evalset/fixtures/hr_corpus/`（2 篇）+ `hr_corpus_acl/`（1 篇受限） |

`campus` 直接复用 `evalset/questions.json`（不复制一份，避免两份 gold 漂移）；
其余场景等真实制度文档进来再出题——**没有语料就不出题**，否则评测只能测"模型会不会编"。

## 出题与验收纪律

1. 出题人**不看**检索代码与历史结果（沿用 `enterprise_eval_template.md` 的隔离要求）；
2. 冻结后跑，不用它调参；调参用 `heldout` 之外的题；
3. 每批改动前后用 `scripts/run_scenario_eval.py` 跑同一份题集，**配对对照**；
4. gold 与语料的联动用 `scripts/check_eval_gold.py` 守：扫到 0 条 gold 直接报红。

## 跑法

```powershell
# 1) 把语料灌进一个 namespace（只有带 namespace 的请求才有 context trace）
D:\Python-project\.venv\Scripts\python.exe scripts\run_scenario_eval.py `
    --scenario campus --namespace campus_eval --bootstrap-docs docs/kb `
    --cases evalset/questions.json --out evalset/report-scenario-campus.json

# 2) 离线复算（用录好的响应重算指标，不联网）
D:\Python-project\.venv\Scripts\python.exe scripts\run_scenario_eval.py `
    --responses <录好的响应文件.json> --out evalset/report-scenario-campus.json
```

## 报告里看什么

- 三类题的正确率 + Wilson 区间（`answer/refuse/deny`）；
- 上下文指标：`context_recall`、`context_precision`、`ndcg@3`、`budget_cut_rate`、`p95_ms`；
- **归因分布**：`retrieval_miss / rank_shift / budget_cut / gen_uncited / acl_leak / stale_version`。
  调优只能针对"当前最多的那个桶"做单点改动，然后用同一批题**配对**重跑。

## 怎么扩展（评测内核在 `kb/evalkit/`，不要另写脚本）

请求级评测已经拆成四个可替换部件，**新场景/新指标/新执行方式都只是组装**：

| 想做什么 | 改哪里 | 判据 |
|---|---|---|
| 加一个场景套件 | 放 `evalset/scenarios/<name>.jsonl`（同格式），`--cases <name>` 即可 | `validate_cases` 自动校验；**空题集直接退出 1** |
| 加一个指标 | `kb/evalkit/metrics.py` 里 `register_metric(MetricSpec(...))` | 必须写 `desc/unit/missing`（"拿不到时是什么、为什么"）；`aggregate` 与报告自动带上 |
| 换执行方式 | `kb/evalkit/transport.py`：`http` / `recorded` / `inproc` | 三者对 runner 完全等价；`recorded` 让评测逻辑能进单测、不花钱 |
| 接看板/CI | 读报告里的 `schema_version` + `aggregate` + `metric_specs` | 字段含义变更要升 `SCHEMA_VERSION`，不许悄悄改名 |

**报告 schema（`request-eval/1`）**：`schema_version / suite / mode / n_cases / generated_at /
meta / metric_specs / aggregate / rows`。

★ `metric_specs` 是刻意放在 `aggregate` 前面的：先告诉读的人"`null` 是什么意思"，
再给数字。本仓库反复踩过"把缺失当 0"的坑，这一层就是为了让它不可能被误读。

## 三种跑法（同一套内核）

```powershell
# ① 打服务（要 trace 才有上下文指标）
D:\Python-project\.venv\Scripts\python.exe scripts/run_scenario_eval.py `
    --cases campus --namespace campus_eval --bootstrap-docs docs/kb `
    --base-url http://127.0.0.1:8028 --out evalset/report-scenario-campus-evalkit.json

# ② 离线复算（读录好的响应，不联网、不花钱）
... scripts/run_scenario_eval.py --responses <响应文件.json>

# ③ 直连管道（不起 HTTP；需要模型已加载，测试里可用假模型）
... scripts/run_scenario_eval.py --transport inproc
```

## 会话级（多轮）套件：`sessions/`

`evalset/scenarios/sessions/*.jsonl`，一行一个会话：

```json
{"id": "s07", "type": "session", "scenario": "campus-followups",
 "turns": [
   {"question": "请假一天以内由谁审批？", "type": "answer", "gold": ["学生请销假制度.md", "总则-第三条"]},
   {"question": "那一个礼拜呢？",       "type": "answer", "gold": ["学生请销假制度.md", "总则-第三条"]}
 ]}
```

跑法（复用 `kb/evalkit` 的题集/指标/报告，**不是**第二套实现）：

```powershell
D:\Python-project\.venv\Scripts\python.exe scripts/run_session_eval.py `
    --cases evalset/scenarios/sessions/campus-followups.jsonl `
    --namespace campus_eval --bootstrap-docs docs/kb `
    --base-url http://127.0.0.1:8029 --out evalset/report-sessions-campus-followups.json
```

指标（`schema_version=session-eval/1`）：

| 指标 | 口径 | 什么时候是 null |
|---|---|---|
| `session_pass` | 整个会话每一轮都对 | 无会话 |
| `turn_pass_rate` | 逐轮正确率（合并所有会话） | 无轮次 |
| `rewrite_used` | 真的用上历史改写的轮数 | 0 = 没触发 |
| `ellipsis_turns` | 真正"省字追问"的轮数（改写指标分母） | 0 = 这套题没有指代 |
| `rewrite_resolved_rate` | 省掉的上下文实词被改写补回的比例 | 没有省字追问/没触发改写 |
| `repeat_question_rate` | 同会话重复提问的比例 | 无轮次 |

★ **`rewrite_resolved_rate` 是粗代理**（词重叠），不是语义判断；它只回答"改写有没有把省掉的词
补回来"，不回答"补得对不对"。第一版判据要求改写里出现 gold 条款号 —— 实测 12 轮合理改写全判 0，
因为**查询改写产出的是问句、不是引用**；这条经验写在 `kb/evalkit/session.py` 顶部。

★ 会话级**刻意不测** step/tool/goal：系统一步就结束、history 只喂改写，
硬造轨迹指标是自欺（HANDOFF §5 写了将来什么条件下才做）。

## 制度语料套件：`policy`

语料：`evalset/fixtures/policy_corpus/`（3 篇合成制度：差旅 2026 修订版 / 差旅 2024 版 / 考勤请假）。
题集：`evalset/scenarios/policy.jsonl`（20 题 = 17 应答 + 3 必须拒答）。

```powershell
# 按"2026-06-01 时点有效"的版本回答（旧版会被 as-of 规则剔除）
D:\Python-project\.venv\Scripts\python.exe scripts/run_scenario_eval.py `
    --cases policy --namespace policy_eval --event-time 2026-06-01 `
    --base-url http://127.0.0.1:8032 --out evalset/report-scenario-policy.json

# 按 2025 年时点回答：同 3 问应命中旧版（500 元 / 60 日 / 100 元）
D:\Python-project\.venv\Scripts\python.exe scripts/run_scenario_eval.py `
    --cases policy-asof-2025 --namespace policy_eval --event-time 2025-06-01 `
    --out evalset/report-scenario-policy-asof-2025.json
```

实测（2026-10-26）：

```text
policy（n=20）：应答 17/17、拒答 3/3；recall/precision/nDCG@3 = 1.0；引用合法率 1.0；
                未标注段落 0.0；budget_cut 0；p95 ≈ 3.8s；归因 {ok:17, refuse_ok:3}
as-of（n=3，event 2025-06-01）：3/3，答案带"按 2025-06-01 时点有效的版本"，值为 500 元 / 60 日 / 100 元
```

★ **不加 `--event-time` 时**，系统会答"资料中存在两套互相冲突的限额，无法确定"——这是刻意设计：
  版本没定就不替用户选；要按某时点回答，显式给 `--event-time`。

## HR 语料套件：`hr` / `hr-deny`（2026-10-26 加）

语料：`evalset/fixtures/hr_corpus/`（2 篇合成制度，public）
题集：`evalset/scenarios/hr.jsonl`（24 题 = 17 应答 + 7 必须拒答，其中 **5 题是 boundary 边界题**）

| 题组 | 数量 | 设计意图 |
|---|---|---|
| answer | 17 | 数值型条款为主（学时/限额/天数/比例），判分靠 gold 命中；h17 是跨文档多跳 |
| refuse·库外 | 2 | 食堂、年会 —— 库里压根没有这类信息 |
| refuse·**边界** | 5 | ★ 语义沾边但库里没有那个值（讲师课时费 / 绩效工资算法 / 注册资本 / 培训差旅转致 / 扣工资） |

边界题的存在意义：现有 `policy` 套件的 3 道拒答都是"完全库外"（食堂几点开饭），
拒答准确率虚高。真正的失效模式是 **"检索到了相关条款，但里面没有用户要的那个数"** ——
这批题专门打这个。

```powershell
D:\Python-project\.venv\Scripts\python.exe scripts\run_scenario_eval.py `
    --cases hr --namespace hr_eval --bootstrap-docs evalset/fixtures/hr_corpus `
    --base-url http://127.0.0.1:8030 --out evalset/report-scenario-hr.json
```

### 越权套件 `hr-deny`（必须先手工上传受限语料）

语料：`evalset/fixtures/hr_corpus_acl/`（1 篇，密级=受限）
题集：`evalset/scenarios/hr-deny.jsonl`（3 题 deny）

★ 2026-10-27 起 `bootstrap_docs` **不再写死 acl**：支持 `--bootstrap-acl hr`，
也支持文件 frontmatter 里的 `acl:`（frontmatter 优先）。所以受限语料可以直接灌：

```powershell
D:\Python-project\.venv\Scripts\python.exe scripts\run_scenario_eval.py `
    --cases hr-deny --namespace hr_deny --bootstrap-docs evalset/fixtures/hr_corpus_acl `
    --bootstrap-acl hr --base-url http://127.0.0.1:8030 --out evalset/report-scenario-hr-deny.json
```

（旧办法仍然可用：手工 curl 上传，见下面的历史命令。）

D:\Python-project\.venv\Scripts\python.exe scripts\run_scenario_eval.py `
    --cases hr-deny --namespace hr_deny `
    --base-url http://127.0.0.1:8030 --out evalset/report-scenario-hr-deny.json
```

判据：deny 题要求 `knowledge_hit=false` 且 `sources=[]`；泄漏则归因进 `acl_leak` 桶。
★ 跑 deny 套件时**不要**加 `--acl hr` —— 加了就变成"有权看"，题就测不出越权了。

清场（评测会往 namespace 里写 manifest，不清理会一直堆）：

```powershell
Remove-Item -Recurse -Force kb_data\hr_eval, kb_data\hr_deny
```

**已知覆盖空洞**：本题组用不上 `stale_version` 归因桶 —— 那需要同一 `(doc, clause)` 的
deprecated 版本，本套件的旧版对照留给了 `policy` / `policy-asof-2025`。

