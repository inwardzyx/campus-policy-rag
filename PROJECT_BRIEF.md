# PROJECT BRIEF —— 给另一个 AI 的项目说明书

> **读者**：另一个 AI（代码 agent / 评审模型）。
> **读法**：先读 §1–§3 建立骨架，再按 §10 的顺序读代码，动手前**必须**读 §7（硬约束）。
> **和 `HANDOFF.md` 的区别**：HANDOFF 是**逐批演进记录**（每个决定为什么、踩过什么坑，按时间）；
> 本文件是**现状快照 + 上手说明**（现在是什么、在哪、怎么验）。两者冲突时以**代码和产物**为准。

---

## 1. 一句话定位

**面向 HR / 合规场景的"可审计答案层"**：回答必须带条款出处、按事件时点选对版本、答不了就明确拒答，
并且每一步都能被评测、被审计、被私有化部署。

**它不是什么**（这些是刻意的边界，不是没做完）：

- 不是通用企业搜索（Glean / Microsoft Copilot 的赛道，我们打不过也不打）；
- 不是多步 agent 编排（系统一步结束：检索 → 精排 → 生成）；
- 没有长期记忆（记忆在 trace 里明确标 `reserved` + 理由）；
- 没有多模态（只吃文本；扫描件可选走 Docling）。

---

## 2. 现状快照（2026-10-26）

**数字只信现算**（本仓库的规矩：文档里的数字必须能指回产物）：

```powershell
# 端点数（README 的名单与它必须集合相等）
Select-String -Path service.py -Pattern '@app\.(get|post|delete|put)\(' | Measure-Object
# 测试收集数 / 全量结果
D:\Python-project\.venv\Scripts\python.exe -m pytest --collect-only -q
D:\Python-project\.venv\Scripts\python.exe -m pytest -q --no-header
# 变异体脚本
Get-ChildItem scripts\mutate_*.py | Measure-Object
```

| 项 | 值（2026-10-26 实测） |
|---|---|
| HTTP 端点 | 16（`/chat`、`/chat/stream`、`/search`、`/console`、`/admin/*`、`/kb/*`、`/health`、`/limits`、`/guard-report`、`/`） |
| 测试 | 收集 373 条；全量 **425 passed / 27 skipped**（跳过的是 CI 无 Linux 轮子的 nDCG 真值测试） |
| 测试文件 | 29 个，CI 全覆盖（`scripts/check_test_counts.py` 把关） |
| 变异体脚本 | 17 个（每个关键判据都能被故意打坏、然后变红） |
| 语料 | `docs/kb/` 10 篇（真实校规，263 块）+ `evalset/fixtures/policy_corpus/` 3 篇（合成制度，47 块） |
| 评测产物 | `evalset/report-*.json` 11 份（schema `request-eval/1` / `session-eval/1`） |
| 核心代码量 | `service.py` 约 2.3k 行；`kb/` 下模块均为百行级 |

---

## 3. 三层架构（这是理解本项目的骨架）

| 层 | 职责 | 主要模块 | 成熟度 | 最大缺口 |
|---|---|---|---|---|
| **① 基础数据层** | 把外部文档变成"可检索、可追溯、有权限、有版本"的块 | `kb/connectors/`、`kb/loader.py`、`kb/source.py`、`kb/store.py`、`kb/versioning.py` | L2.5/4 | 真实连接器被租户策略卡住；权限是映射不是组织同步；**解析质量对照没跑** |
| **② Runtime 运行层** | 一次请求从问题到带出处答案的全过程 | `service.py`、`kb/context.py`、`kb/citation.py`、`kb/compress.py`、`session.py`、`limits.py`、`tenants.py`、`kb/audit.py` | L3/4 | 无并发压测、无缓存、无成本面板 |
| **③ 评测自进化层** | 证明它"没坏"，并把结论反馈回前两层 | `kb/evalkit/`、`scripts/run_scenario_eval.py`、`scripts/run_session_eval.py`、`scripts/mutate_*.py`、`kb/attribution.py` | L1.5/4 | **闭环断着**：有自评测 + 自证伪，**没有**反馈回收 → 候选修改 → 配对门禁 → 回滚 |

★ 关键判断：第三层之所以只算一半，是因为"层"的定义不是"有一堆评测代码"，而是**评测结果能回头改前两层**。
当前的准确说法是：**自评测 + 自证伪，尚不自进化**。别在对外表述里说成"自进化"（会被追问"它自己改过什么"）。

---

## 4. 模块地图（路径 → 职责 → 关键不变式）

