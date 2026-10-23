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

当前：**9 passed**（上传/元数据/namespace 隔离/软删除/txt+html/路径逃逸/契约/重传更新）；`tests/test_kb_api.py` **4 passed**（含 ACL 过滤）；`tests/test_kb_eval.py` **2 passed**（smoke 质检）。

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

已验证（手动 + 测试）：

```text
upload(acl=hr) -> search(namespace=demo, acl=hr) 命中
search(namespace=demo, acl=None/finance)          拒答
delete -> search(namespace=demo)                  拒答
tests/test_kb_api.py: 4 passed
```

## 下一步（按优先级）

1. **ACL 最终块复检**：检索前已按块过滤；父文档扩展/rerank 合并后还要对最终入选块再验一次，防越权内容被带回来；
2. **飞书 wiki 连接器**：`wiki/v2/spaces/get_node` 树遍历 + docx 正文 + 权限跳过；
3. **PDF/DOCX 正式支持**：优先 Docling/MinerU，不在仓库里手写解析器；
4. **独立评测集**：自动质检（self-hit/拒答/ACL）不是独立评测；下一步做每语料模板题 + 人工抽检。

## 不做

- 不做通用 RAG 平台、50+ 连接器、Trace UI、multi-agent；
- 不把六道把关复制到 store（仍由 `service.RAG._guard` 统一做）。
