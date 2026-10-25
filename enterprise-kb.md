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