| 路径 | 职责 | 不变式（破坏了会静默出错） |
|---|---|---|
| `service.py` | FastAPI 应用：路由、闸门、上下文规划、prompt、审计 | 生成 prompt 里**只有本轮问题 + 本轮块**，历史绝不进生成 |
| `kb/context.py` | 上下文预算与 trace（`Budget`、`select_retrieved`、`exclusion_trace`） | 八层额度之和**恒等于**总预算；未实现层必须 `status=reserved` + 理由 |
| `kb/citation.py` | 引用校验：解析 `[n]`、段落级未标注、拒答白名单 | 指标单位是**段落**，不是句子（句子会假高） |
| `kb/compress.py` | 块压缩（去重/噪声行），默认关 | 关掉时 trace 里是 `None`，不是 `{}` |
| `kb/versioning.py` | as-of 版本裁决：`not_yet_effective` / `stale` / `superseded` | 日期未知的文档**保留并计数**，不按"过期"丢掉 |
| `kb/audit.py` | 审计落盘：会话/轮次/决策/条款/引用，**无原文** | 只存问题 sha256 与角色名，不存原文、不存改写 |
| `kb/attribution.py` | 六桶归因：`retrieval_miss/rank_shift/budget_cut/gen_uncited/acl_leak/stale_version` | 拒答是否"答了"按**答案文本**判（`looks_like_refusal`），不用 `knowledge_hit` 代理 |
| `kb/evalkit/` | 评测内核：cases / metrics（注册表）/ report（schema 化）/ transport（http·recorded·inproc） | 缺失值必须是 `None` + `metric_specs.missing` 说明，**不许补 0** |
| `kb/evalkit/session.py` | 会话级打分：会话全对率 / 改写补全 / 重复提问 | 拒答题不进"改写补全"的分母 |
| `kb/connectors/core.py` | 连接器协议 + 增量同步引擎 | 变更 = 哈希 ∪ ACL ∪ 元数据；删除只碰 `owns()` 的文档 |
| `kb/normalize.py` | 把没有 `##` 的正文里的 `第X条` 提成标题（真实 PDF 的第一道坎） | 已有 `#` 标题时**原样返回**；长尾正文不许误提成条款 |
| `kb/connectors/fixture.py` | 本地目录连接器（离线验收增量/权限） | 改 manifest 的 acl 等价于"外部系统收回权限" |
| `tenants.py` | 租户注册表（key 只存 sha256） | `create()` 只返回一次明文 key；`allows()` 按前缀且不放过相似名（`hrx`） |
| `limits.py` | 限流 + 日配额（Redis 或进程内） | 存储挂了按 `LIMIT_FAIL_MODE` 处置，默认 `closed`（宁可不服务） |
| `session.py` | 多轮历史（最近 5 轮问题，30 分钟 TTL） | **只存问题**，不存答案；历史只喂查询改写 |
| `static/console.html` | 薄控制台（租户/语料/审计） | 只用 `textContent`，禁 `.innerHTML` |

---

## 5. 数据与产物

| 位置 | 内容 |
|---|---|
| `docs/kb/` | 真实校规语料（**不要改**，加了语料要同步 `tests/test_guard.py` 的块数断言与盯条款断言） |
| `docs/inbox/网页抓取残留.md` | 脏数据标本：15 块会被入库把关拦下，是"关卡真的会拦"的证据 |
| `evalset/fixtures/policy_corpus/` | 3 篇合成制度（差旅 2026/2024 两版 + 考勤请假），含生效日/废止关系 |
| `evalset/scenarios/policy.jsonl` | 20 题（17 应答 + 3 必须拒答） |
| `evalset/scenarios/policy-asof-2025.jsonl` | 3 题 as-of 对照（问 2025 时点应命中旧版：500 元 / 60 日 / 100 元） |
| `evalset/scenarios/sessions/campus-followups.jsonl` | 10 个会话 / 22 轮（指代、连续拒答、换主题） |
| `evalset/report-*.json` | 评测产物；`schema_version` 决定字段含义，**改字段必须升版本号** |
| `kb_data/` | 运行时数据：`<namespace>/` 语料、`audit.jsonl`、`tenants.json`（**不入库**） |

---

## 6. 怎么跑（完整命令）

