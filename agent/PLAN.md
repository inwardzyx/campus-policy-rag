# PLAN —— 把「Agent 实验」提升成「Agent 产品」（2 周）

> 写于 2026-11-12。**Step 1 已完成（同日）**：端点、工具、冒烟全部落地，
> 实测数字见 `eval/reports/agent-smoke.md`（实测记录，由 `agent/smoke_agent.py` 生成）。读者：我自己（或下一个接手的 AI）。
> 背景：`agent/` 里已经有**三种 loop 实现**和一套 MCP 工具（见下），但它们只活在脚本里；
> 产品路径 `/chat` 仍是**单步**。本计划补的是「产品化 + 前端时间线 + agent 指标进评测」。

---

## 0. 现状：不是从零开始（先说清已有资产，避免重写）

| 已有的 | 在哪 | 说明 |
|---|---|---|
| MCP server（JSON-RPC over stdio） | `mcp/server.py`、`mcp/tool_schemas.py` | 工具：`search_policy(query,k)`、`ask_policy(question)` |
| 工具实现（HTTP 打回 RAG） | `mcp/tools_impl.py` | 含 `NO_HIT` 翻译、usage 记账、`/search` 拆分 |
| 手写 agent loop（`MAX_ROUNDS=6`） | `mcp/probe_loop.py::run_one` | 含 `check_fabrication` / `check_language` |
| 真 MCP client + 真 LLM `bind_tools` | `agent/policy_agent.py` | 承诺"只换工具调用那一行" |
| LangGraph 版对照 | `agent/langgraph_agent.py` | 已写清"换框架到底换了什么"（控制流三处） |
| 协议/递归/工具选择的探针 | `mcp/probe_*.py` | 递归上限、tool_choice、ToolNode 报错 |

**⇒ 所以本计划的第一条纪律：loop 不重写、工具不重写、prompt 不重写。**
只做三件事：**暴露成 HTTP + 前端看见过程 + agent 指标进评测**。

---

## 1. 目标与非目标

**目标（可验收）**
1. `POST /agent`（非流式，给评测）与 `POST /agent/stream`（SSE，给 UI），**复用**现有工具与拒答门禁。
2. 前端能看见每一步：`plan → tool_call → tool_result → answer`，每步带工具名/参数/耗时/命中条数。
3. agent 的三条指标（任务完成率 / 工具选择正确率 / 步数与 token）进现有评测流水线，并**如实报出"agent 比单步更差"的地方**。

**非目标（这轮不做，写在这里防止跑偏）**
- 不换框架（LangGraph 对照实验已经做完，结论在 `agent/langgraph_agent.py` 顶部，不需要再证一遍）。
- 不做多 agent / 工具市场 / 长期记忆 / 沙箱执行。
- **不动 `/chat` 默认路径**：agent 是可开关的第二条路，出问题一键退回。

---

## 2. 两周排期（每步都有判据与回滚点）

### Step 1（第 1–2 天）`agent/loop.py`：把 loop 变成可调用的函数

**改什么**
- 新文件 `agent/loop.py`：从 `mcp/probe_loop.py::run_one` 抽出 `run_loop(question, *, max_rounds, budget_tokens, tools) -> dict`。
  返回结构（**这是前端与评测共同的契约**）：
  ```python
  {
    "answer": str,
    "refused": bool,
    "stopped_reason": "done" | "max_rounds" | "budget" | "error",
    "steps": [
      {"i": 1, "plan": str, "tool": str | None, "args": dict,
       "tool_ms": int, "n_src": int, "tokens": int | None, "error": str | None},
    ],
    "usage": {...} | None,
  }
  ```
- `service.py` 新增两个端点，**都走现有 `_gated()`（限流 + 记账 + 审计）**：
  - `POST /agent` → `ChatResponse` 同构，另加 `agent: {steps, stopped_reason}`；
  - `POST /agent/stream` → SSE，事件类型固定四种：`plan` / `tool_call` / `tool_result` / `answer`（+ `error`、`done`）。
