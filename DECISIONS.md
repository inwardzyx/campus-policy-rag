# DECISIONS —— 评测这件事：用什么现成的、自己补什么、什么不做

> 一页面试材料。每条都指到代码/产物；**判据不响的地方如实标出来**，不写成已做。
> 2026-11-11 盲审后重写（第一版里「replay 被 promptfoo 缓存覆盖」这条理由被本仓自己的
> `--no-cache` 反证过，已按实测改正 —— 见 §3）。

## 1. 一句话

这个仓库的评测不是"建平台"，是**一条可复核的流水线**：

```text
promptfoo 调度 → case_server 进程内跑真管道 → 判分复用 evalset/run_eval.py
→ 报告（数字全现算 + 运行指纹）→ 配对显著性（kb/loopgate）+ 人机一致性（eval/agreement）
```

对外话术只有一句：**评测资产版本化 + 运行指纹**。
不说"数据中台"—— 判据该被追问的是"会不会响"，不是"多租户怎么隔离"。

数据层不建接口：它已经存在，且只有一个消费者。

| 已有 | 承担什么 |
|---|---|
| `kb_data/*/manifest.json` | 数据集版本（content_hash / acl / status / effective_date） |
| `kb/runstate.py` | 运行指纹 / 血缘（git sha + worktree diff + 逐文件语料 sha256 + env） |
| `kb/evalkit/metrics.py` | 指标登记与缺失语义（`desc` / `unit` / `missing` 三样必备） |

`evalset/run_eval.py` 是 `import service` 直调，**没有进程边界** ⇒
再插 `get_dataset()/lineage()/metrics_spec()` 就是给单人单仓加一层没人调的间接层。

## 2. promptfoo 有什么 / 我补什么 / 我为什么不补

promptfoo（27k star）官方把断言分三层：确定性断言、模型判（rubric）、轨迹/trace-span。
它还有缓存、报告、CI 退出码。**这些它做得比我快**，所以我不重写。

| 能力 | promptfoo | 我补在哪 | 为什么补（它真没有 / 或不宜用它） |
|---|---|---|---|
| 调度、聚合、退出码、报告 UI | ✅ | 直接用 | 重写没有收益 |
| 确定性/模型判/轨迹断言 | ✅ 三层 | `eval/assert_case.js` 只写**业务判据**（recall5 / no_miss / refuse_ok），口径来自 `run_eval.py` | 判据必须复用产品那条路（`rag.search()` 的 top-5，**不是** `/chat` 的 sources），否则是第二把尺子 |
| 缓存（省 token） | ✅ | **强制关掉**（`--no-cache`） | 实测：缓存键只含 provider+prompt+变量，**不含被测服务的内容** ⇒ 服务改了也会命中旧结果（坑见 `eval/README.md` 第 5 条）。关掉是**实测结论**，不是保守 |
| 配对显著性（McNemar） | ❌ | `eval/gate.py`（**再导出** `kb/loopgate.py`，不新写）+ `eval/reports/` 逐题 JSON | 它只报通过率；「改好 3 题、弄坏 1 题」在通过率上看不出，必须配对 |
| 评分者一致性 κ | ❌ | `eval/agreement.py` + `eval/annotation-rubric.md` | 答案质量只能人判；人判就要知道"人和系统一致吗" |
| **被测服务的状态指纹** | ❌ | `kb/runstate.py`，报告头落指纹 | 它的缓存键没有"被测服务版本"这个概念 ⇒ 两次跑批是不是同一个系统，它答不了 |
| 变更分级调度（小/中/大） | ❌ | **不补** | 唯一来源是两篇单公司经验帖（弱证据）；单人单仓用不着分级，纪律一句话够：**改了判据/语料就全量重跑；只改文案就抽样 20 题冒烟** |
| 录制回放（replay） | 缓存**不是**替代品 | **不补** | 见 §3 |

## 3. 为什么不做 replay（这条第一版是错的，已改）

- ❌ 错的说法：**「replay 被 promptfoo 的 caching 覆盖」** —— 本仓自己强制 `--no-cache`，
  这句话被自己的配置直接反证。
- ✅ 实测的理由：缓存键**不含被测服务内容**（2026-11 实测，写进 `eval/README.md` 坑 5），
  所以缓存根本不能承担"复现同一次运行"的职责。