```powershell
# 起服务（8028 是这轮常用的端口，随便换）
cd D:\_review_rag-cs
D:\Python-project\.venv\Scripts\python.exe -m uvicorn service:app --port 8028

# 请求级评测（制度语料，按 2026-06-01 时点）
D:\Python-project\.venv\Scripts\python.exe scripts\run_scenario_eval.py `
  --cases policy --namespace policy_eval --event-time 2026-06-01 `
  --base-url http://127.0.0.1:8028 --out evalset/report-scenario-policy.json

# as-of 对照（按 2025-06-01，应命中旧版）
D:\Python-project\.venv\Scripts\python.exe scripts\run_scenario_eval.py `
  --cases policy-asof-2025 --namespace policy_eval --event-time 2025-06-01 `
  --base-url http://127.0.0.1:8028 --out evalset/report-scenario-policy-asof-2025.json

# 会话级
D:\Python-project\.venv\Scripts\python.exe scripts\run_session_eval.py `
  --cases evalset/scenarios/sessions/campus-followups.jsonl --namespace campus_eval `
  --bootstrap-docs docs/kb --base-url http://127.0.0.1:8028

# 全量测试 / 部署自检 / 三个检查器
D:\Python-project\.venv\Scripts\python.exe -m pytest -q --no-header
D:\Python-project\.venv\Scripts\python.exe scripts\preflight.py
foreach ($s in 'check_readme_numbers.py','check_file_refs.py','check_test_counts.py') {
  D:\Python-project\.venv\Scripts\python.exe "scripts\$s"
}

# 变异体（任选一个；每个都应 2/2 或 3/3 变红 + 字节还原）
D:\Python-project\.venv\Scripts\python.exe scripts\mutate_tenants.py
```

环境变量：`DEEPSEEK_API_KEY` 必需（不配则 `/chat` 全 500，服务能起）；`.env` 会被 `service.py`
和几个脚本自动读。

---

## 7. 硬约束（动手前必读，逐条都有事故背景）

1. **不改检索/精排/六道入库把关**（唯一批准过的例外：数据触发的版本感知去重）；
2. **不动 `docs/` 语料**；设计文档一律不进 `docs/`；
3. **数字不许手写**：写进文档的数字必须能指回产物（报告 JSON / 命令输出）；
4. **缺失 ≠ 零**：拿不到就 `None` + 说明；可证明是 0 才能写 0；
5. **空转必须报红**：0 题、0 块、空套件都是错误，不许报绿；
6. **改任何判据都要配变异体**：先证明"坏情况下它会红"；
7. **一个批次一个 commit**（中文 subject，`feat(scope):` / `fix(...)`），提交前跑 pre-commit；
8. **不新增重依赖**（`requirements.txt` 保持轻；Docling 只在 Docker）；
9. **审计不落原文**：不存问题原文、不存改写结果、不存块正文；
10. **渲染知识库文本一律 `textContent`**，禁 `innerHTML`；
11. **仪表盘/控制台页面不含数据**：数据靠 `/admin/*`，页面是静态壳；
12. **改 `.env` 只追加**：整篇粘贴覆盖过它，丢了 `DEEPSEEK_API_KEY`（HANDOFF §4 有记录）。

---

## 8. 已知坑（现象 → 根因 → 处置）

| 现象 | 根因 | 处置 |
|---|---|---|
| `/chat` 全 500，日志 `DEEPSEEK_API_KEY must be set` | 模型懒加载，启动时不校验 | `scripts/preflight.py` 会拦；本地先跑它 |
| 服务重启后语料/审计全没了 | 容器内 `/app/kb_data` 没挂卷 | compose 已挂 `kb-data`；见 `DEPLOY.md` §A4 |
| 拒答率看起来只有 1/3 | 判据用 `knowledge_hit` 代理"有没有答" | 已改按答案文本判（`looks_like_refusal`） |
| 改写命中率恒 0 | 判据要求改写含条款号——查询改写产出的是问句不是引用 | 已改 `rewrite_resolved`（省字是否被补回） |
| 测试块数从 263 变 310 | 测试把 `kb_data/` 运行时语料也算进"仓库语料" | 已按 `docs/**` 文件名过滤 |
| 飞书 `131006 tenant needs read permission` | 应用不是知识空间成员，且 tenant 无法自举 | 需管理员后台开权限；实测成员组合是 `appid`+`member` |
| 飞书 `10003 invalid param` | 多半是凭证不对（占位符/抄错/未发布） | `explain_error` 会直说 |
| 真实 PDF 上传后切不出条款 | `pypdf` 只抽正文、**没有 `##`**（实测 0 标题） | `kb/normalize.py` 先补 `第X条` 标题；提不出来才退回定长块 |
| `metric_specs` 里 `null` 被当成 0 | 消费方不看 `missing` 说明 | 报告把 `metric_specs` 放在 `aggregate` 之前 |

---

## 9. 术语表

