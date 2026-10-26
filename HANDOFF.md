# HANDOFF — 企业知识库 RAG（2026-10-25）

> 快照时间：**2026-10-25 19:10（Asia/Shanghai）**
> 仓库：`D:\_review_rag-cs`　·　分支：`main`　·　代码快照 HEAD：**`48d9e6e`**
> 本文/说明书的后续提交：`4635fd3`、`183eab6`；**当前 HEAD 以 `git log -1` 为准**。
> 工作树：只有 `samples/` 未跟踪（本地测试 PDF，见 §6）；代码/文档无未提交改动。
>
> ★ 本文是**交接快照，不是真相源**：数字以 `README.md`、`enterprise-kb.md`、测试输出和
> `git log` 为准；本文与产物冲突时，以产物为准。
> ★ 旧版 `HANDOFF.md`（2026-10-14，校园政策 RAG 主线：限流/agent/旧评测）已归档为
> **`HANDOFF-20261014.md`**；本文覆盖 2026-10-23 之后的企业知识库线。
> `HANDOFF.wb.md` 是另一条 WorkBuddy 线，未改动。

---

## 1. 项目目标

**做什么**：把原来的「校园制度问答 RAG」改造成**可复用的企业知识库服务**。输入是企业的
制度/手册/FAQ/飞书 wiki/扫描 PDF，输出是**带出处条款的答案**，或者**明确拒答**；每个语料
（namespace）有自己的上传、ACL、质检和评测。

**给谁用**：

1. **企业 HR / 行政 / 合规 / 客服**：内部制度、报销、考勤、员工手册问答；要求“答错不如拒答”，
   且不同角色只能看自己有权看的条款；
2. **求职作品集**：面试官能看到会跑的服务、11 个接口、真实 OCR/检索/评测产物；
3. **作者本人**：继续做“企业级”的工程样本，不是给真实学生用的线上服务。

**当前语料**：`docs/` 里仍是 9 份校园制度（286 条原始材料 → 263 块入库），作为默认
`default` 语料；企业语料放 `kb_data/<namespace>/`，通过 `/kb/{namespace}/documents` 上传。

**明确不做什么**：不做通用 RAG 平台、50+ 连接器、Trace UI、multi-agent、多租户 SaaS。
“企业级”在这里指：**权限、上下文、评测、可运维**，不是功能数量。

---

## 2. 当前进度

### 2.1 服务层（原有，仍可用）

`service.py` 现有 **11 个端点**（README 有清单，pre-commit 会校验）：

- `POST /chat`、`POST /chat/stream`、`POST /search`
- `GET /health`、`GET /limits`、`GET /guard-report`、`GET /`
- `GET/POST /kb/{namespace}/documents`、`DELETE /kb/{namespace}/documents/{doc_id}`
- `GET /kb/{namespace}/eval`

检索 = fastembed `bge-small-zh-v1.5`（512 维）+ BM25（jieba）+ RRF；精排 = DeepSeek
listwise（0–10，<5 硬短路拒答，`VEC_REJECT_THRESHOLD=0.55`）；生成 = DeepSeek；答案带
`sources`（doc/clause/text/score）。限流/配额在 `limits.py`，会话在 `session.py`。

### 2.2 企业知识库层（2026-10-23 之后新增）

