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

### Step 2（第 3–4 天）前端：把「过程」画出来 —— **已完成（非流式版，2026-11-12）**

> 实际落地比计划小一号，原因写在这里：Java 层**没有** `/api/chat/stream` 的 SSE 转发
> （前端一直是「问一句等一个 JSON」）。要做**实时**时间线，得先在 Java 里加 SSE 穿透，
> 那是另一件事。这一版先把「过程」在响应里带回来并画出来 —— 用户能看到每一步，
> 只是不是边跑边看。**Step 2b（SSE 穿透）留到 Step 3 之后。**
>
> 已落地：
> - `apps/web/src/agentTimeline.ts`：**判据纯函数** `verdictOf` / `stepTone`（7 条单测）
> - `apps/web/src/components/AgentTimeline.vue`：步骤列表（工具 / 参数 / 耗时 / 命中数 / 失败标红）
> - `apps/web/src/views/ChatView.vue`：Agent 模式开关；404 时提示「agent 未启用」并**回退**到 /chat
> - Java：`Dto.AgentResponse`、`PythonAiClient.agent()`、`ChatService.agent()`（配额 → 调用 → 审计，
>   新增 `degraded` 决策档）、`POST /api/agent`
> - `apps/web/src/api.ts`：`askAgent()` + 类型

**Step 2 的判据（已用单测钉住，不依赖浏览器）**

| # | 判据 | 用例 |
|---|---|---|
| 2.1 | 零步骤 / 工具全失败 ⇒ **不许显示成成功** | `verdictOf([], 'done') → error`；`tool_error → error` |
| 2.2 | 撞上限 / 超预算 ⇒ 必须显式写「被截断」 | `max_rounds` / `budget` → warn，detail 含「截断」 |
| 2.3 | 未知 `stopped_reason` ⇒ 不许当成功 | `something_new → warn / 状态未知` |

原计划的「缺 `tool_result` 事件必须显示未完成」随 SSE 一起挪到 Step 2b；
现在这条判据的等价物是 2.1（零步骤 = 没有证据）。

<details><summary>原计划（保留，作为 Step 2b 的依据）</summary>

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
</details>

### Step 3.5（2026-11-13）拒答语义修好 —— **已完成**

修的是 Step 3 实测出来的那个洞：`refused` 判「任何一次命中即不拒答」，于是模型
**改写问法再查一次**就能把拒答磨掉（`s10` 实测：第一步被闸拒，第二步命中无关的《应征入伍管理办法》）。

修法（三处，缺一不可）：

1. **`gate`**：原问题能不能答，由 `handle_chat(原问题)` 判定 —— 与 `/chat` **同一道闸**。
   循环里查到什么都不能推翻它；`done` 分支也要拦（第一版只在收尾拦，等于没接）。
2. **改写上限**：`MAX_REWRITES = 2`，且**按"新的不同问法"去重计数**。
   第一版按"每次调用"数、上限 1 —— 正常题（`h1`）第一次换个措辞就被截断，当场误杀。
3. **memo 共享**：`make_tools(roles)` 让闸和工具用同一份 `handle_chat` 响应，
   同一个问法只调一次模型（否则一次问答跑两遍，既贵又可能不一致）。

复验（同一服务、同一天）：`s10` / `h18` → `gate_refused`；`h1`（应答题）→ 正常 `done`。

### Step 2b（2026-11-13）SSE 穿透 —— **已完成**

- Java：`PythonAiClient.streamAgent()`（`exchange` 拿 `InputStream`，`retrieve` 会缓冲）、
  `ChatService.agentStream()`（配额在开流前扣、审计记 `streamed`）、`POST /api/agent/stream`
- Python：`/agent/stream` 收尾补发 `result` 帧（`refused`/`stopped_reason` 不进事件流的话，
  前端只能猜"成功"）
- 前端：`applyEvent` / `parseSseFrames`（纯函数，5 条用例：合并成一步、result 定成败、
  流断=状态未知、分片留残余、坏帧只丢自己）
- 判据没变：判"算不算成功"的仍然是 `verdictOf`，组件不解析模型的散文

### Step 3（第 5–6 天）评测：agent 的三条指标进流水线 —— **已完成（2026-11-12）**

> 产物（实测记录，脚本生成）：`eval/reports/agent-baseline.md`（105 题 `/agent`）与
> `eval/reports/chat-baseline-same-moment.md`（**同一刻**的 `/chat` 对照）。
> 两份都由 `eval/agent_metrics.py` 生成，数字不许手改。

**实测（105 题独立集，同一刻两次跑批）**

| 指标 | `/agent` | `/chat`（同刻） | 差 |
|---|---|---|---|
| `gold_in_sources` | 30/44 | 41/44 | **-11** |
| `no_miss` | 37/44 | 43/44 | **-6** |
| `refuse_ok` | 26/61 | 41/61 | **-15** |

**结论：agent 全面更差，拒答崩得最多。** 机制已用单题复现钉住（不是猜）：

- `s10`「退学后还能回来接着读吗」：第一步 `ask_policy(原问题)` 返回 `no_hit`（同一道闸），
  第二步模型**改写问法** `search_policy("退学 复学 重新入学")` 命中 3 条，
  返回的来源是《学生应征入伍管理办法》—— 与问题无关。
- 而 loop 的 `refused` 判定是「**任何一次**命中即不拒答」⇒ 这条路绕过了拒答。

⇒ 缺陷在**循环的拒答语义**，不在工具。修法（Step 4 之前的必做项）：

1. 拒答口径改以**原问题**为准（记录第一次 `ask_policy` 的结果作为基准，后续改写命中只作补充）；
2. 禁止「换个问法重试直到命中」：同一问题最多一次改写重试；
3. 判据：修好后 `refuse_ok` 不得低于同刻 `/chat` 的 41/61；低于就继续修，**不许带着更差的拒答进前端演示**。

**顺带一条方法论教训**：8 题冒烟时 agent 拒答 8/8（看起来打平），105 题全量只有 26/61。
小样本让人过度乐观 —— 这就是为什么"冒烟"只能当冒烟，不能当结论。

<details><summary>原计划（保留）</summary>

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
</details>

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
