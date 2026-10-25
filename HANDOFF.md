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

### 2.3 实测结果（可复现）

**测试**（2026-10-25，最后一次全量）：

```text
196 passed, 27 skipped
```

命令见 §7。跳过的 27 项是需要 Redis/外部服务的旧测试。

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

- **上下文层没做**：没有 `kb/context.py`，没有分层/预算/Trace/降级；`/chat` 仍是
  “检索 → 拼 prompt → 生成”，上下文不可解释。
- **版本/as-of、审计日志没做**：`DocumentMeta.version` 有字段，但没有按版本查询；
  没有审计表/审计事件。
- **飞书真实同步没跑通**：应用没有 wiki scope（§4）。
- **独立评测集没出**：只有 40+20+10 的模板和样例，没有真实 70 题结果。
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

---

## 5. 下次从哪接（按优先级）

### P0：Context Layer v1（最该做，1–3 天）

新增 `kb/context.py`，先做**不破坏旧路径**的最小版：

- 分层：System / Policy / History / Task / Memory / Retrieved / Tool / Output；
- 预算：按 chars 估算（不要假装是 token），记录每层占用、截断/压缩；
- `ContextTrace`：`trace_id`、blocks（source/clause/chars/included/reason）、excluded、degraded；
- 只对**带 namespace 的 `/chat` / `/chat/stream`** 启用；`docs/` 的 105 题旧路径不动；
- 第一版可以先“选块 + 预算 + Trace”，不改答案 prompt；v2 再做模板化/压缩；
- 验收：3 个真实问题返回 trace，能看到用了哪几块、丢了什么、为什么；全量测试仍 196+。

设计稿在 `D:\dsh-work\_上下文工程-清单-20261023-v2.md`，路线图在
`D:\dsh-work\_企业知识库-落地清单-20261023.md`。

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