| 能力 | 状态 | 证据/文件 |
|---|---|---|
| namespace 上传/列表/软删除 | ✅ | `kb/store.py`、`service.py` 的 `/kb` 路由 |
| DocumentMeta（namespace/doc_id/title/source/version/updated_at/acl/content_hash/tombstone） | ✅ | `kb/source.py` |
| Source 协议 + `UploadSource` | ✅ | `kb/source.py` |
| 块级 ACL：检索前过滤 + 精排后复检 | ✅ | `service.py::_acl_allows/_final_acl_filter` |
| SSO 角色注入（none/proxy/jwt） | ✅ | `auth.py`、`service.py::_request_roles` |
| 每语料 smoke 质检 | ✅ | `kb/eval.py`、`GET /kb/{ns}/eval` |
| 独立评测 runner + 模板 | ✅ | `kb/eval_runner.py`、`scripts/run_kb_eval.py`、`evalset/enterprise_eval_template.md` |
| 飞书 wiki 只读连接器 | ✅ 代码 / ❌ 真实同步 | `kb/feishu.py`、`scripts/feishu_sync.py`；被应用 scope 卡住（§4） |
| PDF/DOCX 解析 | ✅ | `kb/source.py`：PDF 用 pypdf；DOCX 用标准库 zip+XML |
| Docling OCR（扫描件/复杂 PDF） | ✅ | `kb/layout.py::DoclingDockerBackend`、`Dockerfile.docling`、`docker/docling_worker.py` |
| MinerU 外部命令后端 | ✅ 代码 / 未实测 | `kb/layout.py::MinerUBackend` |
| 上下文层 v1（八层/字符预算/ContextTrace） | ✅ 只对带 namespace 的 `/chat`·`/chat/stream` | `kb/context.py`、`service.py::_plan_context`；实测见 `experiments/_context-v1-acceptance.json` |
| 失败归因（6 桶：retrieval_miss/rank_shift/budget_cut/gen_uncited/acl_leak/stale_version） | ✅ | `kb/attribution.py`；trace 里新增 `candidates`（没有它分不开 miss 与 rank_shift） |
| 评测指标（context_recall/precision、nDCG@3、budget_cut_rate、p95） | ✅ | `kb/eval_metrics.py`；nDCG 用 `ir-measures`（TREC 口径，不自己写） |
| 场景评测 runner + campus 基线 | ✅ campus（20 题）/ ⏳ hr·compliance·support 待真实语料 | `scripts/run_scenario_eval.py`、`evalset/report-scenario-campus.json`、`evalset/scenarios/README.md` |
| gold ↔ 语料联动检查（扫 0 条报红） | ✅ | `scripts/check_eval_gold.py` |
| 评测依赖单列（不污染 runtime） | ✅ | `requirements-eval.txt`（`ir-measures` + `pytrec-eval-terrier`；**CI 不装**：Linux 无 cp314 wheel） |
| 引用锚点 + 校验（题号编号 / validity / 未标注段落） | ✅ 只对 namespace 路径 | `kb/citation.py`、`service.py::build_answer_prompt`（双模板）+ `_attach_citation`；流式在 `citation` 事件里 |
| 引用判据的变异体 | ✅ | `scripts/mutate_citation.py`：编号错位 / 越界不标记，**2/2 变红 + 字节还原** |
| 块级规则压缩（近重复去重 / 噪声行） | ✅ 代码，**默认 off**（`CONTEXT_COMPRESS=rule` 才开） | `kb/compress.py`；本语料实测 20 题触发 **0** 次 ⇒ 保持默认关（见 §2.3） |
| 压缩判据的变异体 | ✅ | `scripts/mutate_compress.py`：阈值失效 / 正则吞正文，**2/2 变红 + 字节还原** |
| as-of 版本裁决（effective_date/status + event_time） | ✅ 端到端可用（语料带生效日时） | `kb/versioning.py`；字段贯通 loader·store·manifest；六道把关对「版本兄弟」放行（`service.py::_is_version_sibling`，数据触发） |
| as-of 判据的变异体 | ✅ | `scripts/mutate_versioning.py`：忽略 event_time / 不丢废止版，**2/2 变红** |
| 审计落盘（只存决策与定位，不存原文/正文） | ✅ 只对 namespace 请求 | `kb/audit.py`、`scripts/prune_audit.py`；`AUDIT_ENABLED/AUDIT_LOG_PATH/AUDIT_RETENTION_DAYS` |
| 答案标注适用版本（as-of 时） | ✅ 数据触发（块带 `as_of_note` 才加） | `service.py::build_answer_prompt` |
| 审计判据的变异体 | ✅ | `scripts/mutate_audit.py`：写原文 / 清理删脏行，**2/2 变红** |
| 评测内核（可复用） | ✅ | `kb/evalkit/`：cases（schema+校验+套件解析）/ metrics（**指标注册表**）/ report（`schema_version`+指标说明书）/ transport（http·recorded·inproc） |
| 报告 schema | ✅ `request-eval/1` | `metric_specs` 自带 unit 与 missing 说明；加指标不改 runner |
| 内核判据的变异体 | ✅ | `scripts/mutate_evalkit.py`：缺失补 0 / 不查重复 id，**2/2 变红 + 字节还原** |
| 会话轨迹的键（A） | ✅ | trace / 审计带 `session_id` + `turn_index`；`rewritten_query` 只进 trace（审计保持无原文） |
| 会话级评测（B） | ✅ 最小版（只测改写这条跨轮链路） | `kb/evalkit/session.py`、`scripts/run_session_eval.py`、`evalset/scenarios/sessions/campus-followups.jsonl`（10 会话 / 22 轮） |
| 会话判据的变异体 | ✅ | `scripts/mutate_session.py`：指代补全恒 False / 拿掉 session_id，**2/2 变红** |
| 连接器内核（S1） | ✅ | `kb/connectors/`：`Connector` 协议 + **增量同步引擎**（哈希∪ACL∪元数据比较）；删除只作用于 `owns()` 的文档 |
| fixture 连接器（离线验收） | ✅ | `kb/connectors/fixture.py`；`evalset/fixtures/connector_demo/` 可直接跑 |
| 飞书连接器适配 | ✅ 代码 / ⏳ 真实同步等 scope | `kb/connectors/feishu.py`；`FeishuWikiSource.sync` 已**委托**同一引擎（不再两套实现） |
| 飞书报错 → 可操作清单 | ✅ | `kb/feishu.py::explain_error`：列出缺的 scope + 发版 + 加入知识空间；CLI 失败时直接打印 |
| 多租户 S2-1 | ✅ 身份 + 隔离 + 审计导出 | `tenants.py`（key 只存 sha256）；`MULTI_TENANT=1` 时 `/chat` `/search` 校验 key 与 namespace；审计带 `tenant`；`/admin/tenants`、`/admin/audit` |
| 多租户判据的变异体 | ✅ | `scripts/mutate_tenants.py`：`allows()` 恒 True / key 不比对哈希，**2/2 变红 + 字节还原** |
| 多租户 S2-2 | ✅ 配额按租户 + 写接口鉴权 | 配额键 `tenant:{id}`（A 打满不影响 B）；写接口：管理员 token 可跨租户，否则必须有该 namespace 的租户 key |
| 薄控制台 S2-3 | ✅ 静态页（不引框架） | `GET /console` → `static/console.html`：租户 / 语料 / 最近审计；数据只靠 `/admin/*` + `/kb/*`，渲染只用 `textContent` |
| 控制台判据的变异体 | ✅ | `scripts/mutate_console.py`：页面加 `.innerHTML` / 改成不存在的端点，**2/2 变红** |
| 私有化打包 S2-4 | ✅ | compose 增 `kb-data` 卷（语料/审计/租户表持久化）、S2 环境变量、`preflight` profile；`DEPLOY.md` 补私有化章节（含 Docling 挂 socket 的安全提醒） |
| 部署自检 | ✅ 可在坏情况下报红 | `scripts/preflight.py`：key / kb_data 可写 / 审计 / 租户表 / 模型缓存；CLI 退出码 1 |
| 开通租户 CLI | ✅ | `scripts/create_tenant.py`：明文 key **只打印一次**，落盘只有 sha256 |
| 自检判据的变异体 | ✅ | `scripts/mutate_preflight.py`：删 key 检查 / fail 也返回 0，**2/2 变红** |
| 多租户的**未做部分** | ⏳ 明确未做 | 计费；配额数值仍是全局一套（不分租户自定义） |
| 未实现层的状态标注 | ✅ | `layers[*].status`（implemented/reserved）+ `reason` + `reserved_layers`；`reserved`+`used_chars=0` 只能读成「没实现」 |
| 「想实现记忆必须显式改测试」 | ✅ | `test_implementing_memory_requires_a_deliberate_test_change` 锁住 `implemented_layers==["retrieved"]` |
| S1 判据的变异体 | ✅ | `scripts/mutate_connectors.py`：忽略 ACL 变化 / 清单删“发版”，**2/2 变红 + 字节还原** |

### 2.3 实测结果（可复现）

**测试**（2026-10-25，最后一次全量）：

```text
356 passed, 27 skipped
```

命令见 §7。跳过的 27 项是需要 Redis/外部服务的旧测试。
（2026-10-25 加 Context Layer 前是 196 passed；`tests/test_context.py` 20 条、
`tests/test_eval_attribution.py` 17 条。Context Layer 的关键判据已被变异体证明会响：
`scripts/mutate_context_layer.py` 跑完是 `2/2 变红 + service.py 字节还原一致`。）

**场景评测基线（campus，n=20，2026-10-25）** —— 产物 `evalset/report-scenario-campus.json`：

```text
应答 14/15 = 93.3%  Wilson [0.7018, 0.9881]
拒答  5/5  = 100%   Wilson [0.5655, 1.0]
context_recall 0.9333 / context_precision 0.9333 / nDCG@3 0.9333
budget_cut_rate 0 / p95 4794ms
归因：{"ok": 14, "refuse_ok": 5, "retrieval_miss": 1}
```

失败样本是 q2（"违纪了的话，学校都有哪些处分等级啊？"）：gold 是《学生纪律处分管理规定》
第二章-第四条，实际捞到的是第二十四条/第五条 —— **释义问法的检索遗漏**，与预算无关
（`budget_cut_rate=0`，说明 1840 的额度在这批题上没裁块）。

**引用锚点（批 2，同一批 20 题，配对对照）**：

```text
改前（无引用，evalset/report-scenario-campus.json）：应答 14/15、p95 4794ms
改后（v2 模板 + [n] 编号，evalset/report-scenario-campus-citation.json）：
  应答 14/15、citation_validity 1.0（0 条越界编号）、未标注段落/答案 0.2、p95 4683ms
```

★ 这不是"提升"，是"没变差"：每臂只跑 1 次、n=20，Wilson 区间很宽（[0.70, 0.99]），
  只能说明新模板没把应答率打下去，同时答案开始带可校验的编号。