- ✅ 真正承担复现的是两样东西：
  1. `kb/runstate.py` 的**工作区快照**（git sha + `worktree.diff` 的 sha + 未跟踪文件的聚合哈希
     + 逐文件语料 sha256）⇒ 告诉你"当时跑的是哪个状态"；
  2. `kb/evalkit/transport.py::RecordedTransport`（`transport.py:31-52`）⇒
     `{case_id: response}` 的录好响应，离线重放不用调模型。
- ⇒ 结论：**不需要 replay 模块**。缺的那一层（"重放同一次模型输出"）已经由 RecordedTransport 完成，
  而"同一个系统"由 runstate 指纹保证。这两个加起来比一个 replay 模块更诚实：
  replay 模块最容易骗人的地方是"回放成功 = 结果可复现"，而它可能回放的是一份**旧代码**的响应。

## 4. registry / 版本：为什么砍掉模块，而不是砍掉这件事

砍的是**模块**，不是职能。职能由双件套承担，另补一件：

| 资产 | 谁管版本 | 缺口 / 已补 |
|---|---|---|
| kb_data 企业语料 | `manifest.json` 的 `content_hash` | **已知边界**：绕过上传 API 直接改文件，manifest 不变 ⇒ 指纹不变。已在 `kb/runstate.py::corpus_state` 的 docstring 明写（文件对账判据**尚未实现**） |
| `docs/` 默认语料 | `runstate.docs_state()` 逐文件 sha256 | 无缺口 |
| **held-out 105 题** | 原来是**裸的**（只有自由文本 `_说明`） | **2026-11-11 补上**：`evalset/heldout/questions.sha256` + `scripts/check_heldout_sha.py`（pre-push 跑）+ `tests/test_heldout_sha.py` 的变异用例（改一个字符必须红） |

## 5. 案例：旧报告的 35/44 与今天的 30/44（这条是本仓最好的一课）

- 2026-10-10 的 `evalset/report-vector-heldout.md`（历史快照，勿引用） 写 Recall@5 **35/44**；今天跑出来 **30/44**。
- 一开始我写的是"检索退化了，此后 65 个 commit" —— **这是猜的**。
  而且当时还有一个更糟的对手假设：旧报告用的是旧入口（`chat(req)`），今天走 `handle_chat()`，
  两个数**可能根本不可比**。
- 于是做了 `eval/repro_old_code.py`（`git worktree` 拉出 `a9e4f85`，三种组合各跑一次，
  落盘 commit sha + questions sha + docs 语料树 sha + 判分脚本 sha + 入库块数）：
  | 组合 | 结果 |
  |---|---|
  | 旧脚本 + 旧代码 | 与旧报告**逐项一致** |
  | 当前脚本 + 旧代码（带 `handle_chat = chat` shim） | **与上一行完全一致** ⇒ 入口不是变量 |
  | 当前脚本 + 当前代码 | 今天基线 |
  且 `questions.json` 与 `docs/` 语料树 sha256 **逐字节未变**，**docs 块数与 chunk 文本 sha256 三组全同**
  ⇒ 评测索引逐字节没变（"索引是不是变量"这一步是被量掉的，不是假设的）。
- ⇒ 结论：两组数**可以相减**，差异只能来自检索/判据**代码**；但"是哪一处代码"**没定位**（没做 `git bisect`，
  也不做 —— bisect 的前提是"入口一致"，这个前提本身要先被证明）。
- 顺带自己抓到一次误读：我第一版拿 `len(rag.chunks)` 的**合计**（263 docs + 69 块本机 demo 企业语料
  = 332）去跟旧版的 263 比，得出"切分/入库关卡变了"。实际评测请求不带 namespace，
  `rag.search()` 只查 default 语料 ⇒ 那 69 块不参与。**"合计"和"评测用的那部分"是两个口径**，
  混起来就会把"我本机多装了几份 demo 数据"读成"系统变了"。
- 这件事证明了什么、没证明什么：证明「①改清洗规则→自动算出影响面」**是假绿**（依赖映射不存在，
  靠重跑才发现）；没证明根因。**把它当案例写在这里，比把它写成能力更有用。**

## 7. 报告新鲜度：一条命令算出「哪些结论过期了」

### 为什么要它

`scripts/check_readme_numbers.py` 比的是「README ↔ 产物」。问题是：**两份一起过期就是"一致"= 绿**。
2026-10-10 的 35/44 就是这样躲了两周。补的另一半是「产物 ↔ 当前代码与语料」：
`scripts/check_report_freshness.py`（CI 快 job，`--strict`）。

