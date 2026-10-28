# 新窗口说明书（2026-10-28 版）

> 用途：把下面「第一条消息」整段复制到新窗口，即可无历史接手。
> 项目根：`D:\_review_rag-cs`　·　交接快照：`HANDOFF.md`　·　索引：`MEMORY.md`
> 本文件自身随项目走；**过期就改它**，别另建第 N 份"入口"。

---

## 第一条消息（复制这段）

```text
你是接手「企业知识库 RAG」项目的新窗口。工作目录：D:\_review_rag-cs。
先不要写代码，第一轮只做只读核对。

【一句话背景】
一个带评测闭环的中文制度问答服务：混合检索（bge 向量 + BM25 + RRF + LLM 精排）+ 带出处回答 +
不编造（答不出明确拒绝）+ 多租户/ACL + 评测集与失败归因 + 可交付部署。

【当前阶段（2026-10-28）】
代码侧已冻结。关键路径上只剩两件非代码的事：固定公网链接、第一个真实用户。
不要在"加企业级功能"上花时间（SSO/观测/更多连接器都属拿到 offer 后再学）。

【必读，按顺序，别整篇读大文件】
1. MEMORY.md（15 行索引，先看它决定读哪份）
2. HANDOFF.md（重点 §2 进度、§3 关键决定、§5 下一步）
3. START.md（30 秒电梯稿 + 五个数字）
4. enterprise-kb.md（只读你需要的节：评测/容量/部署）
5. git log --oneline -5（最近改了什么）

【可核对事实】
- HEAD 9764611；工作区可能有另一个 agent 的未提交改动（kb/runstate.py、tests/test_runstate.py、
  scripts/run_scenario_eval.py、evalset/runs/）—— 不要覆盖、不要擅自提交。
- 已提交状态全量：421 passed / 27 skipped（命令见下）。
- 工作区含未提交 runstate 测试时：430 passed（多 9 条）。
- 9 个评测套件：campus / hr / hr-deny / policy / policy-asof-2025 / company / company-hard /
  company-52 / sunrun。
- 演示：scripts/demo.py 一条命令起服务 + 6 项自检（含 /admin 必须 403）。

【不要做】
- 不要新增企业级功能（SSO/组织同步/告警/Langfuse/更多连接器）——已明确冻结。
- 不要改另一个 agent 的未提交文件；不要 `git add -A`。
- 不要手写数字：任何数字要能指回产物（report JSON / pytest 输出）。
- 不要复述旧结论当收益：闭环 accept 那 +1 小于运行间波动 ±2、McNemar p=1.0，
  文档已改成"机制跑通，不构成收益证据"。
- 不要声称"断网可用"：文档与检索在本机，生成调用 DeepSeek（可换内网模型）。

【第一轮只做这些，不改文件】
1. git log -1 --oneline / git status --porcelain；
2. 跑 pytest（命令见下），核对是否 421 passed / 27 skipped（27 条 skip 多与网络/依赖有关，别删测试凑数）；
3. 读 MEMORY.md + HANDOFF.md §2/§5；
4. 输出：现状核对（HEAD/测试/工作树，3 行）+ 你认为的下一步 3 件事（按价值排序）。
```

---

## 验证命令（三条，都是真实跑过的）

```powershell
cd D:\_review_rag-cs

# 1) 全量测试（约 2 分钟）
D:\Python-project\.venv\Scripts\python.exe -m pytest -q --no-header

# 2) 演示自检（起服务 + 6 项真打接口；--keep 留着演示）
D:\Python-project\.venv\Scripts\python.exe scripts\demo.py --port 8769 --keep `
  --corpus evalset/fixtures/sunrun_corpus --namespace sunrun `
  --in-scope "男生这学期要跑多少公里才算达标？" --out-scope "学校食堂几点开饭？"

# 3) 仓库自带的三个检查（提交前钩子也跑它们）
D:\Python-project\.venv\Scripts\python.exe scripts\check_readme_numbers.py
D:\Python-project\.venv\Scripts\python.exe scripts\check_file_refs.py
D:\Python-project\.venv\Scripts\python.exe scripts\check_test_counts.py
```

---

## 当前状态快照

| 项 | 值 |
|---|---|
| HEAD | `9764611`（docs(memory): 项目记忆索引） |
| 已提交测试 | **421 passed / 27 skipped** |
| 工作区（含他人未提交 runstate） | 430 passed |
| 评测套件 | 9 个（见上） |
| 真实语料 | 公司制度 50 条（company）、校园阳光跑通知 3 份（sunrun） |
| 公网 | 临时 Tunnel 验收过 6/6；**固定链接尚未买服务器** |
| 真实用户 | **0** |

## 下一步（按价值，不是按代码量）

1. **谈第一个用户**（材料：`D:\dsh-work\首访材料包-2026-10-27.md`）：
   辅导员/学工办/大创/电商；目标拿 3–5 个真实问题 + 一句"可以试"。
2. **固定链接**（一晚 + 约 40 元/月）：`DEPLOY.md` 路线 B；上完用 `deploy_smoke.py` 复验。
3. **真实语料第二批**：拿到真实问题后进评测集，跑 `eval_loop.py`（对比要看 `state_fingerprint`）。

## 常见坑（都踩过）

| 坑 | 现象 | 处理 |
|---|---|---|
| namespace 没传 | 页面问"怎么申请免跑"说没资料 | 页面选"全部分区（自动匹配）"或 `?namespace=sunrun` |
| hosts 被改 | GitHub/`raw.githubusercontent.com` 解析到 127.0.0.1 | 注释 hosts 里 `github-fix by Buddy` 段，`ipconfig /flushdns` |
| AGENTS.md 硬链接 | 改完只剩一个名字，DSH 读到旧内容 | `fsutil hardlink list ~/.dsh/AGENTS.md` 必须两个；用追加/字节替换，别 write+rename |
| Auto Memory | 以为有 MEMORY.md 自动加载 | 本 harness **没有**；规则在 `~/.dsh/AGENTS.md`「记忆纪律」，索引是项目内 `MEMORY.md` |
| 429 被当答错 | 连续跑评测，报告静默变 0/15 | 已修：退避重试 + `TransportError` 中断；跑前调高 `RATE_PER_MIN` |
| DeepSeek 外发 | 以为"数据不出内网" | 文档与检索在本机，**生成调 DeepSeek**；要全内网换本地模型 |

## 不要做（冻结清单）

- 不加 SSO/组织同步、告警、Langfuse、更多连接器；
- 不为"看到闭环 accept"人为制造失败；
- 不把 `docs/` 里的数字和报告对不上仍写进 README；
- 不 `git add -A`（工作区有另一个 agent 的 WIP）。

---

> 上一版（2026-10-25，Context Layer v1 任务）已作废，保留在 git 历史里：
> `git log -p --follow NEXT-WINDOW.md` 可看。