★ 度量本身踩过两次坑（都已修 + 有用例守着）：
  ① 按**句子**数"未标注"⇒ 模型是段末标 `[1]`，把同段已被覆盖的句子全算成没标，假高 3~5 倍；
  ② 拒答白名单漏了变体（"无法**完整**回答"不含"无法回答"）⇒ 又把说明性段落算成没出处。

**块级压缩（批 3，同一批 20 题，配对对照）**

```text
先量语料（271 块）：块长中位 75 字 / 均值 118 / 最大 1870；
页眉页码噪声行 **0 处**；近重复对（3-gram Jaccard≥0.8）**5 对**；超额度(1840) **1 块**
压前（CONTEXT_COMPRESS=off）：应答 14/15、recall 0.9333、uncited 0.2667、p95 3558ms
压后（CONTEXT_COMPRESS=rule）：应答 14/15、recall 0.9333、uncited 0.2667、p95 3692ms
压后报告里的触发计数：启用 15 题、**去重丢弃 0**
```

★ 结论不是"压缩没用"，而是**这套语料上没有可压的东西**：271 块里只有 5 对近重复、0 处噪声行，而这 5 对没有一对同时进过同一题的 top-k（触发 0/15）。所以默认保持 **off**；
  等真实企业语料（同一制度的多个版本、PDF 页眉）进来再打开复测。
★ **明确不做**：句子级抽取（中位 75 字的块抽完只剩半句，风险远大于收益）；
  超长块按句拆段（只有 1 块 1870 字，拆了会改变出处与引用编号语义）—— 记在 §5。

**as-of 版本裁决（批 4，受控验收，语料 synthetic）**

```text
① 先撞墙（产物 experiments/_asof-acceptance.json）：
   两版都进 manifest，但 /search 只返回 1 条；/guard-report 里新版被拦 reason="[asof_demo] 近似重复"
   ⇒ 运行时过滤对，端到端拿不到两版（六道把关的近似重复关跑在版本冲突关之前）
② 修好之后（产物 experiments/_asof-acceptance-fixed.json）：
   索引里该条款 2 块、被拦 0、/search 返回 2 条
   event_time=2025-06-01 → kept=1 dropped_by_reason={"not_yet_effective":1} → 答案 400 元
   不传 event_time       → kept=1 dropped_by_reason={"superseded":1}     → 答案 600 元
```

★ 修法是**数据触发**的：只有块带 `effective_date` 才把两块当"版本兄弟"（去重放行、入库不裁决），
  没有生效日的 docs/ 校规走原逻辑 ⇒ 263 留 / 23 拦那些数字一个没变（老守卫测试全绿）。
★ 也就是说：**as-of 能不能用，取决于语料有没有写生效日**。没写就诚实地按现行处理，不猜。

**真实结构制度语料（policy，2026-10-26）** —— 3 篇合成制度（差旅 2026/2024 两版 + 考勤请假），
20 题（17 应答 + 3 必须拒答）+ 3 题 as-of 对照

```text
policy（event 2026-06-01，n=20）：应答 17/17（Wilson [0.82, 1.0]）、拒答 3/3
  recall/precision/nDCG@3 = 1.0；引用合法率 1.0；未标注段落 0.0；budget_cut 0；p95 ≈ 3.8s
as-of（event 2025-06-01，n=3）：3/3；答案带「按 2025-06-01 时点有效的版本」，值 500 元 / 60 日 / 100 元
不加 event_time：答「两套限额互相冲突，无法确定」——版本没定就不替用户选（刻意行为）
```

★ 这轮抓到第二个「指标判错对象」：库外题的拒答，旧判据用 `knowledge_hit` 代理「有没有答」，
  把「引用了不充分资料 + 明说不知道」判成「意外作答」（20 题里误判 2 道，拒答显示 1/3）。
  改成**按答案文本**判（`looks_like_refusal`，标记含「不知道/资料中没有/未提供/无法据此」），
  拒答变成 3/3。与之前 `rewrite_hit` 那次是同一类错误：**判据测错了对象**。

**S1 连接器与增量同步（2026-10-26）** —— 离线验收（fixture，`evalset/fixtures/connector_demo/`）

```text
首次同步：added=2；第二次：unchanged=2（只比哈希，不重复写）
改正文：updated=1（只动那一篇）；改 ACL：updated=1 且 changed_reasons 含 acl
改 ACL 后**不重建索引**：chunk.acl 已是 [hr]，_acl_allows(acl, None) = False
删一行 manifest：deleted=1，但**同 namespace 的人工上传语料仍在**（owns() 保护）
dry-run：added=1 但库里 0 条
```
★ 关键设计：变更判据 = **内容哈希 ∪ ACL ∪ 元数据**（不是时间戳）；
  删除只看 `owns()`（按 source 前缀）—— 否则连接器一轮同步会把同 namespace 的人工语料清掉。
★ 兼容：`FeishuWikiSource.sync` 改为**委托**同一引擎，老测试 6 条全绿（skipped 语义按老字段映射）。

**会话级评测（批 7，A+B，2026-10-26）** —— 产物 `evalset/report-sessions-campus-followups.json`

```text
10 个会话 / 22 轮：整个会话全对 8/10 = 80%（Wilson [0.49, 0.94]）、逐轮正确率 0.9091
改写：用了 12 轮，其中真正省字追问 4 轮，指代补全率 1.0；重复提问率 0
失败：s10#1（老熟人 q2 的释义问法检索遗漏）、s09#3（「学费多少」这轮被答了 —— 跨轮拒答不一致）
```

★ 一次运行 ≠ 稳定结论：10 个会话、单次采样；两次跑 s09 的拒答表现就不同。
★ 指标返工过一次：第一版 `rewrite_hit` 要求改写里出现 gold 条款号/文档名，
  实测 12 轮合理改写（「那一个礼拜呢？」→「请假一周以内由谁审批？」）**全判 0** ——
  因为查询改写产出的是问句、不是引用。改成 `rewrite_resolved`（省掉的上下文实词有没有被补回）、
  并把拒答题的主题切换排除出分母，才得到 4/4。

**评测内核重构（批 6，行为保持）**：同一批 20 题、同一个服务，新旧 runner 的 8 个指标
**逐项完全一致**（answer 14/15、recall/precision 0.9333、nDCG@3 0.9333、budget_cut 0、
citation_validity 1.0、未标注段落 0.2）。新报告：`evalset/report-scenario-campus-evalkit.json`
（`schema_version=request-eval/1`，12 个指标都自带 missing 说明）。

**审计 + 版本措辞（批 5，受控验收，语料 synthetic）** —— 产物 `experiments/_audit-sample.jsonl`

```text
/chat?namespace=audit_demo&event_time=2025-06-01
  答案开头："按 2025-06-01 时点有效的版本：…… 400 元 ……[1]"
  as_of：dropped_by_reason={"not_yet_effective":1}
  审计文件：1 行（docs/ 旧路径那次请求没有新增行）
  字段：answer_chars / as_of / citation / decision / knowledge_hit / n_blocks / namespace /
        question_sha256 / question_chars / roles / route / sources / took_ms / trace_id …
  原文在审计里吗：False（只存 sha256 + 字数）
```

