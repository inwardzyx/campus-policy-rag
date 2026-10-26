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

### 2.3 实测结果（可复现）

**测试**（2026-10-25，最后一次全量）：

```text
256 passed, 27 skipped
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
- **版本/as-of、审计日志没做**：`DocumentMeta.version` 有字段，但没有按版本查询；
  没有审计表/审计事件。
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
| 飞书 wiki 无权限 | `99991672 Access denied ... [wiki:wiki, wiki:wiki:readonly, wiki:space:retrieve]` | 需在开发者后台加 scope、发布版本、把应用加入知识空间 | **未解决（需用户操作）** |
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

**下一步（批 4）**：数据层的时间维 —— `scenario/authority/effective_date/status`
+ as-of 过滤（`event_time`）+ `stale_version` 归因桶从空钩子变成真判据。

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
