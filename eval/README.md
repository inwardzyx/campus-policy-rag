# eval/ —— 一条可复核的评测流水线

**一句话**：把 `evalset/heldout/questions.json` 那 105 道题（应答 + 拒答）跑一遍，
产出一份**能对比后续改动**的分数，落盘成 `eval/reports/BASELINE-<层>.md`；
并让每份报告自己回答"这是同一个系统吗"（运行指纹）。

> **对外只说一句**：**评测资产版本化 + 运行指纹**。
> 不说「数据中台 / 评测中台」—— 那是资深平台团队的词，按那个标准会被追问多租户、调度、
> 口径治理委员会；而这个项目该被追问的是「**你的判据会不会响**」。
> （2026-11-11 盲审的结论：中台/血缘/分级调度这套词表，在 AI 应用岗 JD 里没人看。）

```text
promptfoo（调度 / 聚合 / 退出码）
    │ POST /case
    ▼
eval/case_server.py（进程内跑真管道）
    │ import                       ← 判分**复用** evalset/run_eval.py，不抄第二份公式
    ▼
eval/summarize.py（出报告，数字全现算）──▶ eval/reports/BASELINE-<层>.md（报告头带运行指纹）
    │
    ├─ eval/gate.py        配对显著性（**再导出** kb/loopgate.py，不新写）
    └─ eval/agreement.py   人机一致性 κ（样本 < 20 拒绝输出）
```

**数据层不建接口**：它在这个仓里已经存在，而且只有一个消费者（本流水线）。

| 已有产物 | 承担什么 |
|---|---|
| `kb_data/*/manifest.json` | 数据集版本（每文档 content_hash / acl / status / effective_date） |
| `kb/runstate.py` | 运行指纹 / 血缘（git sha + worktree diff + 逐文件语料 sha256 + env + python） |
| `kb/evalkit/metrics.py` | 指标登记与缺失语义（每个指标必须声明 desc / unit / missing） |

`evalset/run_eval.py` 是 `import service` 直调 —— **没有进程边界**，
所以再插一层 `get_dataset()/lineage()/metrics_spec()` 只会多一层没人调的代码。

## 四条判据的诚实标注（本文件最重要的一屏）

| # | 判据 | 状态 | 证据 / 缺口 |
|---|---|---|---|
| ① | 改清洗规则 → 自动算出影响哪些评测集 | **假绿** | 「规则 → 评测集 → 结论」的依赖映射不存在。真实形态是「runstate 指纹 diff + 全量重跑才发现」。案例：旧报告 35/44 与今天 30/44 的差异，是**重跑才发现的**，不是算出来的（见 `DECISIONS.md`） |
| ② | 结论可追到 数据集 → 切块 → 索引 → 模型/prompt 版本 | **旧路径真绿；新路径 2026-11-11 起接上** | `BASELINE-*.md` 报告头现在带 `kb/runstate.py` 指纹；切块/索引是（语料哈希 × 代码版本）的派生量、**不物化** ⇒ 指纹即索引版本 |
| ③ | 改「命中」定义 → 报告自动跟着变 | **半绿** | 判分单源 + 报告全脚本生成 + `check_readme_numbers` 对账；但「自动」不成立：每份报告要手动重跑，且两份 2026-10-10 的旧报告曾挂着过期数（已加历史快照声明） |
| ④ | 数据集内容变了但版本没变 → 报错 | **接近真绿 → 2026-11-11 补上最后一环** | run 级已真绿（改评测集必然换指纹，`tests/test_runstate.py` 守着）；缺的是 held-out 自己的校验和，已补 `evalset/heldout/questions.sha256` + `scripts/check_heldout_sha.py`（pre-push 跑）+ `tests/test_heldout_sha.py` 的变异用例 |

> ★ 这张表可以照着念给面试官。**「①是假绿」这句反而是最强的差异化信号** ——
> 大多数候选人的评测文档不会写自己哪条判据不响。
> 已收尾到真绿的：④（校验和 + 变异）与 ②（指纹进报告）。②③ 的剩余缺口见上表。

## 口径：形状与版本

- **指标定义**：`eval/summarize.py` 里的 `METRICS` 表（**代码级唯一来源**）；
  人类可读版就是本文件下面「三条指标与它们的分母」一节。两者冲突时以代码为准。