**Docling 容器**：

- 镜像 `docling-runner:2.142.1`，**2.86 GB**（CPU-only torch；PyPI 默认 torch 会拖 CUDA 包）；
- 文本 PDF：28 字符；
- 图片版 PDF（pypdf 抽 0 字）：OCR 85 字符，能读出 `SCAN TEST 2026` 和
  `Reimbursement within 30 days`；
- 公开样本（上外《学生手册 2025》第 9–11 页，**原 PDF 有文本层，渲染成图片版模拟扫描件**）：
  pypdf 0 字 → Docling OCR **3,481 字 / 174.9 秒/3 页（约 58 秒/页，CPU）**；
  切 5 块，标题正确；检索 2 题命中、1 题拒答（4–11ms）。
  注意：**不是真实扫描仪原件**，没有倾斜/噪点/盖章。

样本在 `samples/sisu-handbook-2025.pdf` 与 `samples/sisu-handbook-scan.pdf`，未入库。

### 2.4 停在哪

- **上下文层只有 v1**：`kb/context.py` + `service.py::_plan_context` 已接（八层声明、
  chars 预算、ContextTrace、整块丢弃、拒答也带 trace）。**v2 没做**：prompt 模板化、
  压缩（摘句/去重）、memory/tool 层真正注入、未用完额度不借用（v1 是固定 cap）。
- **评测层只有 v1**：场景 runner + 六桶归因 + 指标（recall/precision/nDCG/budget_cut/p95）
  已接，但 **hr/compliance/support 三个场景没有语料也没有题集**（不编数据）；
  `citation_validity` 只判"编号合法"，ALCE 口径的"是否被所引段落支持"要 NLI，**没做**；
  `stale_version` 桶是空钩子（等 as-of）。
- **压缩只有 v1 且默认关**：去重/噪声行已实现、变异体 2/2 变红，但本语料触发 0 次；
  句子级抽取与超长块拆段**刻意没做**（见 §2.3 的取舍）。
- **版本/as-of 已端到端可用**，但只在**语料带 `effective_date`** 时成立；
  答案正文还不会写"按 X 年 X 月版本"（模板没改，见 §5）。
- **多租户到 S2-2**：身份/隔离/审计导出/按租户配额/写鉴权都有；
  但**配额数值不分租户自定义**，控制台与私有化打包还没做。
- **真实语料**：现有语料是「校规（真实但非企业）+ 制度（合成但结构真实）」，
  **还没有一份真实公司的内部制度**；20 题验收是在合成语料上做的。
- **飞书接入**：代码就绪（连接器/增量/ACL/报错清单），但**租户策略卡住**：
  租户 token 不能自举进知识空间（131006），且公开空间不允许加成员（131101）；
  UI 与本版本 API 都不支持把应用加为空间成员 —— 需要管理员在管理后台开权限。
- **评测有请求级 + 会话级最小版**：会话级只测「改写有没有补全指代 + 逐轮对不对」；
  **工具/Agent 轨迹级没做**（没有多步工具，硬造指标是自欺，触发条件见 §5）。
- **审计只有 JSONL**：单机追加 + 独立清理脚本；没有查询接口/审计库/签名链，也还没有对接 SIEM。
- **飞书真实同步没跑通**：应用没有 wiki scope（§4）。
- **独立 70 题集没出**：只有 40+20+10 的模板和样例。
- **真实扫描件/双栏/表格没验**：目前只有模拟扫描件。
- **没有公网部署**：只有本地 `uvicorn`；登录、限额、域名、HTTPS 还没做。

---

## 3. 关键决定（每条一行：选了什么 + 为什么）

1. **保留原 RAG 核心，只加企业层，不重写** —— 检索/精排/拒答/六道入库把关已有测试，重写会丢判据。
2. **namespace + DocumentMeta + Source 协议** —— 多语料隔离，且解析/存储/索引解耦，换飞书/上传不用改检索。
3. **默认 `namespace=None` 只查 `default`，企业语料必须显式 namespace** —— 防止默认路径把企业私有块带出来。
4. **ACL 两段过滤（检索前 + 精排后）** —— 只在检索前过滤，精排可能把无权块捞回来；只在最后过滤，候选分数/日志已泄漏。
5. **SSO 角色覆盖请求体 `acl`；proxy 头必须配 `TRUST_PROXY_HEADERS=1`** —— 客户端自填 acl 在演示可以，生产必须由网关注入身份。
6. **`AUTH_MODE=none` 只做本地 demo 默认** —— 不破坏旧测试/演示；生产强制 proxy 或 jwt。
7. **KnowledgeStore 用 manifest + tombstone + content_hash，不引数据库** —— 当前规模单机文件够用，软删除保留可审计痕迹。
8. **Docling 走 Docker，而不是在 Windows 进程内 import** —— 本机 WDAC 拦 `_pava_pybind` 的 `.pyd`；容器内 Linux `.so` 不受限。
9. **DOCX 用标准库 zip+XML，不装 python-docx/lxml** —— 避免 lxml DLL 被应用控制策略拦；DOCX 本质就是 zip+XML。
10. **PDF 默认 pypdf，扫描件才回退 Docling/MinerU；`auto` 失败继续试下一后端** —— 文本层 PDF 快且稳，OCR 慢且贵，不该默认全走 OCR。
11. **Docling 镜像用 CPU-only torch + 国内 pip/HF 镜像 + `HF_HUB_DISABLE_XET=1`** —— PyPI 默认 torch 会拉数 GB CUDA；hf-mirror 的 xet CAS 会 401。
12. **评测分“smoke”和“独立题集”，不把自动质检冒充独立评测** —— smoke 只能证明链路没断；独立集必须出题人隔离、冻结后跑。

---

## 4. 踩过的坑

