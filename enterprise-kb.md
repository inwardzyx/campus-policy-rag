# 企业语料层（kb/source.py + kb/store.py）

> 2026-10-23 第一版：**数据层**先落地；检索/API 接线是下一步。
> 目标：把“读死 docs/ 的 10 份校规”升级成“按 namespace 上传/删除/加载任意语料”，
> 同时**不改**现有检索、六道把关、拒答与评测。

## 为什么加这一层

企业语料不是“一个文件夹”，而是**多源 + 权限 + 持续更新**：

- 来源：上传（PDF/Word/HTML/MD）、飞书知识库、Confluence、网页；
- 元数据：属于哪个租户/知识库、谁能看、什么时候更新、内容哈希；
- 生命周期：新增、覆盖、删除（tombstone，不能留幽灵条款）。

参考（不重复造轮子）：

- [Onyx](https://github.com/onyx-dot-app/onyx)：50+ 连接器、metadata/permissions、sync workers —— **借架构**；
- [Docling](https://github.com/docling-project/docling) / [MinerU](https://github.com/opendatalab/MinerU) / [Unstructured](https://github.com/Unstructured-IO/unstructured)：复杂 PDF/Office 解析；
- [RAGAS](https://github.com/vibrantlabsai/ragas)：context precision/recall/faithfulness；
- [Langfuse](https://github.com/langfuse/langfuse)：trace/eval。

## 已实现

### `kb/source.py`

- `DocumentMeta`：`namespace/doc_id/title/source/version/updated_at/acl/content_hash/tombstone`；
- `Source` 契约：`list_documents()` / `fetch(doc_id)`；
- `UploadSource`：读上传目录；
- `extract_text`：`.md/.txt/.html` 内置；`.pdf/.docx` 可选依赖（缺依赖报明确错误）；
- `chunk_markdown`：按 `## 条款` 切块，保留前言；`chunk_plain_text` 按空行分段；
- 产出的 chunk 保留现有字段（`doc/clause/version/source/text/text_full`），只**新增**企业元数据，不破坏 `service.RAG._guard`。

### `kb/store.py`

- `<root>/<namespace>/manifest.json` + `files/<doc_id><suffix>`；
- `upload()`：解析 → 内容哈希 → 覆盖/新增 → 记 manifest；
- `delete()`：**软删除**（tombstone=True，文件删掉），`load_chunks` 不再返回；
- `list_documents(include_deleted=True)`：审计用；
- `load_chunks()`：按 namespace 加载所有 active 文档的 chunk；坏文件不静默，记在 `last_errors`。

### `scripts/kb_admin.py`

```powershell
$py = 'D:\Python-project\.venv\Scripts\python.exe'
& $py scripts\kb_admin.py upload --ns demo --file .\faq.md --acl public
& $py scripts\kb_admin.py list --ns demo
& $py scripts\kb_admin.py chunks --ns demo --limit 5
& $py scripts\kb_admin.py delete --ns demo --doc-id <id>
```

默认落在仓库根 `kb_data/`（已 gitignore）；`--root` 可改。

## 测试

```powershell
$env:PYTHONIOENCODING='utf-8'
D:\Python-project\.venv\Scripts\python.exe -m pytest tests/test_kb_store.py -q
```

当前：**9 passed**（上传/元数据/namespace 隔离/软删除/txt+html/路径逃逸/契约/重传更新）；`tests/test_kb_api.py` **5 passed**（含 ACL 过滤与最终复检）；`tests/test_kb_eval.py` **2 passed**（smoke）；`tests/test_eval_runner.py` **5 passed**（独立集打分器）。

## HTTP 接口（2026-10-23 已接）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/kb/{ns}/documents?filename=faq.md&acl=public` | raw body 上传（不是 multipart，避免加依赖） |
| GET | `/kb/{ns}/documents?include_deleted=false` | 列文档 |
| DELETE | `/kb/{ns}/documents/{doc_id}` | 软删除 + 热更新索引 |
| GET | `/kb/{ns}/eval?acl=hr` | 每语料 smoke 质检：self-hit / 拒答探针 / ACL 泄漏 |

权限：默认只允许本机（127.0.0.1/::1/TestClient）；公网必须设 `KB_ADMIN_TOKEN`，请求头带 `X-KB-Admin-Token`。

`/chat`、`/chat/stream`、`/search` 新增可选 `namespace` 字段：
不传 = 只查 docs/ 的 `default` 语料（**不会**串到企业 namespace）；传了 = 只检索该 namespace。
企业调用方必须显式传 namespace，否则查不到上传的语料。

ACL：请求可带 `acl: ["hr"]`；`public` 永远可见，其余必须与块 `acl` 有交集。
**生产必须由 SSO/网关注入角色**，不能信客户端自己填的 `acl`。
企业上传的 `MIN_LEN` 放宽到 6（FAQ 短条目），docs/ 校规仍用 15。
检索前过滤是第一道；`_final_acl_filter` 在 rerank 之后再做一次“最终入选块复检”，
防父文档扩展/rerank 合并把未授权块带回上下文。

已验证（手动 + 测试）：

```text
upload(acl=hr) -> search(namespace=demo, acl=hr) 命中
search(namespace=demo, acl=None/finance)          拒答
delete -> search(namespace=demo)                  拒答
tests/test_kb_api.py: 5 passed
```

## 独立评测（人工题集，不是 smoke）

- 模板：[`evalset/enterprise_eval_template.md`](evalset/enterprise_eval_template.md)
- 样例：[`evalset/enterprise_eval_sample.jsonl`](evalset/enterprise_eval_sample.jsonl)
- 打分器：[`kb/eval_runner.py`](kb/eval_runner.py)（Wilson 95% 区间）
- 跑法：[`scripts/run_kb_eval.py`](scripts/run_kb_eval.py)

```powershell
$py='D:\Python-project\.venv\Scripts\python.exe'
& $py scripts\run_kb_eval.py --url http://127.0.0.1:8000 --namespace demo `
  --file evalset\enterprise_eval_sample.jsonl --out evalset\report-demo.json
```

出题纪律：出题人**不看检索代码/结果**；题目来自真实工单/角色走查；冻结后跑，不用它调参。
`answer` 判“source 命中”，不是“答案完全正确”；后者要人工 rubric。

## 上下文层 v1（2026-10-25）

文件：[`kb/context.py`](kb/context.py)。**只对带 `namespace` 的 `/chat` 与 `/chat/stream` 生效**；
`docs/` 默认语料那条 105 题旧路径完全不进入这段逻辑（`context` 为 `None`，流式 payload 里
连这个键都不出现 —— 用 golden prompt 逐字节对照守着）。

1. **八层声明**：`system / policy / history / task / memory / retrieved / tool / output`。
   ★ 2026-10-26 起：未实现的层带 `status="reserved"` + 明确理由（`reserved_layers`），
   不再只给 `used_chars: 0` —— 后者会被读成"这轮用了 0"，和"根本没做"长得一模一样。
   另有一条**锁定测试**：谁要真接记忆，必须显式改测试并删掉对应 reason（否则报告会悄悄多一个没人验证过的指标）。
   v1 **只有 retrieved 真的占预算**，其余层 `used_chars=0`，trace 里用
   `budget.enabled_layers=["retrieved"]` 明说 —— 不把"声明了"伪装成"注入了"。
2. **预算按 chars，不按 token**：默认 `CONTEXT_BUDGET_CHARS=4000`、
   `CONTEXT_RETRIEVED_RATIO=0.46`（检索额度 **1840**）、`CONTEXT_OUTPUT_RESERVE_CHARS=400`
   （给回答留位）。八层额度之和**恒等于**总预算（改 ratio 后按剩余额度重分，否则会出现
   2080 > 2000 的自相矛盾预算表）。env 坏值退回默认并记进 `budget.env_ignored`。
3. **超预算整块丢**：按精排顺序贪心，放不下的块 `reason=budget`；单块本来就超 1840 的
   `reason=oversize`；**不截半块**（截一半的条款比少一条更容易读反）。
   一块都放不下 ⇒ `degraded.retrieval=no_block_fits`、`sources=[]`、`knowledge_hit=false`，
   走既有拒答，**不零上下文硬生成**。
4. **ContextTrace**：`trace_id`（uuid4）、`blocks`（source/clause/chars/included/reason）、
   `excluded`、`degraded`、`budget`。**只存定位信息、不存正文**（正文在 `sources` 里）。
   拒答/未就绪也留 trace（`low_score` / `not_ready`）—— 那是最需要"为什么"的场景。
5. **不借用未用额度**：固定 cap 才可复现；未用额度记在 `budget.retrieved_unused`，不参与分配。

实测（`experiments/_context-v1-acceptance.json`，3 个真实问题打 `/chat?namespace=accept_ctx`）：

```text
Q1 本科生转专业需要什么条件？  hit=True  5629ms  used 1187/1840  第 3 块 681 chars 放不下 → 整块丢
Q2 学生宿舍可以用电热毯吗？    hit=True  2628ms  used  979/1840  丢 2 块（budget）
Q3 学校对考试作弊怎么处理？    hit=True  4035ms  used 1036/1840  丢 2 块（budget）
三题 degraded 均为 {"retrieval": "budget_truncated"}
```

判据与变异：`tests/test_context.py`（20 条）；`scripts/mutate_context_layer.py` 把
"旧路径 gating"与"trace/ctx 同源"各打坏一次，**2/2 变红**并按字节还原（sha256 一致）。

复现上面那份产物：服务在跑时执行 `D:\Python-project\.venv\Scripts\python.exe scripts\ctx_acceptance.py`
（默认打 8019、传 `samples/sisu-handbook-2025.pdf` 进 `accept_ctx`；可用 `--url/--namespace/--pdf/--out`）。

v2 未做：prompt 模板化、压缩（摘句/去重）、memory/tool 层真正注入、预算敏感性实验。

## 引用锚点与校验（2026-10-25，批 2）

文件：[`kb/citation.py`](kb/citation.py)。**只对带 namespace 的请求生效**：`docs/` 旧路径的 prompt
逐字节不变（`tests/test_context.py` 的 golden 用例继续守着），响应里也没有 `context`。

### 做法

1. **模板双轨**：喂给模型的块带 `cite_id` 时走 v2 模板 —— 块前缀 `[1] [2] …`，
   要求"每条结论后标依据编号；没资料支撑的话不要写；不足以下结论就说不知道"。
   ★ 用**数据触发**而不是给 `answer()` 加参数：那两条方法（同步/流式）的签名被十几个测试桩依赖，
   加参数要连带改桩，而且两条路必须喂同一份 prompt。
2. **校验（纯规则，不调模型）**：`used_ids` / `unknown_ids` / `validity` / `uncited_count` /
   `clause_numbers_in_answer`；越界编号写进 `context.degraded["citation"]="unknown_id"`。
3. **流式**：引用只能等答案收完才判 ⇒ 多发一个 `citation` 事件（只有 namespace 有，
   `sources → delta* → citation` 顺序不变，docs/ 旧流式帧序列完全不变）。

### 指标口径：叫 validity，不叫 precision

ALCE（Gao et al., EMNLP 2023）的 citation precision 判断"这句话是否**被所引段落支持**"，
需要 NLI/人判。我们只做可机械判定的部分：编号是否合法、条款编号是否出现。
所以指标叫 `citation_validity`（合法引用 / 全部引用），**不冒充** ALCE 的 precision；
语义级抽样 judge 留给批 5（用现有 DeepSeek 通道，10% 抽样、离线）。

### 实测（campus 同 20 题，配对对照）

```text
改前（无引用）：应答 14/15、p95 4794ms
改后（v2 模板）：应答 14/15、citation_validity 1.0、未标注段落/答案 0.2、p95 4683ms
```

★ 结论只能写到"**没变差**"：每臂 1 次、n=20，Wilson [0.70, 0.99] 很宽。
★ 两个度量 bug 都是这一轮实测暴露并修掉的（都有用例守着）：
① 按句子数"未标注"⇒ 模型段末标 `[1]`，假高 3~5 倍；
② 拒答白名单漏变体（"无法完整回答"不含"无法回答"）。

判据的变异体：[`scripts/mutate_citation.py`](scripts/mutate_citation.py) —— 编号从 0 开始 /
越界不标记，**2/2 变红**，并按字节还原（sha256 一致）。

## 会话轨迹级评测（2026-10-26，A+B）

先做"能做对的"，不做"看起来高级的"：系统的跨轮状态只有 `session.py` 里最近 5 轮**问题原文**
（30 分钟 TTL），历史**只喂查询改写**、不喂精排与生成。所以会话级只测这条链路。

### A. 轨迹的键（基础设施）

- `ContextTrace` / `ContextSummary` / 审计记录增加 `session_id` 与 `turn_index`
  （窗口内第几轮；超过保留窗口会饱和在 N+1，这是保留策略的已知限制）；
- `rewritten_query` 只进 **trace**（客户端本来就发过那句话），**审计不记**（审计保持无原文）；
- trace 是唯一来源：摘要由 `from_trace` 整份拷贝 —— 删掉了原先的二次赋值
  （两处赋值 = 改一处忘一处，而漏掉的那处不报错，只静默少字段）。

### B. 会话级套件与指标

`evalset/scenarios/sessions/campus-followups.jsonl`：10 个会话 / 22 轮
（指代追问、连续拒答、换主题追问、口语问法）；`scripts/run_session_eval.py` 复用 `kb/evalkit`。

| 指标 | 口径 |
|---|---|
| `session_pass` | 整个会话每一轮都对 |
| `turn_pass_rate` | 逐轮正确率 |
| `rewrite_used` | 真的用上历史改写的轮数 |
| `ellipsis_turns` | 真正"省字追问"的轮数（改写指标分母） |
| `rewrite_resolved_rate` | 省掉的上下文实词被改写补回的比例 |
| `repeat_question_rate` | 同会话重复提问比例 |

### 实测（`evalset/report-sessions-campus-followups.json`）

```text
整个会话全对 8/10 = 80%（Wilson [0.49, 0.94]）；逐轮正确率 0.9091
改写用了 12 轮，其中省字追问 4 轮，指代补全率 1.0；重复提问率 0
失败：s10#1（释义问法的检索遗漏）、s09#3（"学费多少"被答了 —— 跨轮拒答不一致）
```
★ 单次采样、10 个会话：两次跑 s09 的拒答表现就不同，别把 80% 当承诺值。

### 指标返工记录（值得记）

第一版 `rewrite_hit` 要求改写结果里出现 gold 条款号/文档名 —— 实测 12 轮改写全都合理
（"那一个礼拜呢？" → "请假一周以内由谁审批？"），却**全判 0**。
错因：**查询改写产出的是问句，不是引用**。改成 `rewrite_resolved`（省掉的实词有没有被补回），
并把拒答题的主题切换排除出分母（"那食堂几点开饭？"不是对上一轮的省字），才得到 4/4。

判据：`tests/test_session_eval.py` 10 条；`scripts/mutate_session.py` 把
"补全判定"与"session_id 唯一来源"各打坏一次 —— **2/2 变红**，三个文件按字节还原。

## 多租户 S2-1（2026-10-26）

**做了什么**：身份（`X-Api-Key`）→ 命名空间隔离（前缀匹配）→ 审计带 `tenant` → 只读管理接口。

```text
MULTI_TENANT=1 时：无 key → 401；tenant.allows(namespace)=False → 403；匹配 → 200
key 只落 sha256（create 只返回一次明文）；/admin/tenants 对外展示不含 key 哈希
/admin/audit?tenant=&session_id= 按租户/会话导出（审计本来就不存原文）
```

**S2-2 补上**：配额按租户键计（`tenant:{id}`），A 打满不会拖垮 B；
写接口（上传/删除/列文档/质检）先试管理员 token，否则按租户 key 校验 namespace。

**S2-3 补上**：`GET /console` → `static/console.html` 薄控制台（租户/语料/最近审计），
纯 vanilla、不引框架；页面只调已有 API，渲染只用 `textContent`；
两条判据（只用已知端点、禁 `.innerHTML`）各配一条变异体。

**S2-4 补上**：私有化单机部署 —— `kb-data` 卷持久化（语料/审计/租户表）、
S2 环境变量进 compose、`preflight` profile、`DEPLOY.md` 私有化章节、`create_tenant.py`；
`scripts/preflight.py` 把「起来了但 /chat 全 500」这类问题挡在部署前（判据有变异体守着）。

**仍没做（别当已完成）**：配额数值不分租户自定义、计费。

**为什么默认关闭**：`MULTI_TENANT` 不设时一切走 `default` 租户（namespace 不限）——
既有部署、脚本、测试一行不改就能跑。加功能不能把默认路径搞挂。

**为什么复用 `KB_ADMIN_TOKEN`**：多一套凭证就多一处会配错的地方；管理接口沿用
"有 token 用 token、没有只允许回环"的既有闸门。

## 真实结构制度语料验收（policy，2026-10-26）

语料：[`evalset/fixtures/policy_corpus/`](evalset/fixtures/policy_corpus/) —— 3 篇合成制度
（差旅报销 2026 修订版 / 差旅报销 2024 版 / 考勤请假），**结构参照真实企业制度**
（文号、生效日、修订记录、审批权限表、跨制度引用），但不是任何公司的内部文件。

题集：20 题（17 应答 + 3 必须拒答）+ 3 题 as-of 对照。

```text
policy（event 2026-06-01）：应答 17/17、拒答 3/3
  context recall / precision / nDCG@3 = 1.0；引用合法率 1.0；未标注段落 0.0
  budget_cut 0；p95 ≈ 3.8s；归因 {ok:17, refuse_ok:3}
as-of（event 2025-06-01，3 题）：3/3，答案带「按 2025-06-01 时点有效的版本」
  值分别为 500 元/晚、60 日、100 元/日（旧版）
不加 event_time：答「资料中存在两套互相冲突的限额，无法确定」——版本没定就不替用户选
```

### 这轮抓到的第二个「指标判错对象」

库外题的拒答，旧判据用 `knowledge_hit` 代理「有没有答」。实测模型会引用**检索到但不充分**的
资料，然后说「资料中没有…我不知道」——`knowledge_hit=True` 却是**正确拒答**，被误判成
「意外作答」（20 题里 2 道，拒答显示 1/3）。

改成按**答案文本**判（`looks_like_refusal`），拒答变 3/3。
与之前 `rewrite_hit`（查询改写不该含条款号）是同一类错误：**判据测错了对象**。

### 飞书接入的边界（实测，2026-10-26）

- `tenant_access_token` **不能自举进知识空间**：加成员报 `131006 permission denied`；
- 知识空间成员接口的合法组合是 **`member_type=appid` + `member_role=member`**
  （`app`/`openapp` → `invalid member_type`；`read/reader/viewer/edit/editor` → `invalid member_role`）；
- **公开空间不允许任何成员**（`131101 public wiki space can't create member`）→ 想加成员必须先改回私有；
- 结论：需要管理员在管理后台给应用开「云文档/知识库访问」，或走 `user_access_token` 桥接（不推荐长期）。

### 第一次真实 accept（2026-10-27，52 题导入集）

从 WorkBuddy 导入 52 题（46 答 + 6 拒，**39 题引用 2 条以上条款**）：

| 阶段 | 结果 |
|---|---|
| 基线 | 应答 **39/46**、拒答 6/6、recall 0.8478 |
| 候选（查询扩展词） | 应答 **40/46**（gain `c50-E1`，0 loss）、recall 0.8696 |
| 门禁 | **accept**（净收益 1、无回退）——但 `mcnemar_p=1.0`，**不显著** |

**必须一起读的限定**：同日同配置基线还跑出过 **41/46**，运行间波动 ±2 > +1；
且候选只对那一句逐字问题生效（不是检索能力修复）。所以这条只能算**机制跑通**，不是收益。

**关键过程**：第一版候选的 match 取问题前两个词（如"公司"），跑出 **6 gain / 11 loss**，
其中 5 道拒答题被答了 —— 门禁按"拒答回退"直接拒。把 match 改成**整句**后，候选只影响目标题，
才被 accept。这正是门禁存在的意义：**没有它，这个坏候选就上线了**。

### 为什么闭环还没 accept 过（2026-10-27 实跑四种场景）

| 场景 | 结果 |
|---|---|
| company（20 题） | 15/15 |
| company-hard（8 题：跨条款/口语转述） | 8/8 |
| 基线降级 `INDEX_TEXT_MODE=plain` | 8/8 |
| company + hr 混库 | 8/8 |

全部"无事可做"。**这不是闭环没跑通，而是当前语料上没有失败可修**。
accept 分支目前只有单测覆盖（`test_gate_accepts_net_gain_without_loss`）。
要触发它，需要更难的题集（真正超出检索能力的表述）或接入真实用户问题——而不是人为造失败。

### 用闭环跑 company（2026-10-27）

```
eval_loop run --suite company --namespace company --corpus evalset/fixtures/company_corpus
① 基线 15/15 → ② 挖到 0 条候选 → ✗ 无事可做（如实停止）
```

结论：**没有失败就不改**。这是闭环的正确行为，不是没跑通。

**过程中抓到的自家 bug**：重构 `_run_eval` 时把 `--bootstrap-docs` 弄丢了，首跑基线是空库，
产出 **15 条假失败**（门禁因为"净收益 0"拒了，但候选已经写出来了）。已补回，并加回归测试
断言命令里必须带 `--namespace` 与 `--bootstrap-docs`。

## 真实公司制度套件（2026-10-27）

语料：`evalset/fixtures/company_corpus/企业规章制度（50条）.md`（通用版，公司名已脱敏）。
原始文件是「`## 章` + `**第X条【标题】**`」写法，切块器只认 `##`——
直接用会把整章挤成一块。所以先跑 `scripts/normalize_clause_headings.py`（条款行 → `##` 标题），
再上传，得到 **51 块**（50 条 + 前言）。

题集 `company.jsonl` 20 题：15 道应答（数值/程序）+ 5 道拒答（2 道库外 + **3 道 boundary**：
年终奖公式 / 工龄买断 / 股权激励——制度里只提"项目奖金"，没有这些细则）。

**实测**：应答 **15/15**、拒答 **5/5**、recall/precision **1.0**、nDCG@3 **0.9754**。

### 这个套件抓到的两个真 bug（都已修）

1. **文档名口径不一致**：企业存储里 `doc` 不带 `.md`，默认语料带 ⇒ 检索明明命中，归因却记
   `retrieval_miss`、`context_recall` 0.0。修法：比较前只去已知后缀（`kb/attribution._norm_source`）。
2. **nDCG 一直是失真值**：qrels 键是 `doc｜clause`，run 键只有 doc ⇒ 修正后 campus 从"1.0"变成
   **0.9087**（原来那个 1.0 是 bug 造出来的），company 0.9754，hr 1.0（这次是真的）。

## 分组指标与显著性子门禁（2026-10-27）

**分组指标**：报告新增 `by_category`，按题型分别给 Wilson 区间——
小样本下区间会如实变宽，能看出"哪一类没覆盖"。分类规则：题面 `tags` 优先，
否则拒答→`out_of_scope`、越权→`acl_deny`、问题含单位词→`numeric`、其余 `other`。

HR 实测：

| 类目 | n | 应答 | 拒答 | 值命中 |
|---|---|---|---|---|
| boundary | 5 | — | 1.0 | — |
| numeric | 12 | 1.0 | — | 1.0 |
| other | 5 | 1.0 | — | — |
| out_of_scope | 2 | — | 1.0 | — |

**显著性门禁**：闭环门禁加精确 McNemar（`mcnemar_p`）。默认仍按"净收益/回退"判，
加 `--alpha 0.05` 进严格模式：**+1/0 的 p=1.0（不显著）会被拒**，+6/0 的 p=0.031 才放行。
这是诚实的：20 题的小样本，单题提升本来就不该叫"有效"。

## 答案值校验与拒答细分（2026-10-27）

**问题**：原来的 `answer` 指标只判"gold 条款进没进上下文"，**检索对了就算过**——
条款进了上下文、答案却写错数，报告看不出来。

**做法**：
1. `kb/values.py` 从条款抽 (数值, 单位)（含中文数字"三天"与单位同义"时/小时"）；
2. `scripts/extract_answer_values.py` 生成 `expect` 映射——**只判问题里问数值的题**
   （问题没有"多少学时/几天/比例"这类单位词就不判，缺失≠0）；
3. 指标 `answer_value`：答案里出现期望值任一项即命中（弱判据，报告标注 auto）；
4. 拒答归因新增两桶：`refuse_context_overreach`（引用了相关块却越界作答）与
   `refuse_fabricated`（没有任何引用，疑似编造）。

**实测**（HR 24 题）：`answer=17/17`、`context_recall=1.0`、`answer_value=**1.0**`（8 道数值题）。

**抓到的假阴性**：h09「服务期内离职培训费要还吗」不含数值，旧版自动判值把它判成未命中——
修正为"只判问数值的题"。这条说明：**值校验必须知道"用户在问什么"，不能只看条款里有什么数字**。

**判据**：`tests/test_answer_value.py` 8 条（含"值写错必须判 False"的反向用例）；
变异体 `scripts/mutate_answer_value.py` **2/2 变红**。

## 评测闭环 v0（2026-10-27）

```
基线评测 → 从失败题挖候选（查询扩展词） → 同题集重跑 → 配对门禁 → registry / 回滚
```

**门禁规则**（四条全过才 accept）：净收益 ≥ 1；无回退；拒答题不变成作答；无越权泄漏。

**实测**：
- campus / loop-hard 两套题基线均全对 ⇒ **无失败可挖**（闭环如实报"无事可做"，不编收益）；
- 故意做坏的候选（往查询里塞"食堂/年终奖"）实测被门禁 **reject**（`evalset/loop-hard/registry.json`）；
- 判据变异体 2/2 变红（门禁不查回退 / 扩展不判重）。

**回滚**：`registry.json` 的 `active` 指回上一版或置空；服务重启即生效。

## 改写/精排缓存（2026-10-26，配对实验后默认开启）

压测显示瓶颈是**上游 LLM 的 3 次调用/问**（并发 8 时 p95 翻倍、吞吐不升）。
其中改写与精排是"同输入⇒同输出"的纯函数，可以缓存；**生成不缓**（答案必须针对本轮）。

**实验设计**（同一进程先冷跑再热跑，避免跨运行抖动）：

| | 冷跑 p50/p95 | 热跑 p50/p95（并发 8） | 热跑 token | 检索结果一致 |
|---|---|---|---|---|
| 关缓存（控制组） | 3756 / 5441ms | 6625 / 8370ms | 11070 | 8/10 |
| **开缓存** | 3844 / 6667ms | **2528 / 4045ms** | **2913** | **10/10** |

⇒ p95 **-39%**、token **-73%**，且检索结果与冷跑**逐项一致**。

**一个必须记的教训**：不能用"答案文本一致"判缓存——控制组（关闭缓存）两轮答案也只有 **4/10** 一致，
因为生成本身有随机性。判据必须是**检索结果（sources 顺序）**。

**回滚**：`CACHE_ENABLED=0`。语料增删时缓存自动清空（候选变了，旧分数不能留）。

## 容量与成本（2026-10-26 实测）

单进程、完整链路（改写 + 精排 + 生成）、12 请求/档：

| 并发 | 成功 | p50 | p95 | 吞吐 | token/问 |
|---|---|---|---|---|---|
| 1 | 12/12 | 2202ms | 3709ms | 0.39 rps | 732 |
| 4 | 12/12 | 2514ms | 3342ms | 1.48 rps | 729 |
| 8 | 12/12 | 4735ms | 6457ms | 1.35 rps | 730 |

**结论**：并发到 8 时 p95 翻倍而吞吐不升 ⇒ 瓶颈在**上游 LLM 的 3 次调用/问**，不在本服务；
这正是"先量后改"该去动缓存（改写/精排）而不是加副本的证据。

**成本**：≈730 token/问（输入 581 / 输出 149）。金额**不写死**——
`scripts/cost_report.py` 只在给了 `PRICE_INPUT_PER_M`/`PRICE_OUTPUT_PER_M` 时才算，
没给就明确写"未配置单价"（过期价格比没有数字更坏）。

**可观测**：`GET /metrics`（Prometheus 文本，手写无依赖）暴露请求数/token/拒绝数/块数/uptime；
`GET /admin/usage` 给进程内合计（重启清零，已标注）。

## 按条款号检索（2026-10-26，配对实验后改默认）

**问题**：索引只放正文，条款号只在元数据里 ⇒ 问「第八十三条」检索命中 0。真实用户一定会这么问。

**做法**：`INDEX_TEXT_MODE`（默认 `doc_clause_text`）把索引文本变成 `doc + clause + text`；
`text` 保留为回滚开关，改配置即可，不用改代码。

**配对实验**（同批 32 题、同语料，产物 `experiments/ab-index-text.json`）：

| | `text`（旧） | `doc_clause_text`（新） |
|---|---|---|
| 条款题 | 2 / 15 | **13 / 15** |
| 内容题 | 17 / 17 | **17 / 17（零退化）** |

改完复跑 `policy` 20 题：应答 17/17、拒答 3/3、引用合法率 1.0、p95 ≈ 4.2s（与改前一致）。

**实验期间踩到的两个"假 0"**（都记进 HANDOFF §4）：
1. 没传 `event_time` ⇒ 新旧两版同时在场，gold 被旧版挤掉；
2. 脚本连续请求撞上限流（默认 burst=10）⇒ 后 17 题全是 429，看起来像"检索退化为 0"。

## 真实 PDF 的解析现实（2026-10-26 实测）

用 `samples/sisu-handbook-2025.pdf`（3.5 MB 中文手册）实测：

| 解析器 | 字符 | 行 | `##` 标题 | 表格行 | 结论 |
|---|---|---|---|---|---|
| `pypdf` | 339,901 | 11,969 | **0** | **0** | 正文抽得出，**结构全丢** |
| `Docling` | — | — | — | — | 本机被 WDAC 拦（`torch_python.dll`），只能 Docker，未完成对照 |

**这不是小事**：我们的切块是按 `## 条款` 切的 ⇒ 真实 PDF 直接上传会切出**没有条款号的定长块**，
引用无法指回条款、gold 也对不上。

### 修法：`第X条` 归一化（`kb/normalize.py`）

把形如 `第五条` 的行提成 `## 第五条`；已有 `#` 标题时**原样返回**（幂等）；
长尾正文（`第三条所述情形包括……`）不许误提成标题。

同一份 3.5 MB 手册：**修前 0 块带条款 → 修后 1653 块、全部带条款号**。

### 判据

`tests/test_normalize.py` 4 条（能补 / 幂等 / 无条款退回定长且不造条款号 / 长尾不误提）；
`scripts/mutate_normalize.py` 两条变异体（补完标题仍退回定长 / 去掉长尾挡板）**2/2 变红**。
对照脚本：`scripts/compare_parsers.py`（产物 `experiments/report-parse-quality.json`）。

## 连接器与增量同步（S1，2026-10-26）

文件：[`kb/connectors/`](kb/connectors/)。把"外部系统"收敛成两个动作（**列文档 / 取正文**），
同步逻辑只有一份 —— 飞书、Confluence、共享盘都只是连接器实现。

### 变更判据：哈希 ∪ ACL ∪ 元数据

```text
变更 = content_hash 不同  OR  acl 不同  OR  title/version 不同
```

★ 为什么不只看 `updated_at`：外部系统的时间戳可能滞后/时区不一致；而 **ACL 变化不会改内容**，
只看内容哈希就会漏掉"权限已经收回、库里还能看到"这种最危险的漂移。

### 删除只碰"自己拥有"的文档

`Connector.owns(record)`（默认按 `source` 前缀）。否则一个 namespace 里同时有连接器同步的文档
和人工上传的语料时，连接器一轮同步就会把后者清掉。

### 权限变更如何"下一次检索就生效"

ACL 存在 chunk 上；检索层每次都用**请求时的角色**比对（`_acl_allows`）。
所以同步更新 ACL 之后，**不需要重建索引**，下一次检索就会被挡住。验收用例直接断言这一点。

### 离线验收（不需要飞书 scope）

```powershell
D:\Python-project\.venv\Scripts\python.exe scripts/connector_sync.py `
    --connector fixture --source evalset/fixtures/connector_demo --namespace demo
```

实测（fixture 2 篇，其中 1 篇 `acl=["hr"]`）：首次 `added=2`；二次 `unchanged=2`；
改正文 `updated=1`；改 ACL `updated=1` 且 `changed_reasons` 含 `acl`；
删一条 manifest `deleted=1` 且**人工上传的语料仍在**；`--dry-run` 不写库。

### 飞书报错不再靠猜

`kb/feishu.py::explain_error` 把 `99991672` 翻译成清单：
**缺哪些 scope**（从 msg 里正则提取）+ ①加权限 ②**发版**（不发版不生效）
③管理员审批 ④**把应用加入知识空间**（scope ≠ 空间成员）⑤重跑命令。
CLI 失败时直接打印这份清单（`scripts/feishu_sync.py` 已接）。

### 判据

`tests/test_connectors.py` 8 条 + `tests/test_feishu_sync.py` 6 条（老测试，改造后仍绿）；
`scripts/mutate_connectors.py`：忽略 ACL 变化 / 清单删掉"发版"，**2/2 变红** + 字节还原。

## 审计落盘（2026-10-26，批 5）

文件：[`kb/audit.py`](kb/audit.py)、[`scripts/prune_audit.py`](scripts/prune_audit.py)。
每个**带 namespace** 的请求写一行 JSONL（`docs/` 旧路径一行不写）。

### 记什么 / 不记什么（这条比"多记点"重要）

| 记 | 不记 |
|---|---|
| `trace_id`、`namespace`、`route`、`decision`（answer/refuse/error） | 问题原文（只存 `sha256[:16]` + 字数） |
| 命中的 `doc` / `clause`、`n_blocks`、`chars_used` | 块正文（正文不进审计） |
| `as_of`（event_time + 丢弃原因）、`citation`（编号/合法性） | 答案原文（只存 `answer_chars`） |
| `roles`（组织维度）、`took_ms`、`rerank_used`、`retention_days` | 个人信息 |

三条硬约束：① 原文/正文都不进日志（它们有各自的保留期与访问控制）；
② 写失败**不拖垮请求**（只 warning 一次，带原因）；③ 保留期清理是**独立脚本**，
不在服务里自动删（自动删会和并发追加打架，也不再"可审计"）。

### 实测（`experiments/_audit-sample.jsonl`，语料 synthetic）

```text
/chat?namespace=audit_demo&event_time=2025-06-01
  答案开头：按 2025-06-01 时点有效的版本：…… 400 元 ……[1]      ← 版本措辞生效
  as_of：dropped_by_reason={"not_yet_effective":1}
  审计：1 行；docs/ 旧路径那次请求没有新增行
  原文在审计里吗：False
```

### 判据

`tests/test_audit.py` 9 条；`scripts/mutate_audit.py` 把"写问题原文"和"清理时删脏行"
各打坏一次 —— **2/2 变红**，`kb/audit.py` 按字节还原（sha256 一致）。

## 版本 / as-of（2026-10-26，批 4）

文件：[`kb/versioning.py`](kb/versioning.py)。回答"**这件事发生在哪一天，当时有效的是哪一版**"。

### 字段与规则

- 文档级：`effective_date`（`YYYY-MM-DD`，空串 = **日期未知**）、`status`（`active`/`deprecated`），
  来自 frontmatter，贯通 loader → store manifest → chunk；
- 请求级：`/chat`、`/chat/stream` 新增可选 `event_time`（pydantic 校验格式，写错直接 422）；
- 裁决（运行时、检索之后精排之前）：
  1. 带 `event_time`：只留 `effective_date <= event_time`；晚于它的丢（`not_yet_effective`）；
     **日期未知的块保留并计数**（不假装能裁决）；
  2. 不带 `event_time`（现行）：丢 `status=deprecated`（`stale`）；
  3. 同一 `(doc, clause)` 多版本：留当时最新的一版，其余 `superseded`。
- 丢弃的块进 `trace.excluded`（带 chars 与原因），`context.as_of` 汇总各原因计数；
  归因里三种原因都归到 `stale_version` 桶。

### 端到端：先撞墙，再修好（2026-10-26）

修法是**数据触发**的：只有块带 `effective_date` 时，才把同 `(doc, clause)` 的
不同版本当「版本兄弟」——近似重复关放行、入库时**不裁决**，留给查询时按 `event_time` 裁决。
没有生效日的 `docs/` 校规走原逻辑 ⇒ **263 留 / 23 拦一个没变**（老守卫全绿）。

修复后证据（`experiments/_asof-acceptance-fixed.json`，语料 synthetic）：

```text
索引里该条款 2 块、被拦 0、/search 返回 2 条
event_time=2025-06-01 → kept=1 dropped={"not_yet_effective":1} → 答案 400 元
不传 event_time       → kept=1 dropped={"superseded":1}     → 答案 600 元
```

判据：`tests/test_versioning.py` 18 条 + `scripts/mutate_versioning.py` **3/3 变红**
（忽略 event_time / 不丢废止版 / 版本兄弟判定失效），两次还原都按字节核对。

### 当初的墙（留档）

受控验收（`experiments/_asof-acceptance.json`，语料 synthetic）暴露了数据层冲突：

```text
两版都进了 manifest（2024-01-01 / 2026-01-01，doc_id 不同）
但 /search 只返回 1 条
/guard-report: {"doc":"差旅报销制度","clause":"第三条","reason":"[asof_demo] 近似重复"}
```

⇒ **六道把关的"近似重复"关（余弦 > DUP_THRESHOLD）在"版本冲突"关之前跑**，
  于是两版文本几乎一样（只差数字）时，新版被当重复丢掉，as-of 永远只看到一版。
⇒ 要让 as-of 真正可用，必须让数据层允许"同一 (doc, clause) 多版本共存"，
  也就是近似重复关要**版本感知**（`version`/`effective_date` 不同就不算重复）——
  这属于改动六道入库把关，本轮按约束没有动，交给人决定（HANDOFF §5 有 a/b/c 三条路）。

### 判据

`tests/test_versioning.py` 14 条（时点/废止/多版本/日期未知/上传读取/服务接线/归因桶），
`scripts/mutate_versioning.py`：忽略 `event_time`、不丢废止版 —— **2/2 变红**，字节还原 sha256 一致。

## 块级压缩（2026-10-26，批 3，默认关）

文件：[`kb/compress.py`](kb/compress.py)。只做**块边界/行边界**的减法，绝不重写、绝不截半句
（ACE 的 context collapse 是反例：整体重写会让 18k token 塌成 122 token，准确率反降）。

### 先量后改：这套语料上其实没什么可压的

271 块实测（2026-10-26）：

```text
块长：中位 75 字 / 均值 118 / 最大 1870
页眉页码噪声行：0 处（loader 已经清过页脚）
近重复对（3-gram Jaccard ≥ 0.8）：5 对（其中 3 对是跨文件完全相同的样板条款）
超过检索额度（1840 chars）的块：1 块
```

⇒ 只实现**两条有证据的规则**：近重复去重（保留**先到**的块：排序更靠前，且可复现）、
  噪声整行删除（当前 0 命中，保留给上传的 PDF/HTML 语料）。
⇒ **刻意不做**：句子级抽取（中位 75 字的块抽完只剩半句，风险大于收益）、
  超长块按句拆段（只有 1 块，拆了会改变出处展示与引用编号语义）。

### 开关与配对结果（同一批 20 题）

`CONTEXT_COMPRESS=rule` 才开，默认 `off`（不量就改是禁止的）。

```text
压前：应答 14/15、context_recall 0.9333、未标注段落 0.2667、p95 3558ms
压后：应答 14/15、context_recall 0.9333、未标注段落 0.2667、p95 3692ms
压后触发计数：启用 15 题、去重丢弃 0
```

★ 两臂一样的原因**不是**"压缩没用"，而是**没触发**：5 对近重复没有一对同时进过同一题的 top-k。
  结论是"当前语料无压缩收益 ⇒ 默认关，等真实企业语料再复测"，不是"压缩有害/无用"。

### 判据

`tests/test_compress.py`（9 条）+ `scripts/mutate_compress.py`：
把去重阈值抬到永不触发、把噪声正则改成吞掉整行 —— **2/2 变红**，并按字节还原（sha256 一致）。

## 评测自进化层 v1（2026-10-25）

三个文件：`kb/attribution.py`（归因）、`kb/eval_metrics.py`（指标）、
`scripts/run_scenario_eval.py`（按场景跑）+ `scripts/check_eval_gold.py`（gold 联动检查）。

### 为什么先做"归因"，而不是先加功能

只报"答对 14/15"没有任何方向：同样是答错，可能是检索没捞到、捞到了被预算裁掉、
排在第 4 名、或答了没标引用——修法完全不同（换切块 / 调预算 / 改排序 / 改模板）。
所以每个失败样本必须落进**一个**桶：

| 桶 | 判据 | 该改哪层 |
|---|---|---|
| `retrieval_miss` | gold 不在 trace 的候选里 | 数据层（切块）或检索 |
| `rank_shift` | gold 在候选里，但没进 included | 精排 / 权威序 |
| `budget_cut` | gold 进了 excluded，reason=`budget`/`oversize` | Runtime 预算或压缩 |
| `gen_uncited` | gold 进了 included，但答案没标引用（**等批 2**） | 模板 |
| `acl_leak` | 越权题却命中块（红线） | ACL 双道 |
| `stale_version` | gold 因版本过滤被排除（**等 as-of**） | 数据层 |

★ 为此 trace 新增了 `candidates`（`source/clause/rank`，不带正文也不带分数）：
**没有它，`retrieval_miss` 和 `rank_shift` 长得一模一样**，归因会系统性偏错。

### 指标与口径（不重复造轮子）

- `context_recall` / `context_precision`：gold 是否进了这一轮**真正喂给模型**的块（自算，只有 trace 知道）；
- `nDCG@3`：用 `ir-measures`（TREC 口径）+ `pytrec-eval-terrier`，**没有自己写**；
- Wilson 区间：复用 `kb/eval_runner.wilson_interval`；
- `p95_ms`、`budget_cut_rate`：从响应/trace 现算。

**缺失 ≠ 零**（三处都显式返回 `None`）：没有 trace 时 `context_recall=None`；
`ir_measures` 没装时 `nDCG=None` 并附说明；引用字段还没有时 `citation_precision=None`。

### campus 基线（n=20，2026-10-25）

```text
应答 14/15 = 93.3%  Wilson [0.7018, 0.9881]
拒答  5/5  = 100%   Wilson [0.5655, 1.0]
context_recall 0.9333 / context_precision 0.9333 / nDCG@3 0.9333
budget_cut_rate 0 / p95 4794ms
归因：{"ok": 14, "refuse_ok": 5, "retrieval_miss": 1}
```

产物 `evalset/report-scenario-campus.json`。唯一失败 q2 是**释义问法**检索遗漏
（"违纪了的话，学校都有哪些处分等级啊？" → 实际捞到第二十四条/第五条，gold 是第四条），
`budget_cut_rate=0` 说明这批题上 1840 的额度没裁块 —— 问题在检索/改写，不在预算。

### 评测内核（2026-10-26，可复用）

请求级评测已拆成 `kb/evalkit/` 四件套，**新场景/新指标/新执行方式都只是组装**：

| 部件 | 职责 | 换掉它意味着 |
|---|---|---|
| `cases` | 题集 schema + 校验 + 套件解析（`campus` 或路径） | 加题型（会话/工具轨迹） |
| `metrics` | **指标注册表** + 逐题判分 | 加指标只加一条 `MetricSpec` |
| `report` | `schema_version=request-eval/1` + 指标说明书 | 接看板/CI |
| `transport` | `http` / `recorded` / `inproc` | 不联网、不起服务也能跑 |

三条硬规矩写在 `kb/evalkit/__init__.py` 顶部：**缺失有名字**（每个指标声明 null 的含义）、
**空转报红**（0 道题是错误）、**加指标不改 runner**（`register_metric` 后报告自动带上）。

重构的行为证据：同一批 20 题、同一服务，新旧 runner 的 **8 个指标逐项完全一致**
（`evalset/report-scenario-campus-evalkit.json` vs `report-scenario-campus-citation.json`）。

### 已知缺口（别当成"CI 覆盖了"）

`pytrec-eval-terrier` **没有 Linux / cp314 的 wheel**（实测），所以 CI 不装
`requirements-eval.txt`，`tests/test_eval_attribution.py` 里那条 nDCG 真值用例在 CI 是
**skip**、只在本地/评测机跑。另有一条不依赖它的用例守着"缺依赖时必须是 `None` 而不是 0"。

### 复现

```powershell
# 服务在跑时：把 docs/kb 灌进一个 namespace（只有带 namespace 的请求才有 trace）
D:\Python-project\.venv\Scripts\python.exe scripts\run_scenario_eval.py `
    --scenario campus --namespace campus_eval --bootstrap-docs docs/kb `
    --base-url http://127.0.0.1:8020 --out evalset/report-scenario-campus.json
# ★ 跑完请删掉 kb_data/campus_eval：残留 namespace 会被启动时一起索引，
#   test_guard 的语料计数断言会因此变红（那是环境残留，不是回归）。
```

## 飞书 wiki 连接器（2026-10-23）

文件：[`kb/feishu.py`](kb/feishu.py)、CLI [`scripts/feishu_sync.py`](scripts/feishu_sync.py)。

用到的飞书接口：

| 接口 | 用途 |
|---|---|
| `POST /auth/v3/tenant_access_token/internal` | 取 tenant token |
| `GET /wiki/v2/spaces/:space_id/nodes` | 分页列子节点（`parent_node_token`） |
| `GET /docx/v1/documents/:document_id/raw_content` | 取 docx 纯文本 |

应用权限（缺一不可）：

- `wiki:wiki:readonly`（或 `wiki:wiki` / `wiki:space:retrieve`）；
- `docx:document:readonly`（或 `docx:document`）；
- **应用被加入目标知识空间，且对节点有阅读权限**。

> 2026-10-23 实测：现有应用 `cli_aa5296…` 调 `iter_spaces()` 返回
> `99991672 Access denied ... [wiki:wiki, wiki:wiki:readonly, wiki:space:retrieve]`
> ⇒ 还没开 wiki 权限。去开发者后台「权限管理」加上述 scope、发布版本，
> 再把应用加入知识空间（成员/管理员），否则连 space 列表都拉不到。

```powershell
$env:FEISHU_APP_ID="cli_xxx"; $env:FEISHU_APP_SECRET="xxx"
$py='D:\Python-project\.venv\Scripts\python.exe'
& $py scripts\feishu_sync.py --space-id <space_id> --namespace feishu_demo --acl public
# 排错：先列 space
& $py scripts\feishu_sync.py --list-spaces
```

同步语义：

- 只同步 `docx`；`sheet/bitable/file` 记 `skipped`；
- 按 `obj_edit_time` 生成版本；内容哈希不变 → `unchanged`；
- 飞书里被移除/移走的 docx → 本地 `delete`（tombstone），可用 `--no-delete` 关闭；
- 单篇无权限不中断整棵树，记进 `errors`；
- 频率：每篇正文后 sleep 0.25s（飞书 5 次/秒限制）。

**明确没做**：节点级权限同步（`acl` 由 `--acl` 统一给，生产要接权限源）；不是实时同步（手动/定时跑）。

## SSO 角色注入（ACL）

`auth.py` 把身份转换成 ACL 角色；**SSO 角色存在时覆盖请求体里的 `acl`**（客户端不能自己提权）。

| `AUTH_MODE` | 行为 | 适用 |
|---|---|---|
| `none`（默认） | 不校验；`acl` 可来自请求体 | 本地 demo |
| `proxy` | 只信网关注入的 `X-Auth-Roles`；必须 `TRUST_PROXY_HEADERS=1` | **生产推荐**：OIDC 在网关做 |
| `jwt` | 应用自己验 HS256 `Authorization: Bearer`，取 `roles` claim | 无网关的 demo/内网 |

```bash
# 生产：oauth2-proxy / nginx 验完 OIDC，向下游注入角色头
AUTH_MODE=proxy
TRUST_PROXY_HEADERS=1
AUTH_PROXY_HEADER=X-Auth-Roles
```

```bash
# 本地：HS256 JWT
AUTH_MODE=jwt
AUTH_JWT_SECRET=dev-secret
AUTH_ROLES_CLAIM=roles
# token 由 IdP 签发；测试里用 tests/test_auth.py::make_jwt 生成
```

- 缺 token / 签名错 / 过期 / issuer/audience 不符 → **401**；
- `proxy` 模式但没 `TRUST_PROXY_HEADERS=1` → 503（拒绝信任可伪造头）；
- `/kb` 管理接口仍用 `KB_ADMIN_TOKEN`，与 SSO 分开；
- 只实现 HS256；生产用 RS256/OIDC 时让网关验签后注入角色，别让应用直接暴露公网。

## PDF / DOCX 解析（2026-10-23）

| 格式 | 做法 | 依赖 |
|---|---|---|
| `.docx` | 标准库 `zipfile` 解 `word/document.xml`，Heading 转 `##`、表格转 `a \| b` | 无（不装 python-docx/lxml） |
| `.pdf` | 默认 `pypdf` 逐页 `extract_text()`；扫描件/复杂排版可切 Docling / MinerU | `pypdf==6.19.0`；后两者可选 |

行为：

- DOCX 的 `Heading 1/2/3` 会变成 `## 标题`，现有 `chunk_markdown` 直接按标题切块；
- 表格按行拼成 `单元格 | 单元格`，不丢表格文字；
- 扫描版/图片版 PDF **没有文本层** → 默认 `PDF_BACKEND=auto` 会尝试已安装的 Docling/MinerU；
  都没装则 422，提示安装或先转 md；
- PDF 有密码/损坏 → 422，不返回空文本假装成功；
- 不支持的扩展名 → 415。

```powershell
# 上传 docx/pdf（raw body）
curl.exe --data-binary "@制度.docx" "http://127.0.0.1:8000/kb/demo/documents?filename=制度.docx&acl=public"
curl.exe --data-binary "@制度.pdf"  "http://127.0.0.1:8000/kb/demo/documents?filename=制度.pdf&acl=public"
```

测试：`tests/test_parsers.py` **11 passed**（DOCX 标题/表格、PDF 文本、扫描件 422、坏 DOCX、auto 回退、Markdown 切块）。

## 复杂 PDF / 扫描件：Docling / MinerU（2026-10-23）

适配器：[`kb/layout.py`](kb/layout.py)。**不自研 OCR**，只调用本地已安装的后端。

| 后端 | 形态 | 用法 |
|---|---|---|
| Docling（推荐） | **Docker 容器**，`Dockerfile.docling` | `PDF_BACKEND=docling` |
| Docling（本机进程内） | 仅无 WDAC 的机器/测试 | `PDF_BACKEND=docling-local` |
| MinerU | 外部命令 | `PDF_BACKEND=mineru`，或设 `MINERU_CMD`/`MINERU_ARGS` |

```bash
# 构建 Docling 镜像（在仓库根；国内建议用镜像源 base）
docker build -f Dockerfile.docling \
  --build-arg BASE_IMAGE=docker.1panel.live/library/python:3.11-slim \
  -t docling-runner:2.142.1 .

# 默认：先 pypdf；失败再试 Docling/MinerU
PDF_BACKEND=auto
# 强制走 Docker Docling
# PDF_BACKEND=docling
# DOCLING_CMD=docker
# DOCLING_IMAGE=docling-runner:2.142.1
# DOCLING_TIMEOUT=300
# DOCLING_CACHE_VOLUME=docling-models
```

- `DoclingDockerBackend` 一次性 `docker run --rm`：输入/输出各挂临时目录，输入只读；
- 模型缓存放 named volume `docling-models:/root/.cache`，避免每次重下；
- 超时先 terminate，再 `docker kill <唯一 name>`，防孤儿容器；
- 容器入口是 `docker/docling_worker.py`（`DocumentConverter` + `do_ocr=True`），不依赖易变的 CLI 旗标；
- 输出 Markdown 的 `##` 标题会被 `chunk_document` 按标题切块；
- 镜像不存在/超时/空输出 → 422，不静默返回空文本。

**已实测（2026-10-23）**：

| 项 | 结果 |
|---|---|
| 镜像 | `docling-runner:2.142.1`，**2.86 GB**（CPU-only torch） |
| 文本 PDF | Docling 返回 28 字符 |
| 图片版 PDF（pypdf 抽 0 字） | Docling OCR 返回 85 字符：`## SCAN N TEST 2 2026`、`Reimbursement within 30 days...` |
| store 上传 | `upload → load_chunks` 成功，1 chunk，`last_errors=[]` |

容器内访问 HF 需要两个环境变量（适配器已默认传）：

```bash
HF_ENDPOINT=https://hf-mirror.com
HF_HUB_DISABLE_XET=1     # hf-mirror 的 xet CAS 会 401
```

**公开样本实测（2026-10-25）**：

- 样本：上海外国语大学《学生手册（2025）》公开 PDF，取第 9–11 页；
- ★ 原 PDF 有文本层，不是真实扫描件；用 pypdfium2 渲染成图片版 PDF 模拟扫描件
  （没有倾斜/噪点/盖章，真实扫描会更难）；
- pypdf 抽 0 字；Docling OCR **3481 字 / 174.9 秒（3 页，约 58 秒/页，CPU）**；
- 切块 **5 个**，标题 `上海外国语大学简介 / 历史承传 / 办学优势 / 人才培养 / 卓越研究` 都正确；
- 检索：`上海外国语大学创建于哪一年？` 命中简介（11ms）；`上外的校训是什么？` 命中（9ms）；
  `图书馆几点关门？` 拒答（4ms）；
- 识别瑕疵：`StudiesUniversity` 粘连、引号被识别成 `"`、中英文间空格不稳；标题和关键事实没错。

**本机为什么不用进程内 Docling**：Docling `2.142.1` 已装，但加载布局模型时报
`_pava_pybind` **DLL load failed —— 应用程序控制策略已阻止此文件**（WDAC 拦 `.pyd`）。
容器内是 Linux `.so`，不受该策略影响；`docling-local` 仅留给没有 WDAC 的机器。

**边界**：扫描件/双栏/表格版式靠后端，不在仓库里手写 OCR/版面算法；OCR 有字符级误差，关键字段必须人工抽检。

## 下一步（按优先级）

1. **真实扫描件验证**：拿真实扫描件（有倾斜/噪点/盖章）和双栏 PDF 跑 `PDF_BACKEND=docling`，重点看表格、数字、阅读顺序；
2. **每语料独立题集**：用模板出 40+20+10 题，出题人独立、冻结后跑；
3. **定时同步**：用 cron/任务队列每天跑 `feishu_sync.py`，并监控 `errors`/`deleted`。

## 不做

- 不做通用 RAG 平台、50+ 连接器、Trace UI、multi-agent；
- 不把六道把关复制到 store（仍由 `service.RAG._guard` 统一做）。