- 开关：`AGENT_ENABLED`（默认 **off**）—— 关掉时两个端点返回 404，行为与现在完全一致。

**判据（每条都要能反向验证）**
| # | 判据 | 反向验证（必须有） |
|---|---|---|
| 1.1 | `/agent` 的回答必须带 **sources**，且 sources ⊆ `/chat` 同问题能拿到的块 | 把 loop 的 `sources` 置空 → 断言必红 |
| 1.2 | `stopped_reason` 不许静默：撞上限时要写 `max_rounds`/`budget` | 把 `max_rounds` 设 1 → 响应里必须出现 `stopped_reason="max_rounds"` |
| 1.3 | **拒答不退化**：61 题拒答集，拒答准确率不得低于 `/chat` 的 39/61 | 绕过门禁直接答 → 断言必红 |
| 1.4 | 关掉 `AGENT_ENABLED` 时，`/chat` 的行为逐字节不变 | 拿现有 A/B 脚本比对（`agent/verify_direct_unchanged.py` 已有这套） |

**回滚点**：Step 1 全部在 `AGENT_ENABLED` 后面；任何时候改回 off，产品路径零影响。
**放弃条件**：若 1.3 的拒答准确率掉到 <34/61，**停下来修门禁**，不进 Step 2 —— "agent 更容易瞎答"是真实风险，不能带病上线。

### Step 2（第 3–4 天）前端：把"过程"画出来

**改什么**
- 新组件 `apps/web/src/components/AgentTimeline.vue`：接收 SSE 四类事件，渲染可折叠步骤列表
  （工具名、参数摘要、耗时、命中条数、失败步骤标红）。
- `apps/web/src/views/ChatView.vue`：加一个「Agent 模式」开关，切换后走 `/agent/stream`；其余（登录/鉴权/配额提示）复用现有逻辑。
- 失败可见：流在 `answer` 之前断掉 → 显示"过程未完成，以下为部分结果"，**不许假装成功**。

**判据**
| # | 判据 | 反向验证 |
|---|---|---|
| 2.1 | 每个 `tool_call` 都有配对的 `tool_result`（或 `error`） | mock 一个缺 `tool_result` 的流 → UI 必须显示"未完成" |
| 2.2 | 时间线的步数与 `/agent` 返回的 `steps` 长度一致 | 删掉一个事件 → 断言不一致必红 |
| 2.3 | 关掉 Agent 开关时，页面与现在一致 | 同 1.4 的方式截对比图 |

**回滚点**：开关关闭即回到现 UI；组件独立，不改现有 ChatView 的渲染路径。

### Step 3（第 5–6 天）评测：agent 的三条指标进流水线

**改什么**
- `eval/` 新增 `agent_metrics.py`：任务完成率（规则：gold 条款命中 + 无编造）、工具选择正确率（gold tool=search/ask）、平均步数、平均 token。
- 用现有 **105 题**跑一遍 `/agent`，与 `/chat` 基线**配对比较**（同一个 `eval/gate.py`，不新写统计）。
- 如实记录"agent 更差"的地方（多步更容易过度检索、更容易把相邻条款当答案）。

**判据**
| # | 判据 | 反向验证 |
|---|---|---|
| 3.1 | 指标定义只写一处（复用 `eval/summarize.py::METRICS` 的机制） | 改一个阈值 → 报告数字必须跟着变 |
| 3.2 | 跨层/跨版本拒绝配对比较 | 拿 `/agent` 与 `/chat` 的跑批直接比 → gate 必须拒绝（不同"被测对象"） |
| 3.3 | 工具路由改坏 ⇒ 指标必须下降 | 把路由写成"永远 search" → 工具选择正确率必跌 |

**回滚点**：评测脚本独立；不动现有基线报告（新报告另起文件）。

### Step 4（第 7 天）录像 + 文档

