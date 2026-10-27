# 一分钟入口（START HERE）

> 只想快速判断这个项目靠不靠谱 → **这一页就够**。
> 要动手改代码 → [`PROJECT_BRIEF.md`](PROJECT_BRIEF.md)（模块地图 + 硬约束 + 验证命令）。
> 想查"为什么这么做" → [`HANDOFF.md`](HANDOFF.md)（逐批决策与踩坑记录）。

## 一句话

**企业知识库的可靠 AI 答案系统**：多租户、按事件时点裁决版本、答案可审计可归因、
答不了就拒答、自评测 + 部署自检。

**不是**：通用企业搜索（Glean 的赛道）、多步 agent、长期记忆、多模态。

## 五个数字（每个都能自己复算，命令见下）

| 指标 | 数值 | 产物 |
|---|---|---|
| 入库把关 | 286 条原始 → **放行 263 / 拦下 23** | `docs/kb/` + `docs/inbox/` |
| 制度语料验收（20 题） | 应答 **17/17**、拒答 **3/3**、引用合法率 **1.0** | `evalset/report-scenario-policy.json` |
| as-of 版本裁决（3 题） | **3/3**（按 2025 时点答 500 元 / 60 日 / 100 元） | `evalset/report-scenario-policy-asof-2025.json` |
| 会话级（10 会话 / 22 轮） | 整会话全对 **8/10**、指代补全 **4/4** | `evalset/report-sessions-campus-followups.json` |
| 工程判据 | **412 passed / 27 skipped**；**17 个变异体脚本** | `pytest` + `scripts/mutate_*.py` |
| 重复问题（缓存） | p95 **6.7s → 4.0s**、token **-73%**、检索结果 10/10 一致 | `experiments/cache-ab.json` |
| 容量（部署形态，单进程） | 并发 1/4/8 → p95 **3.7s / 3.3s / 6.5s**；**≈730 token/问** | `experiments/load-test.json` + `experiments/cost-report.md` |

```powershell
# 复算（任选）
D:\Python-project\.venv\Scripts\python.exe -m pytest -q --no-header
D:\Python-project\.venv\Scripts\python.exe scripts\run_scenario_eval.py `
  --cases policy --namespace policy_eval --event-time 2026-06-01 --base-url http://127.0.0.1:8028
```

## 3 分钟看到效果

```powershell
cd D:\_review_rag-cs
D:\Python-project\.venv\Scripts\python.exe -m uvicorn service:app --port 8028

# ① 库内有答案：带条款出处
curl -s -X POST http://127.0.0.1:8028/chat -H 'Content-Type: application/json' `
  -d '{"question":"一线城市出差住宿费上限是多少？","namespace":"policy_eval"}'

# ② 库外问题：明确拒答（不编）
curl -s -X POST http://127.0.0.1:8028/chat -H 'Content-Type: application/json' `
  -d '{"question":"公司食堂几点开饭？","namespace":"policy_eval"}'

# ③ 控制台（租户 / 语料 / 最近审计）
# 浏览器打开 http://127.0.0.1:8028/console
```

## 文档地图（别再一进来就翻 1688 行 README）

| 文档 | 给谁 | 什么时候读 |
|---|---|---|
| 本页 `START.md` | 任何人 | 第 1 分钟 |
| `PROJECT_BRIEF.md` | 另一个 AI / 接手方 | 要改代码前（§7 硬约束必读） |
| `HANDOFF.md` | 想知道"为什么"的人 | 查某个决定/踩坑的背景 |
| `enterprise-kb.md` | 想要技术全景的人 | 深读某一层时 |
| `DEPLOY.md` | 运维/部署 | 要上线时 |
| `evalset/scenarios/README.md` | 要加题集/指标的人 | 写评测时 |
| `HANDOFF.wb.md` | — | **模板，非必读** |

## 现在最大的窟窿（不藏）

1. **真实公司制度还没有，但外部文档已跑通** —— `samples/sisu-handbook-2025.pdf`（别人写的手册）onboarding 结果：探针 **9/10**、引用合法率 1.0、拒答 4/4；
   20 题验收仍跑在 3 篇合成制度上（结构真实、内容是合成）；
   ★ 该手册是多份制度合编（条款号大量重复），按条款号去重后只保留 **92** 条——已知限制见报告；
2. **零部署、零用户** —— 没有公网 URL，也没有"别人传自己语料"的验证；
3. **解析质量对照只量了一半** —— 已实测：`pypdf` 从真实 PDF 抽出 339,901 字符但
   **`##` 标题 0 个** ⇒ 已加 `第X条` 归一化（同一份手册现在切出 **1653 块、全部带条款号**）；
   `Docling` 本机被 WDAC 拦（只能 Docker），**对照尚未完成**；
4. **无并发压测 / 无成本面板** —— 只有单机 p95 ≈ 3.8s，没有规模数字；
5. **飞书连接器被租户策略卡住** —— 代码就绪，应用无法成为知识空间成员。

> 上面每条都不是"没想到"，是**已知未做**。改哪一条、为什么、预计多久，见 `PROJECT_BRIEF.md` §11。