- **数据集版本**：`evalset/heldout/questions.sha256`（改题必须一起更新，否则 pre-push 拦下）。
- **运行版本**：每份 `BASELINE-*.md` 报告头的指纹（`kb/runstate.py`）。

## 接线（为什么长这样）

```text
promptfoo（Node，负责调度/聚合/逐题表）
   │  POST http://127.0.0.1:8091/case   {"qid": "h1", "question": "…"}
   ▼
eval/case_server.py（Python，进程内跑真管道）
   │  import evalset/run_eval.py 里的函数 ← ★ 判分口径的唯一来源
   ▼
service.py / rag：rag.search()（算 Recall@5）、handle_chat()（算 knowledge_hit）
```

三条设计约束，每条都是被实际踩到逼出来的：

1. **不写第二份判分公式。** `Recall@5` 的官方口径是 `rag.search()` 的 top-5，**不是**
   `/chat` 返回的 `sources`（`evalset/run_eval.py` 顶部那条注释解释了为什么：因拒答而
   `sources` 为空时，会被误算成"没召回"）。⇒ `case_server.py` 直接 `import run_eval`，不重算。
2. **不用 promptfoo 的 python provider。** 它会 spawn 子进程并用**管道**通信，
   而本机沙箱把 Node 子进程的管道挡掉（EPERM）。http provider 只走本机 socket，不受影响。
3. **题面不手抄。** `tests.yaml` 由 `gen_tests.py` 从 `questions.json` 生成；
   服务端会把收到的题面与 `questions.json` 对一遍，不一致就返回 `drift`，
   断言那边**一律判红** —— 旧 `tests.yaml` 跑出来的分数是假的。

## 跑法

两个终端。**命令**：

```powershell
# 终端 1 —— 起评测服务（离线层：零 API 成本）
cd D:\_review_rag-cs
$env:PYTHONIOENCODING='utf-8'
D:\Python-project\.venv\Scripts\python.exe eval\case_server.py

# 换成产品默认路径（rerank + 查询改写，会花 DeepSeek 的 token）：
#   $env:EVAL_LAYER='online'   再跑上面那条命令
```

**跑完会看到**（终端 1）：

```text
[boot] 题目 105 道，层=offline，正在加载模型 + 建库……
[boot] 就绪 19.3s：入库 332 块 / 拦下 23 块，监听 127.0.0.1:8091
[case] h1 answer hit=True gold=True 812ms
```

```powershell
# 终端 2 —— 生成题目 + 跑 promptfoo + 出报告 + 刷新 README 的基线表
cd D:\_review_rag-cs\eval
npm install                      # 沙箱里要加 --ignore-scripts，见坑 4
D:\Python-project\.venv\Scripts\python.exe gen_tests.py
npx promptfoo eval -c promptfooconfig.yaml --no-cache -j 1 -o reports\promptfoo-offline.json
D:\Python-project\.venv\Scripts\python.exe summarize.py --input reports\promptfoo-offline.json `
    --out reports\BASELINE-offline.md --compare reports\run_eval-offline-heldout.log
D:\Python-project\.venv\Scripts\python.exe summarize.py --render-index --md README.md
```

对账用的那份 run_eval 日志这样重生成（**不花 API 钱**，约 26 秒）：

```powershell
cd D:\_review_rag-cs
D:\Python-project\.venv\Scripts\python.exe evalset\run_eval.py --questions evalset\heldout\questions.json `
    --label heldout-check | Set-Content -Encoding utf8 eval\reports\run_eval-offline-heldout.log
```

- ★ **不要用 `>` 重定向**：Windows PowerShell 5.1 的 `>` 写的是 UTF-16LE，
  而 `summarize.py` 按 UTF-8 读 ⇒ 正则一条都匹配不上，对账会显示"读不到"（红，但原因假）。
- ★ `--label heldout-check` 是必须的：不带 label 时 `run_eval.py` 会拒跑 ——
  它怕覆盖主评测集的 `evalset/report-vector.md`（那份被 README 与数字对账器引用）。

### 复现"旧报告 / 现在的数到底能不能相减"