四态：`FRESH` / `STALE_CODE` / `STALE_DATA` / `STALE_BOTH` / `UNVERIFIABLE`。
只比**三个字段**：`git sha`、`docs/kb` 哈希、`docs/inbox` 哈希 —— **不比 runstate 的聚合指纹**，
因为后者含 dirty 与工作区未提交文件，而报告自己就在工作区里 ⇒ 改一次报告指纹就变，永远 STALE。

### 我认的一个错：`sha == HEAD` 对已入库报告不成立

原规格写「报告头 git_sha == 当前 HEAD 才算 FRESH」。实测走不通：报告在 HEAD=X 生成，
作为新提交 C 入库后 HEAD=C≠X ⇒ **任何入库报告一出生就 STALE** ⇒ 常红 = 把警报贴住。

→ 采纳的口径：`sha 相同` **或**（`sha` 是 HEAD 祖先 **且** `git diff --name-only sha..HEAD`
里没有"会影响结果"的文件）。排除清单是**排除式**（`NON_AFFECTING`）：清单外一律算影响
⇒ **宁可误报 STALE，不可漏报**。想回到字面口径：`--strict-sha`。

排除清单里有一条**带代价的取舍**，写在这里备案：`eval/summarize.py` 算"不影响结果"
（它只把已记录的跑批折成 md，数字来自 JSON）⇒ **md 与渲染器是否同步，这条判据不管**；
改过渲染逻辑要自己重生成 md。数字定义的正确性由 `tests/test_eval_reconcile.py` 与
`summarize --compare` 守。

### 豁免挂在【引用】上，不挂在【报告】上（选 B）

规格给了 A / B 两条，选 **B（引用级标记）**，理由：同一份文件里既有"历史段"也有"现役段"，
A（按文件白名单）表达不了这种区分；B 更通用，代价是要在引用处写标记 —— 而这个标记本身有信息量。

判据的语义因此变成：

| 情形 | 合法吗 |
|---|---|
| 报告过期（历史快照本来就该过期） | ✅ 合法 |
| 引用过期报告，且引用行**标了**历史/记录标记 | ✅ 合法 |
| 引用过期报告，且引用行**没标** | ❌ 不合法（这正是 README 引用不可复现数字的那个坑） |

★ 这条是**实跑发现**才改对的：第一版把豁免挂在报告上（`∈ HISTORICAL ⇒ 永不放行`），
结果「一份 UNVERIFIABLE 的报告被未标历史地引用了两次，检查器仍 exit 0」——
「未标历史」那列成了装饰。现在红的时候会点名到 `文件:行号`。
标记词（检查器识别）：`历史快照` / `勿引用` / `实测记录` / `旧代码` / `已过期` / `复现不出` / `不可复现` / `过期声明`。

### 噪声地板：gate 的比较基准要能指出处

`eval/noise_floor.py` 用"同配置重跑 N 次"量出极差，写 `reports/noise-floor.md` + `.json`，
每次跑的**逐题结论**落在 `reports/noise/run-*.json`（表里每一格都能指回去）。
`eval/gate.py` 读那份 JSON 打印地板；没量过就明说"未量"（缺失 ≠ 0）。

★ 量的时候有个坑：`service.py` 对 **rewrite / rerank** 有 LRU 缓存（`CACHE_ENABLED`，默认开）。
同一进程连跑会命中缓存 ⇒ 得到"完全一致"的假象——那量的是缓存，不是模型噪声。
⇒ 量噪声必须：**每次全新进程 + `CACHE_ENABLED=0`**，并把这件事写进产物。

## 6. 不做什么（明确排除，避免反复讨论）

- ❌ 不做 `git bisect`（入口/语义要逐版本验证，成本远高于收益；见 §5）。
- ❌ 不建数据层接口 / `registry` 模块 / `metrics_spec` 模块（职能由 manifest + runstate + 各脚本内常量承担）。
- ❌ 不做变更分级调度、不做血缘图、不做多租户评测隔离。
- ❌ 不把 κ / 血缘 / 变更分级写进简历第一屏（应用岗不看这些词）。
- ✅ 只做一件多余的都不做：`gate` 再导出 `kb/loopgate.py`，不重写统计。