- 30 秒录像：一个**需要两步**的问题（例如"人事问加班补贴 + 请假流程"），时间线可见。
- 更新 `agent/PLAN.md` 的实测数字（本文件"结果"一节）。
- README 加一小节：agent 是可选路径 + 一句"为什么不换框架"（引 `langgraph_agent.py` 的对照结论）。

---

## 3. 复用清单（明确不重写，防止"又造一套"）

| 能力 | 复用谁 |
|---|---|
| 工具与协议 | `mcp/server.py` + `mcp/tool_schemas.py` + `mcp/tools_impl.py` |
| loop 主体 | `mcp/probe_loop.py::run_one`（抽函数，不改逻辑） |
| prompt / 拒答翻译 | 现有 `SYSTEM_PROMPT`、`NO_HIT_TEXT` |
| 限流/配额/审计 | `service.py::_gated()`、`UsageLedger`、`audit` |
| 流式骨架 | `service.py` 的 `/chat/stream`（后台线程 + 队列那套） |
| 评测与门禁 | `eval/`、`kb/loopgate.py`（McNemar） |
| 报告与指纹 | `eval/summarize.py`、`kb/runstate.py` |

---

## 4. 结果（待填，做完按实测写，不许手写）

| 指标 | `/chat`（单步基线） | `/agent`（12 题冒烟） | 结论 |
|---|---|---|---|
| 应答带来源 | 4/4 | **4/4** | 不丢引用 |
| 拒答准确（8 道拒答题） | **8/8** | **8/8** | 打平（前两版是 4/8、6/8，见下） |
| 平均步数 / token | 1 步 / ≈700 | 2 步 / **1592** | 多花一倍 token |
| 工具选择 | —（无工具） | `ask_policy` 与 `search_policy` 都会用 | 待 105 题全量再判 |

> ★ 12 题只是冒烟，**不是结论**：全量 105 题要等 Step 3 接进评测流水线再跑。
> 数字出处：`eval/reports/agent-smoke.md`（实测记录，脚本生成）。

### 实测中改写计划的两处（这才是这一步的真正产出）

1. **`mcp/tools_impl.py` 不能直接用**：它是给**独立 MCP 进程**用的，只会 HTTP 打
   `127.0.0.1:8000`。`/agent` 活在 service 进程里，第一版直接复用它 ⇒ 两次工具调用全部失败，
   而响应仍是 `200 + stopped_reason=done`（答案是一段"服务连接失败"的优雅话术）。
   ⇒ 新增 `agent/tools_local.py`：同一份渲染（`_format_sources` / `_signal_line` 直接 import），
   但**失败要抛**（`ToolError`），并给 loop 增加 `stopped_reason="tool_error"`。
   **判据 1.3 当场证明了自己有用。**
2. **工具必须复用 `/chat` 的拒答闸，而不是"再写一个"**：
   | 版本 | search_policy 拿证据的方式 | 拒答 |
   |---|---|---|
   | v1 | 裸 `rag.search`（top-5 直接喂） | **4/8** |
   | v2 | `handle_search`（向量闸 `_vec_reject`） | **6/8** |
   | v3 | `handle_chat`（精排 < 5 的完整闸） | **8/8** |
   ⇒ 代价是为了拿 sources 多一次生成（约 700 token）。这是"先正确、再省 token"的取舍；
   要省，就给 `handle_chat` 加 `no_generate` 参数（那要改 `/chat`，单独一次改动）。

> ★ 这张表的数字必须由 `eval/` 脚本现算并落盘到 `eval/reports/`，本文件只引用产物路径。

---

## 5. 风险

1. **agent 更容易瞎答**（多步检索把相邻条款当答案）→ 用 1.3 的门禁守住；守住前不进前端。
2. **前端时间线会拖长交付**（Vue 是新东西）→ 先把事件协议定死（Step 1），前端可以先接一个最小版本。
3. **演示环境依赖 Docker 与网络** → 录像前跑一遍 `dev.ps1` 自检（HANDOFF §7）。