| 术语 | 含义 |
|---|---|
| **as-of** | 按"事件发生时点"选生效版本；请求字段 `event_time`（`YYYY-MM-DD`） |
| **六桶归因** | 把失败分到 6 个可行动的桶：`retrieval_miss` / `rank_shift` / `budget_cut` / `gen_uncited` / `acl_leak` / `stale_version` |
| **trace** | 一次请求的上下文记录：`blocks`（喂了哪些块）/ `excluded`（丢了哪些、为什么）/ `degraded` / `budget` / `session_id` / `turn_index` |
| **budget** | 字符预算：总 4000、retrieved 上限 1840（0.46）、输出预留 400；整块取舍，不切块 |
| **refusal** | 库外/资料不足时明确拒答；拒答也带 trace，不返回空 |
| **evalkit** | 评测内核：cases / metrics（注册表，必须声明 missing）/ report（schema 化）/ transport（http·recorded·inproc） |
| **变异体** | 故意改坏一处代码，证明某条判据会变红；然后**字节还原**并核对 sha256 |
| **owns()** | 连接器"这条记录归不归我管"，决定删除操作的范围（防误删人工上传的语料） |

---

## 10. 代码阅读顺序

**10 分钟（建立骨架）**
1. `README.md` 的"现在一共 14 个端点"那段（路由全貌）；
2. `kb/context.py` 顶部注释 + `Budget`（预算与八层声明）；
3. `kb/attribution.py`（一次失败是怎么被归因的）。

**1 小时（跑通主路径）**
4. `service.py` 搜 `def chat(` → `_gated` → `handle_chat`（闸门与主流程）；
5. `handle_chat` 里的 `_rewrite_query_with_history` → `_search_for_request` → `version_mod.filter_versions` → `_plan_context` → `build_answer_prompt`；
6. `kb/evalkit/metrics.py` 的注册表（12 个指标与它们的 missing 说明）。

**半天（能改）**
7. `kb/connectors/core.py` + `fixture.py`（增量同步与权限）；
8. `tenants.py` + `service.py` 的 `_tenant` / `_write_guard`（多租户边界）；
9. `tests/test_guard.py`（入库把关的判据与它盯的具体条款）；
10. `scripts/mutate_*.py` 任选两个（理解本仓库"判据必须能变红"的纪律）。

---

## 11. 待办（按 ROI，含触发条件）

| 优先级 | 事项 | 触发条件 / 说明 |
|---|---|---|
| P0 | **真实公司制度 20 题**（现只有合成语料） | 需要一份真实制度文档（敏感信息可替换） |
| ✅ | **按条款号检索**（2026-10-26 完成）：索引改为 `doc + clause + text`；配对实验 条款题 2/15→13/15、内容题 17/17 不变；回滚 `INDEX_TEXT_MODE=text` | 产物 `experiments/ab-index-text.json` |
| P1 | **评测闭环 v0**：失败回收 → 候选修改 → 配对评测 → 接受/回滚门禁 | 让第三层名副其实；2–3 天，不依赖外部数据 |
| P1 | **解析质量对照**：`pypdf` vs `Docling` 的 Recall@5 | 用 `samples/` 现成 PDF，可离线做 |
| ✅ | **改写/精排缓存**（2026-10-26）：热跑 p95 6667→4045ms、token −73%、检索结果 10/10 一致；回滚 `CACHE_ENABLED=0` | 产物 `experiments/cache-ab.json` |
| ✅ | **并发压测 + 成本 + /metrics**（2026-10-26）：并发 1/4/8 → p95 3.7/3.3/6.5s、≈730 token/问；`/metrics` 无依赖 | 产物 `experiments/load-test.json` |
| P2 | 配额数值按租户自定义 | 现在所有租户共用一套 `DAILY_REQ_QUOTA` |
| ⏸ | 飞书真实同步 | 卡在租户策略：应用无法成为知识空间成员 |
| ⏸ | 长期记忆 | 触发条件：历史开始喂生成/检索，或出现多工具编排（Gartner：>40% agentic 项目会被取消） |

---

## 12. 如何验证这份说明书（别信，去跑）

| 主张 | 一条命令 |
|---|---|
| 端点是 14 个且 README 名单一致 | `python scripts/check_readme_numbers.py` |
| 所有测试文件都进了 CI | `python scripts/check_test_counts.py` |
| 文档里引用的文件都存在 | `python scripts/check_file_refs.py` |
| 判据能变红（不是假绿） | 任选 `python scripts/mutate_*.py`，应全绿且退出码 0 |
| 评测数字可复现 | 重跑 §6 的两条 `run_scenario_eval.py`，对比 `evalset/report-*.json` 的 `aggregate` |
| 部署前会拦住致命配置 | `python scripts/preflight.py`（去掉 key 再跑一次，应退出码 1） |

> 本文件若与代码/产物冲突：**以代码与产物为准**，并顺手改掉本文件——过期文档比没有文档更坏。