| 问题 | 现象/原因 | 处理 | 状态 |
|---|---|---|---|
| 文档放错目录 | 把 `enterprise-kb.md` 放进 `docs/`，被当成 KB 语料，loader 测试计数变了 | 移到仓库根；`docs/` 只放语料 | ✅ 已解决 |
| README 端点数过期 | 新增 `/kb` 路由后 README 仍写 7/10 个，pre-commit 报错 | 更新为 **11**；`scripts/check_readme_numbers.py` 强制校验 | ✅ 已解决 |
| 飞书 token 取错层级 | 以为 `tenant_access_token` 在 `data` 下，实际在顶层 | `_request(..., raw=True)` 取顶层 | ✅ 已解决 |
| 飞书知识空间成员 | `131002 invalid member_type` / `invalid member_role` / `131101 public wiki space can't create member` | 实测有效组合是 **`member_type=appid` + `member_role=member`**；但**公开空间不能加任何成员**（先改回私有），且 tenant 身份无权给自己加成员（131006）→ 需管理员 | **未解决（租户策略）** |
| 未实现的层写成 `used_chars: 0` | 读报告的人以为是「这轮没用记忆」，实际是「记忆没做」 | 每层加 `status`/`reason`；`reserved` 只能读成未实现 | **已修复** |
| 改 `.env` 用记事本整体覆盖 | 服务全部 500：`DEEPSEEK_API_KEY must be set` | 改 `.env` 只**追加**、不要整篇粘贴；本次已从 Windows 用户环境变量恢复 | **已修复** |
| 飞书 wiki 无权限 | `99991672 Access denied ...` | 已把报错翻译成清单（缺哪些 scope + **发版** + 加入知识空间），CLI 失败直接打印；scope 仍需你在后台开 | **未解决（需用户操作，但不再靠猜）** |
| Docling 进程内被 WDAC 拦 | `_pava_pybind` DLL load failed，应用程序控制策略阻止 | 改用 Docker 容器；`docling-local` 仅留给无 WDAC 机器 | ✅ 绕过（非根治） |
| Docker 拉取慢 / 镜像过大 | Docker Hub 慢；PyPI torch 拉 CUDA 依赖数 GB | base 用镜像源；CPU-only torch；镜像 2.86GB | ✅ 已解决 |
| 容器访问 HF 失败 | 直连 HF connection refused；hf-mirror 的 xet CAS 401 | 传 `HF_ENDPOINT=https://hf-mirror.com` + `HF_HUB_DISABLE_XET=1` | ✅ 已解决 |
| `docker` 不在 PATH | Python 子进程找不到 `docker`，服务里直接 422 | `kb/layout.py::_resolve_cmd` 找 Docker Desktop bin | ✅ 已解决 |
| Docling 安装后 parser 测试变红 | `auto` 先试 Docling，DLL 错误覆盖了原始“无文本”原因 | 每个后端失败都记录，继续试下一个；最后聚合原因 | ✅ 已解决 |
| `load_chunks` 静默丢文件 | manifest 有记录但文件缺失时返回空列表 | 记进 `store.last_errors`，API/日志能看见 | ✅ 已解决 |
| 企业短条目被长度闸拦 | `MIN_LEN=15` 把 14 字以内的 FAQ 拦掉 | 企业路径用 `ENTERPRISE_MIN_LEN=6` | ✅ 已解决 |
| `_StubRag` 签名不兼容 | 测试桩只有 `search(q,k=5)`，新参数会 TypeError | `_search_for_request` 无 namespace/acl 时走旧签名 | ✅ 已解决 |
| 真实扫描件未验 | 目前只有“文本 PDF 渲染成图片”的模拟扫描件 | 需要真实扫描仪 PDF（倾斜/噪点/盖章） | **未解决** |
| 双栏/表格未验 | SISU 样本是单栏正文，没有表格阅读顺序测试 | 需要双栏论文/年报或带表格制度 | **未解决** |
| OCR 速度慢 | CPU 约 58 秒/页；长文档会拖死同步上传 | 后续做批处理/常驻服务/GPU；当前仅验收 | **未解决（性能）** |
| 多版本被"近似重复"关吃掉 | 同一条款的新旧两版文本几乎一样（只差数字），入库时新版被判近似重复；而它跑在"版本冲突"关**之前** ⇒ as-of 永远只看到一版 | 经用户拍板后改成**版本感知**：`_is_version_sibling`（带 effective_date 且版本/生效日不同 ⇒ 去重放行、入库不裁决），docs/ 无生效日 ⇒ 行为不变；证据 `_asof-acceptance-fixed.json` | ✅ 已解决 |
| `kb_data/` 有残留 namespace ⇒ 全量测试红 | 服务启动会把企业 namespace 一起索引，`test_guard` 断言的是 `docs/` 语料条数（263/23）；我跑完 campus 评测忘了清 `campus_eval`，一下红了 4 条 | 跑完评测/验收后删掉 `kb_data/<ns>`；**这不是代码 bug**，但下次别把它误读成回归 | ✅ 已知（靠记得清） |

---

## 5. 下次从哪接（按优先级）

### P0：Context Layer v1 —— ✅ 已完成（2026-10-25）

`kb/context.py` + `service.py::_plan_context`，**不破坏旧路径**的最小版：

- 分层：System / Policy / History / Task / Memory / Retrieved / Tool / Output（八层都在 trace 里，
  v1 只有 retrieved 真的占预算，`budget.enabled_layers` 明说这一点）；
- 预算：按 chars 估算（不假装是 token）。默认 `CONTEXT_BUDGET_CHARS=4000`、
  `CONTEXT_RETRIEVED_RATIO=0.46`（⇒ 检索额度 1840）、`CONTEXT_OUTPUT_RESERVE_CHARS=400`；
  八层额度之和恒等于总预算；env 坏值退回默认并记进 `budget.env_ignored`；
- `ContextTrace`：`trace_id`、blocks（source/clause/chars/included/reason）、excluded、degraded、budget；
- 只对**带 namespace 的 `/chat` / `/chat/stream`** 启用；`docs/` 旧路径 `context=None`
  （流式连 `context` 这个键都不出现），prompt 用 golden 字面量逐字节对照；
- 超预算**整块丢**（reason=budget）/ 单块超上限丢（reason=oversize）；全放不下 ⇒
  `degraded.retrieval=no_block_fits`、`sources=[]`、`knowledge_hit=false`，**不零上下文硬生成**；
- 验收：3 个真实问题返回 trace，产物 `experiments/_context-v1-acceptance.json`
  （第 1 题实测 `used=1187/1840`，第 3 块 681 chars 因放不下被整块丢弃）。
  复现：服务在跑的前提下 `D:\Python-project\.venv\Scripts\python.exe scripts\ctx_acceptance.py`。

**v2 还没做**（别当成顺手活）：prompt 模板化、压缩（摘句/去重）、memory/tool 层真正注入、
预算敏感性实验。设计稿在 `D:\dsh-work\_上下文工程-清单-20261023-v2.md`，路线图在
`D:\dsh-work\_企业知识库-落地清单-20261023.md`。

### P0.5：评测自进化层 v1 —— ✅ 已完成（2026-10-25）

- `kb/attribution.py`：六桶归因（`retrieval_miss / rank_shift / budget_cut / gen_uncited /
  acl_leak / stale_version`）；**依据是 trace 新增的 `candidates`** —— 没有它，
  "没捞到"和"捞到但排第 4"分不开，而归因错了后面所有调优都会打偏。
- `kb/eval_metrics.py`：`context_recall / context_precision / nDCG@3 / budget_cut_rate / p95`；
  nDCG 用 `ir-measures`（TREC 口径），Wilson 区间复用 `kb/eval_runner.wilson_interval`。
- `scripts/run_scenario_eval.py`：按场景跑题集 → 指标 + 逐题归因 → JSON 报告；
  `--responses` 可离线复算（评测逻辑本身进单测，不联网）。
- `scripts/check_eval_gold.py`：gold ↔ 语料联动检查，**扫 0 条报红**。
- `evalset/scenarios/README.md`：场景题集格式与出题纪律；campus 直接复用老题集（不复制 data）。
- 基线见 §2.3；campus 唯一失败是 q2 的**释义问法检索遗漏**（不是预算）。

**批 2（引用锚点）✅ 已完成（2026-10-25）**：v2 模板只对 namespace 生效（docs/ 的 golden
prompt 逐字节不变），答案带 `[n]`，`context.citation` 报 validity/未标注段落/越界编号；
流式新增 `citation` 事件（只有 namespace 才有）。