```powershell
git worktree add D:\_repro a9e4f85          # 只需一次
D:\Python-project\.venv\Scripts\python.exe eval\repro_old_code.py --variant old --reset
D:\Python-project\.venv\Scripts\python.exe eval\repro_old_code.py --variant current-shim
D:\Python-project\.venv\Scripts\python.exe eval\repro_old_code.py --variant current
```

产物 `reports/repro-old-code.{json,md}`：**commit sha + 数据 sha + 判分脚本 sha + 入库块数**
全部落盘，数字不手写。结论见下面「两组数能不能相减」一节。

## 基线（数字全部由 `summarize.py --render-index` 生成，别手改）

<!-- BEGIN:BASELINE （由 eval/summarize.py --render-index 生成；手改会被下次生成覆盖） -->

| 层 | `recall5` | `no_miss` | `refuse_ok` | 题数 | 产物 |
|---|---|---|---|---|---|
| offline | 30/44 | 42/44 | 12/61 | 105 | `reports/promptfoo-offline.json` |
| online | 40/44 | 42/44 | 41/61 | 105 | `reports/promptfoo-online.json` |

> 口径定义见 `eval/metrics_spec.py`；本表来自 2 份跑批 JSON。
> ⚠️ 层 `offline` 的 `refuse_ok` 不能用来谈「系统的拒答能力」：offline 层拒答只靠向量分 0.55 一道闸，那是已知边界。
<!-- END:BASELINE -->

## 能读出的结论（只到"方向"这一级）

- 查询改写 + 精排明显提高了应答题的**检索命中**（`recall5`），而 `no_miss`（漏答）
  在两层上一样 —— 也就是说"多捞回来的题"里没有一道改变漏答；漏掉的那几道在检索层就没捞到
  （看两份报告的逐题明细，说明列写着「gold 没进前 5 → 检索问题」）。
- 离线层的 `refuse_ok` **不能**用来谈拒答能力（只有向量分 0.55 一道闸）。要谈拒答用 online 层。
  这条纪律写在 `metrics_spec.py`，`summarize.py` 遇到 offline 层会在报告里主动打警告。
- 配对显著性还没做（待办 2 的 `eval/gate.py`）：上面两条都只是**方向**，不是"显著"。

## 对账：证明"接线是忠实的"，以及退出码必须红

`summarize.py --compare` 会把 run_eval 那份日志里的三个数**解析回来**（不手抄），逐项比；
不一致时**打印红字并以非 0 退出**。

- 同刻对账（2026-11-11）：promptfoo 与 `run_eval.py` 逐项一致。
  这一致**只证明接线对**（两边共用 `run_eval.py` 的同一份函数），**不证明指标本身是对的**。
- 反向验证：把日志里的 `30/44` 改成 `35/44`、`12/61` 改成 `9/61` 两处，
  对账立刻 2 项 ❌ 且退出码 1。（这条变异用例当场抓出了"只 print 红字、退出码却是 0"
  的假绿，见坑 6。）
- 常驻判据（不依赖 node）：`tests/test_eval_reconcile.py` 直接比
  `case_server._payload` 与 `run_eval` 的逐题结论 + 汇总，把"两把尺子分叉"变成一条会红的用例。

## 两组数能不能相减（2026-11-11 实测，证据在 `reports/repro-old-code.md`）

`evalset/report-vector-heldout.md`（2026-10-10，commit `a9e4f85`）与今天的基线数字不同。
在说任何"退化/提升"之前，必须先证明它们在同一条测量路上。

做法：`git worktree` 把 `a9e4f85` 拿出来，用**三种组合**各跑一次（产物里记 commit sha、
questions sha、docs 语料树 sha、判分脚本 sha、入库块数）：

| 组合 | 判分脚本 | 入库块数 | 结果 |
|---|---|---|---|
| 旧脚本 + 旧代码 | 旧（worktree 自带） | 263 | 与旧报告**逐项一致** |
| 当前脚本 + 旧代码 | 当前（复制进 worktree，带 `handle_chat = chat` shim） | 263 | **与上一行完全一致** |
| 当前脚本 + 当前代码 | 当前 | 332 | 今天基线 |

由此得到三条**可以被证据否证**的结论：

