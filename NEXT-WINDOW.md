# 新窗口说明书 — 企业知识库 RAG / Context Layer v1

> 用途：把下面「第一条消息」整段复制到新窗口，即可无历史接手。
> 项目根：`D:\_review_rag-cs`　·　交接快照：`HANDOFF.md`（2026-10-25，代码 HEAD `48d9e6e`；说明书提交 `183eab6`）

---

## 第一条消息（复制这段）

```text
你是接手「企业知识库 RAG」项目的新窗口。工作目录：D:\_review_rag-cs。
先不要写代码，第一轮只做只读核对和计划。

【一句话背景】
这是把校园制度问答 RAG 企业化后的服务：多 namespace 上传、块级 ACL/SSO、PDF/DOCX/扫描件 OCR、
飞书 wiki 连接器、每语料质检与独立评测；目标是 HR/合规/客服可用的“带出处答案或明确拒答”。
当前缺的不是功能数量，而是上下文层（分层/预算/Trace/降级）。

【必读，按顺序，不要整篇读大文件】
1. HANDOFF.md：重点 §2 当前进度、§3 关键决定、§5 下一步、§7 约定。
2. enterprise-kb.md：只读「ACL」「飞书 wiki 连接器」「复杂 PDF / 扫描件」三节。
3. D:\dsh-work\_上下文工程-清单-20261023-v2.md：Context Layer 设计稿。
4. 代码只按需定位：
   - grep 找 service.py 的 handle_chat / _search_for_request / _request_roles / ChatResponse；
   - 读 kb/source.py、kb/store.py 的公开类和函数签名；
   - 不要一上来读完整 service.py/README。

【当前可核对事实】
- HEAD 4635fd3；工作树应只有 samples/ 未跟踪；代码/文档干净。
- 全量测试：196 passed, 27 skipped（命令见 HANDOFF §7）。
- 已有：namespace/ACL/SSO、PDF/DOCX、Docling Docker OCR（镜像 docling-runner:2.142.1，2.86GB）、
  飞书连接器代码、smoke 质检、独立评测模板/runner。
- 未做：kb/context.py、版本/as-of、审计、公网部署、真实飞书同步（应用 scope 未开）、
  真实扫描件/双栏/表格验收、真实 40+20+10 题集。

【本轮唯一任务：Context Layer v1】
目标：新增 kb/context.py，做“不破坏旧路径”的最小上下文层。
必须包含：
1. 分层：system / policy / history / task / memory / retrieved / tool / output；
2. 预算：按 chars 估算（不要假装是 token），记录每层占用、截断/压缩；
3. ContextTrace：trace_id、blocks（source/clause/chars/included/reason）、excluded、degraded；
4. 只对带 namespace 的 /chat 和 /chat/stream 启用；docs/ 默认语料的旧路径保持不变；
5. 第一版可以只做“选块 + 预算 + Trace”，不改答案 prompt；v2 再模板化/压缩；
6. 响应里给一个可选的 context 摘要（字段默认 None，不能破坏旧响应）。

【硬约束】
- 不重写检索/精排/六道入库把关；不改 docs/ 语料；不把设计文档放进 docs/。
- 不新增连接器、多租户、前端、部署、70 题大评测。
- 不把 Docling/MinerU 等重依赖塞进 requirements.txt；Docling 继续走 Docker。
- 数字必须能指回产物；不要编 token 数/通过率。
- 生产 ACL 不信任请求体：SSO 角色覆盖 acl；proxy 模式必须配 TRUST_PROXY_HEADERS=1。
- 改 service.py 路由要同步 README 的 11 个端点清单，否则 pre-commit 会红。
- 不绕过 pre-commit；一批改动一个中文 commit。

【第一轮只做这些，不改文件】
1. git log -1 / git status；
2. 按 HANDOFF §7 跑全量 pytest，确认 196/27（不一致就报告，不要删测试凑数）；
3. 用 grep/read 定位 handle_chat、_search_for_request、ChatResponse、build_answer_prompt；
4. 输出：
   A. 现状核对（HEAD/测试/工作树，3 行内）
   B. 5–8 行实现计划：新增/修改哪些文件、函数、测试
   C. 3 个风险点（尤其“旧路径不变”怎么保证）
   D. 需要我确认的 1–3 个问题
等我确认后再改代码。

【沟通】
中文；技术术语保留英文；解释“为什么”而不是复述代码；给命令时用完整 venv 路径
D:\Python-project\.venv\Scripts\python.exe；长任务用后台 job，不整段贴日志。
```

---

## 新窗口第一轮验收标准

- 能准确说出三件事：当前 HEAD/测试数、Context Layer 要接哪条路径、为什么旧 `docs/` 路径不能动；
- 计划里明确 `kb/context.py` 的数据结构、接入点、测试文件；
- 没有在未确认前改任何文件；
- 如果发现 `HANDOFF.md` 与代码冲突，先报告，以代码/测试为准。

## 之后每轮的验收

- 全量 pytest 不低于 196 passed / 27 skipped；
- namespace `/chat` 能看到 `context` 摘要：用了哪几块、多少 chars、丢了什么、是否降级；
- `docs/` 默认路径的答案/响应字段不变；
- commit 前 pre-commit 通过；README 端点数仍为 11。

## 不要给新窗口做的事

- 不要让它先做飞书/部署/新连接器/70 题；
- 不要让它读整个 `service.py`、整个 README、整段历史日志；
- 不要让它改 `docs/`、`kb_data/`、`.env`、`samples/` 里的 PDF；
- 不要让它“顺手重构”检索/精排/拒答逻辑。