**批 3（块级压缩）✅ 已完成（2026-10-26）**，但**默认关**：本语料触发 0 次，
打开它只是为了等真实语料（多版本制度/PDF 页眉）时能直接复测。

**批 4（as-of 版本裁决）✅ 已完成（2026-10-26，走的是方案 a）**：运行时裁决 + 数据层字段
+ 六道把关版本感知（数据触发，docs/ 数字不变）；端到端证据见 §2.3。
18 条用例、变异体 **3/3 变红**。

**批 5（审计 + 版本措辞）✅ 已完成（2026-10-26）**：每个 namespace 请求写一行 JSONL
（问题只存 sha256、块只存 doc/clause），保留期清理走 `scripts/prune_audit.py`；
as-of 提问时答案开头会写"按 YYYY-MM-DD 时点有效的版本"。变异体 2/2 变红。

**批 6（评测内核可复用化）✅ 已完成（2026-10-26）**：`kb/evalkit/` 四件套 +
`schema_version` 报告 + 指标注册表 + 三种 transport；`scripts/run_scenario_eval.py`
退化成薄 CLI；旧 `kb/eval_metrics` 只留兼容转发。**8/8 指标与重构前逐项一致**。

**批 7（会话轨迹 A+B）✅ 已完成（2026-10-26）**：会话键进 trace/审计；
会话级套件 + runner 复用 evalkit；指标 6 个（含 `rewrite_resolved_rate`）。

**S1（连接器与增量同步）✅ 已完成（2026-10-26）**；`FeishuWikiSource` 已并入同一引擎。

**policy 验收 ✅（2026-10-26）**：合成但结构真实的制度语料，17/17 + 3/3 + as-of 3/3。

**下一步（按 ROI）**：
1. **一份真实公司制度**（你能拿到的任何 HR/合规文件，敏感信息可替换）→ 重跑同一套 20 题；
2. 飞书：等管理员在管理后台开通「应用访问云文档/知识库」；或改走 user_access_token 桥接（不推荐长期）；
3. ~~memory 层「恒 0」假指标~~ ✅ 已修（`status`/`reason` + 锁定测试，见 §2.2）；
4. 成本/延迟：每问 3 次 LLM 调用、p95 ≈ 3.8s。

**S2-1 多租户 ✅（2026-10-26）**：`MULTI_TENANT=1` 时 `/chat` `/search` 需要 `X-Api-Key`，
key 只存 sha256、租户限 namespace 前缀、审计带 `tenant`、两个只读管理接口。
默认关闭 ⇒ 既有部署/测试/脚本行为不变。

**S2-2 ✅（2026-10-26）**：配额按租户键计（`tenant:{id}`）；写接口先试管理员 token、
否则按租户 key 校验 namespace。变异体扩到 4 条（隔离/key/写鉴权/配额），**4/4 变红**。

**S2-3 ✅（2026-10-26）**：薄控制台 `/console`（静态页，租户/语料/审计三块），
只调已有 API；两条判据（只用已知端点、禁 `.innerHTML`）都有变异体守着。

**S2-4 ✅（2026-10-26）**：私有化单机部署收口 —— `kb-data` 卷、S2 环境变量、
`docker compose run --rm preflight`、`DEPLOY.md` 私有化章节、`create_tenant.py`。
**S2 全部完成**（S2-1~S2-4）。

**下一步候选**：① 真实公司制度 20 题；② 配额数值按租户自定义；③ 计费/审计查询接口。

**S2 待确认约束**：逻辑租户（前缀+配额+审计视图）/ 薄控制台 / compose profile。
**S3（运营闭环）**：反馈→归因→候选题集 + 健康度周报。

**仍按 ROI 排的其它项**：真实语料 20 题（等你给文档）；
成本/延迟；部署硬化。

### P1：真实语料 + 20 题小型验收（与 P0 并行/紧随）

- 3–5 份真实 PDF：至少 1 份扫描件、1 份双栏/表格；
- `PDF_BACKEND=docling` 上传 → OCR → 切块 → 检索；
- 20 题：12 应答 + 5 拒答 + 3 越权；记录命中率、拒答率、p95、OCR 失败点；
- 修 top 2 问题，重跑；产出一页报告，不要先做 70 题。

### P2：飞书真实同步（等用户 scope）

用户加 `wiki:wiki:readonly` / `docx:document:readonly`、发布版本、把应用加入知识空间后：

```powershell
$env:FEISHU_APP_ID="cli_xxx"; $env:FEISHU_APP_SECRET="xxx"
D:\Python-project\.venv\Scripts\python.exe scripts\feishu_sync.py --list-spaces
D:\Python-project\.venv\Scripts\python.exe scripts\feishu_sync.py --space-id <id> --namespace feishu_demo --acl public
```

先 `--dry-run`，再真同步；检查 `errors/deleted/unchanged`。

### 再往后（不要现在做）

版本/as-of 查询 → 审计日志 → 公网部署（登录/限额/HTTPS）→ 真实 40+20+10 独立题集 →
简历/投递材料更新。

---

## 6. 关键文件路径（每个一句话）