1. **旧报告是真的、可复现的**：同题、同语料、旧代码下，数字逐项复现。
2. **判分脚本/入口不是变量**：把旧脚本换成当前脚本（唯一的桥是 `handle_chat = chat` 这个
   **语义假设**），数字一模一样 ⇒ 之前担心的"旧入口已失效、两组数不可比"**被实测排除**。
   （旧 `chat(req)` 就是管道本身；2026-10-13 才把它拆成 `chat()` = 闸门 + `handle_chat()` = 管道。）
3. **数据也不是变量**：两边的 `questions.json` 与 `docs/` 语料树 sha256 **完全相同**。
   变的只有代码，而代码带来的**可测差异**是：入库块数 263 → 332（切分/入库关卡变了）。
   ⇒ 两组数**可以相减**，差异来自代码演化；但"具体是哪一处改动"**未定位**
   （按要求不做 `git bisect`，且 bisect 的假设是"入口一致"——这一点现在才刚被证明）。

⚠️ 这条路线本身有两个坑，都已经写进 `repro_old_code.py` 的注释：
跨版本必须打 shim（旧 service 没有 `handle_chat`），且当前脚本传的 `use_rewrite`
在旧 `ChatRequest` 上是 extra（pydantic 默认 `extra='ignore'`）⇒ **被静默丢弃**。
第一次跑还踩过两次：漏了 `sys.path`、以及 `run_eval` 的 `logging.basicConfig` 只在
`__main__` 下执行 ⇒ 退出码 0、一个字都没有（"全绿"其实是全空）。产物里对空结果写的是
「没解析出任何数字」，不是 0。

## 坑（全部实测踩到）

1. **`tests: file://tests.yaml` 要求文件是裸数组。** 文件里再套一层 `tests:` 会被当成
   "一个测试用例"，报 `Test case must contain assert, vars, options, metadata…`。
2. **http provider 返回 `Content-Type: application/json` 时，断言拿到的 `output` 已经是对象**，
   不是字符串。断言里 `JSON.parse(output)` 会炸（`output.slice is not a function`）——
   所以 `assert_case.js` 两种都收。
3. **断言自己的参数在 `context.config` 里**（不是 `context.vars`）。`vars` 是**每题**给的，
   `config` 是**每条断言**给的 —— 一道题有两条不同判据时只能靠 `config`。
4. **沙箱里 `npm install` 会挂在 esbuild 的 postinstall**（它 spawn 子进程，EPERM）。
   加 `--ignore-scripts` 即可（esbuild 的二进制来自 optionalDependency，不靠 postinstall）。
5. **必须 `--no-cache`。** promptfoo 的缓存键不包含"被评测服务的内容"，
   服务改了也可能命中旧结果 —— 那会得到一个与本次改动无关、却看着很合理的分数。
6. **判据红了，退出码也得红。** 第一版 `summarize.py` 只 `print` 了 `[FAIL]`，
   函数照样 `return 0` —— 这种判据接到钩子/CI 上等于不存在。
   （它是被"把对数改坏、期望变红"那条变异用例抓出来的。）
7. **离线层的拒答数不能拿来当"系统拒答能力"**：它是单闸（向量分 0.55）边界，
   产品默认路径走 online 层。两层数字不可比，只能并列看。

## 顺带修掉的一个 bug（`evalset/run_eval.py`）

`knowledge_hit()` 原来调 `svc.chat(req)`，而 `chat()` 自 2026-10-13 起是 FastAPI 端点
（签名 `chat(req, request)`）⇒ **整个 run_eval.py 跑不起来**，一跑就 `TypeError`。
改成调 `handle_chat()`（service.py 给"直连管道"的调用方留的入口，不带限流闸门）。
这个 bug 一直躲在 CI 之外：CI 只跑 pytest，没有一条用例真的执行这个脚本 ——
而屏幕上看不出来：README 与 `report-*.md` 里的旧数字还在，
没人重跑就没人发现"这些数已经算不出来了"。

## 还没做（HANDOFF 第 5 节待办 2）

- `eval/gate.py`：两次**同层**跑批的配对 McNemar（跨层直接拒绝比较）
- `eval/agreement.py`：人机一致性 κ（样本 <20 时拒绝输出 κ）+ 标注规范