| 路径 | 作用 |
|---|---|
| `D:\_review_rag-cs\service.py` | FastAPI 服务：11 个端点、RAG startup/search/chat、KB 管理路由、限流/鉴权接线 |
| `D:\_review_rag-cs\auth.py` | SSO：none/proxy/jwt(HS256)、角色 claim、`TRUST_PROXY_HEADERS` 校验 |
| `D:\_review_rag-cs\kb\source.py` | DocumentMeta/Source/UploadSource、PDF/DOCX/MD/TXT/HTML 解析、切块、`ParseError` |
| `D:\_review_rag-cs\kb\store.py` | namespace manifest、上传/列表/软删除、`load_chunks`、`last_errors` |
| `D:\_review_rag-cs\kb\feishu.py` | 飞书 tenant token、wiki 树分页、docx raw_content、递归同步/哈希/tombstone |
| `D:\_review_rag-cs\kb\layout.py` | Docling Docker / Docling local / MinerU 三个后端，`PDF_BACKEND` 选择与回退 |
| `D:\_review_rag-cs\kb\context.py` | 上下文层 v1：八层声明、chars 预算（1840 检索额度）、整块丢弃、`ContextTrace` |
| `D:\_review_rag-cs\tests\test_context.py` | 上下文层 20 条：预算/整块丢/旧路径 golden prompt/trace 不存正文/流式 payload |
| `D:\_review_rag-cs\scripts\mutate_context_layer.py` | 变异体：证明"旧路径不变""裁掉的块不喂"两条判据会响，并核对字节还原 |
| `D:\_review_rag-cs\kb\attribution.py` | 失败归因六桶（依据 trace 的 `candidates` + `included/excluded`）；`context_available=False` 时不猜原因 |
| `D:\_review_rag-cs\kb\eval_metrics.py` | 上下文/检索指标：recall/precision/nDCG@3/budget_cut_rate/p95；缺失一律 `None`，不补 0 |
| `D:\_review_rag-cs\scripts\run_scenario_eval.py` | 场景评测 runner：HTTP 跑题集或 `--responses` 离线复算 → 指标 + 逐题归因 JSON |
| `D:\_review_rag-cs\scripts\check_eval_gold.py` | gold ↔ 语料联动检查（`## <clause>` 必须存在；扫 0 条报红） |
| `D:\_review_rag-cs\kb\evalkit\session.py` | 会话级打分：会话全对率 / 改写补全（指代）/ 重复提问；拒答题不进改写分母 |
| `D:\_review_rag-cs\scripts\run_session_eval.py` | 会话级 runner（复用 evalkit；http/recorded/inproc） |
| `D:\_review_rag-cs\evalset\scenarios\sessions\campus-followups.jsonl` | 10 个会话 / 22 轮（指代、连续拒答、换主题追问） |
| `D:\_review_rag-cs\evalset\report-sessions-campus-followups.json` | 会话级报告（schema `session-eval/1`） |
| `D:\_review_rag-cs\scripts\mutate_session.py` | 会话判据变异体（补全恒 False / 无 session_id），2/2 变红 |
| `D:\_review_rag-cs\kb\evalkit\` | 请求级评测内核：cases/metrics/report/transport（可复用，脚本只组装） |
| `D:\_review_rag-cs\scripts\mutate_evalkit.py` | 内核变异体（缺失补 0、不查重复 id），2/2 变红 + 字节还原 |
| `D:\_review_rag-cs\evalset\report-scenario-campus-evalkit.json` | 新 schema（request-eval/1）报告，8 指标与旧 runner 逐项一致 |
| `D:\_review_rag-cs\scripts\preflight.py` | 部署自检：key / kb_data / 审计 / 租户表 / 模型缓存；fail → 退出码 1 |
| `D:\_review_rag-cs\scripts\create_tenant.py` | 开通租户（明文 key 只打印一次） |
| `D:\_review_rag-cs\tests\test_preflight.py` | 7 条：缺 key=FAIL、占位符=WARN、目录不可写=FAIL、租户表缺失=FAIL、CLI 退出码 |
| `D:\_review_rag-cs\scripts\mutate_preflight.py` | 自检变异体（删 key 检查 / fail 返回 0），2/2 变红 |
| `D:\_review_rag-cs\static\console.html` | 薄控制台（租户/语料/最近审计；纯 vanilla，数据只用 textContent） |
| `D:\_review_rag-cs\tests\test_console.py` | 4 条：页面渲染 / 只调已知端点 / 禁 innerHTML / 页面不含数据 |
| `D:\_review_rag-cs\scripts\mutate_console.py` | 控制台变异体（innerHTML / 错端点），2/2 变红 |
| `D:\_review_rag-cs\tenants.py` | 租户注册表：key 只存 sha256、namespace 前缀匹配、`create` 只返回一次明文 |
| `D:\_review_rag-cs\scripts\mutate_tenants.py` | 租户判据变异体（隔离恒 True / key 不比对），2/2 变红 |
| `D:\_review_rag-cs\tests\test_tenants.py` | 11 条：注册表/隔离/审计/管理接口不泄 key |
| `D:\_review_rag-cs\evalset\fixtures\policy_corpus\` | 3 篇合成制度语料（含 2024/2026 两版，供 as-of 演示） |
| `D:\_review_rag-cs\evalset\scenarios\policy.jsonl` | 20 题（17 应答 + 3 拒答），gold 指向条款标题 |
| `D:\_review_rag-cs\evalset\scenarios\policy-asof-2025.jsonl` | 3 题 as-of 对照（按 2025 时点应命中旧版） |
| `D:\_review_rag-cs\evalset\report-scenario-policy.json` | policy 报告（request-eval/1） |
| `D:\_review_rag-cs\evalset\report-scenario-policy-asof-2025.json` | as-of 报告（3/3） |
| `D:\_review_rag-cs\scripts\feishu_add_member.py` | 排障工具：查看/添加知识空间成员（实测 `appid`+`member`） |
| `D:\_review_rag-cs\kb\connectors\core.py` | 连接器协议 + 增量同步引擎（哈希∪ACL∪元数据；删除只碰 owns()） |
| `D:\_review_rag-cs\kb\connectors\fixture.py` | 本地 fixture 连接器（离线验收增量与权限变更） |
| `D:\_review_rag-cs\kb\connectors\feishu.py` | 飞书 wiki 适配层（遍历/取正文/ACL 来源） |
| `D:\_review_rag-cs\scripts\connector_sync.py` | 统一同步 CLI（fixture/feishu，--dry-run/--no-delete） |
| `D:\_review_rag-cs\evalset\fixtures\connector_demo\` | 可直接跑的 fixture（2 篇 + manifest，含 hr 权限） |
| `D:\_review_rag-cs\scripts\mutate_connectors.py` | S1 变异体（忽略 ACL 变化 / 删“发版”步骤），2/2 变红 |
| `D:\_review_rag-cs\kb\audit.py` | 审计落盘：决策/定位/trace 摘要 → JSONL；问题只存 sha256，块只存 doc/clause |
| `D:\_review_rag-cs\scripts\prune_audit.py` | 审计保留期清理（独立入口，支持 `--dry-run`） |
| `D:\_review_rag-cs\tests\test_audit.py` | 审计 9 条：不落原文/只对 namespace/写失败不炸/清理保脏行/版本措辞 |
| `D:\_review_rag-cs\scripts\mutate_audit.py` | 审计变异体（写原文、清理删脏行），2/2 变红 + 字节还原 |
| `D:\_review_rag-cs\experiments\_audit-sample.jsonl` | 一行真实审计记录（schema 示例；可确认无原文） |
| `D:\_review_rag-cs\kb\versioning.py` | as-of 版本裁决：effective_date/status/event_time → 丢弃原因（stale/superseded/not_yet_effective） |
| `D:\_review_rag-cs\tests\test_versioning.py` | 14 条：时点过滤/废止版/多版本/日期未知/上传读取/服务接线 |
| `D:\_review_rag-cs\scripts\mutate_versioning.py` | 变异体（忽略 event_time、不丢废止版），2/2 变红 + 字节还原 |
| `D:\_review_rag-cs\experiments\_asof-acceptance-fixed.json` | as-of 端到端证据：两版共存、2025 时点答 400 元、现行答 600 元 |
| `D:\_review_rag-cs\experiments\_asof-acceptance.json` | as-of 受控验收（synthetic）：含"新版被近似重复关拦掉"的证据 |
| `D:\_review_rag-cs\kb\compress.py` | 块级规则压缩（近重复去重 / 噪声行），默认 off；只做减法不重写 |
| `D:\_review_rag-cs\tests\test_compress.py` | 压缩 9 条：去重/先到者胜/噪声只删整行/默认关/接线 |
| `D:\_review_rag-cs\scripts\mutate_compress.py` | 压缩变异体（阈值失效、正则吞正文），2/2 变红 + 字节还原 |
| `D:\_review_rag-cs\evalset\report-scenario-campus-compress-off.json` | 压缩关臂（14/15、触发 0） |
| `D:\_review_rag-cs\evalset\report-scenario-campus-compress-on.json` | 压缩开臂（14/15、启用 15 题、去重 0） |
| `D:\_review_rag-cs\kb\citation.py` | 引用校验（纯规则）：编号合法性、未标注段落、条款编号信号；不冒充 ALCE 的 NLI 口径 |
| `D:\_review_rag-cs\tests\test_citation.py` | 引用 14 条：编号/越界/段落级覆盖/拒答变体/流式事件/旧路径无引用 |
| `D:\_review_rag-cs\scripts\mutate_citation.py` | 引用判据变异体（编号错位、越界不标记），2/2 变红 + 字节还原 |
| `D:\_review_rag-cs\evalset\report-scenario-campus-citation.json` | 带引用的 campus 基线（validity 1.0、未标注段落 0.2） |
| `D:\_review_rag-cs\requirements-eval.txt` | 评测专用依赖（ir-measures + pytrec-eval-terrier）；**不进** runtime，CI 不装（Linux 无 cp314 wheel） |
| `D:\_review_rag-cs\evalset\scenarios\README.md` | 场景题集格式/出题纪律/campus 现状（hr·compliance·support 待语料，不编数据） |
| `D:\_review_rag-cs\evalset\report-scenario-campus.json` | campus 基线报告（n=20：14/15 答、5/5 拒、recall 0.9333、nDCG@3 0.9333、1 个 retrieval_miss） |
| `D:\_review_rag-cs\experiments\_context-v1-acceptance.json` | 3 个真实问题的 `/chat?namespace=` 响应 + trace（第 1 题 used=1187/1840） |
| `D:\_review_rag-cs\Dockerfile.docling` | Docling 容器镜像（CPU torch、pip/HF 镜像、worker） |
| `D:\_review_rag-cs\docker\docling_worker.py` | 容器入口：PDF → `DocumentConverter` + OCR → Markdown |
| `D:\_review_rag-cs\kb\eval.py` | 每语料 smoke 质检：self-hit、拒答探针、ACL 泄漏、延迟 |
| `D:\_review_rag-cs\kb\eval_runner.py` | 独立题集打分：answer/refuse/deny、Wilson 区间 |
| `D:\_review_rag-cs\scripts\run_kb_eval.py` | 读 JSONL、调 `/search`、写报告；429 重试/`--pace` |
| `D:\_review_rag-cs\evalset\enterprise_eval_template.md` | 40 answer + 20 refuse + 10 deny 的独立出题协议 |
| `D:\_review_rag-cs\scripts\feishu_sync.py` | 飞书 CLI：`--list-spaces/--space-id/--namespace/--acl/--dry-run` |
| `D:\_review_rag-cs\tests\test_kb_store.py` | store 单测（上传/删除/namespace/错误） |
| `D:\_review_rag-cs\tests\test_kb_api.py` | `/kb` 上传/列表/删除/eval/415/422 测试 |
| `D:\_review_rag-cs\tests\test_auth.py` · `test_auth_endpoint.py` | JWT/proxy/角色覆盖/401 测试 |
| `D:\_review_rag-cs\tests\test_parsers.py` · `test_layout.py` | PDF/DOCX 解析、Docling/MinerU/Docker 适配器测试 |
| `D:\_review_rag-cs\tests\test_feishu_sync.py` | 飞书同步语义（dry-run/哈希/tombstone/权限错误继续） |
| `D:\_review_rag-cs\README.md` | 总说明；端点清单/数字由 pre-commit 校验 |
| `D:\_review_rag-cs\enterprise-kb.md` | 企业层设计/API/ACL/评测/飞书/Docling 实测记录 |
| `D:\_review_rag-cs\HANDOFF-20261014.md` | 旧版交接（校园政策主线：限流/agent/旧评测），只作历史参考 |
| `D:\_review_rag-cs\samples\` | 本地测试 PDF（未入库）：SISU 手册原版 + 图片版模拟扫描 |
| `D:\dsh-work\_上下文工程-清单-20261023-v2.md` | 上下文分层/预算/Trace/降级的设计稿 |
| `D:\dsh-work\_企业知识库-落地清单-20261023.md` | 企业知识库产品化路线图/验收口径 |

---

## 7. 约定与偏好

- **Python 环境**：一律用 `D:\Python-project\.venv\Scripts\python.exe`，不裸敲 `python`/`pip`。
- **测试命令**（在 `D:\_review_rag-cs`）：
  ```powershell
  $env:PYTHONIOENCODING='utf-8'
  D:\Python-project\.venv\Scripts\python.exe -m pytest -q --no-header
  ```
- **中文注释/文档**：注释解释“为什么”和踩过的坑，不复述代码；数字必须能指回产物，不手写。
- **`docs/` 是 KB 语料目录**：设计文档/交接文档放仓库根，禁止放进 `docs/`（会被 loader 当语料）。
- **运行时目录**：`kb_data/` 是上传语料/索引运行时数据，不入库；`samples/` 是本地测试 PDF，不入库；
  `.env` 永不入库。
- **namespace**：默认 `default` 只放 `docs/`；企业语料用显式 namespace；不要跨 namespace 默认检索。
- **ACL**：生产环境不信任请求体 `acl`；用 SSO 角色。`AUTH_MODE=proxy` 必须配
  `TRUST_PROXY_HEADERS=1`，否则 503。
- **依赖**：不把 Docling/MinerU/重模型塞进默认 `requirements.txt`；Docling 走 Docker，
  `requirements.txt` 只保留 `pypdf` 等轻量依赖。
- **端点数字**：改 `service.py` 路由必须同步 README 的“11 个端点”清单；
  pre-commit 的 `scripts/check_readme_numbers.py` 会拦。
- **提交**：一批改动一个 commit，中文 subject（`feat(scope): ...` / `fix(...)` / `docs(...)`）；
  不绕过 pre-commit；不提交 `--no-verify`。
- **评审**：用 ZCode 盲审时 `--mode plan`，任务卡和结论落盘到 `D:\dsh-work\`；不要让它直接改仓库。
- **当前阶段禁止扩战线**：不做新连接器、多租户、前端、70 题大评测；先 Context Layer + 真实语料
  20 题验收。飞书 scope 是用户侧阻塞，不是代码阻塞。
- **Windows 坑**：中文输出先设 `PYTHONIOENCODING=utf-8`；Docker 不在 PATH 时用
  `C:\Program Files\Docker\Docker\resources\bin\docker.exe`；WDAC 拦 `.pyd`，不要在 Windows
  进程内跑 Docling。

---

## 8. 接手第一小时 checklist

1. `git log -1` 确认 HEAD；`git status` 看是否只有 `samples/`。
2. 读本文件 §2/§3/§5；再读 `enterprise-kb.md` 的 ACL/飞书/Docling 三节。
3. 跑全量测试，确认 **196 passed / 27 skipped**（或至少不比这个少）。
4. 起服务：`D:\Python-project\.venv\Scripts\python.exe -m uvicorn service:app --port 8000`，看 `/health`。
5. 确认 Docker Desktop 在跑、`docker images docling-runner:2.142.1` 存在；不存在则按
   `enterprise-kb.md` 重建。
6. 从 §5 P0 开始写 `kb/context.py`，不要先碰飞书/部署/新连接器。
