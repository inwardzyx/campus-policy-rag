# HANDOFF — 校园政策问答 RAG（交接文档）

> 快照时间：**2026-10-14**　·　仓库：`D:\_review_rag-cs`　·　`origin/main` = **`145d06f`**　·　工作区干净
> ★ 本文是**交接快照**，不是真相源：每条数字的**权威出处**是 `evalset/report-*.md|json`
>   与 `README.md`，且由 `scripts/check_*.py` 现算校验。**本文与产物冲突时，以产物为准**
>   （本项目的老规矩：过期的数字比没有数字更坏 —— 它看起来像实测）。

---

## 1. 项目目标

- **做什么**：一个校园制度问答 RAG 服务。自然语言问题进去，出去的是**带出处条款的答案**，
  或者一条**明确的拒答**（库里没有就不编）。语料是本校 9 份现行制度。
- **给谁用**：
  1. **求职作品集的门面** —— 面试官点开链接能看到会跑的网页（`GET /`）、自动生成的接口文档
     （`/docs`，截图在 `docs/images/demo-api-docs.png`）和一份把取舍/负结果都写清楚的 README。
  2. **一个 agent 实验台** —— `agent/` + `mcp/` 里有个 LangGraph 工具调用循环，用它回答
     "接了工具之后模型会不会少瞎说、值不值这个钱"（答案：贵 ~3 倍，硬答率降下来）。
  3. **作者本人** —— **不是**给真实学生用的线上服务：没有对外流量，也没部署（有意为之，见 §3.16）。
- **语料规模**（出处：`README.md` 顶部表 + 起服务后 `GET /guard-report`）：
  286 条原始材料 → **放行 263 块 / 拦下 23 条**（入库有六道关卡：太短、完全重复、近似重复、隐私、缺来源、旧版本）。
- **明确边界**：评测用的题集是**自造**的（`evalset/heldout/`），且**刻意不记录用户问题原文**（PII）
  ⇒ 真实重复率、缓存收益这类"要靠线上流量才知道"的事，本项目**结构上测不了**（这条写在
  `evalset/report-cache-feasibility.json` 的 `observability` 字段里，并由检查器强制）。

## 2. 当前进度

**已完成（服务层）**：`service.py` 现有 5 个端点 —— `/chat`（改写→粗捞→精排→生成）、
**`/search`（只检索、不生成，2026-10-14 新增）**、`/health`、`/limits`、`/guard-report`，外加网页 `/`。
检索 = 向量（fastembed `bge-small-zh-v1.5`，512 维）+ BM25（jieba）+ RRF 融合；
精排 = DeepSeek listwise 打分（0~10，**<5 硬短路拒答**，阈值常量 `VEC_REJECT_THRESHOLD = 0.55`）；
生成 = DeepSeek；usage 在线程内累加后随响应返回。

**已完成（限流/配额层）**：`limits.py` —— 令牌桶（Redis Lua 原子执行，`now` 由外部注入）+ 每身份日请求/日 token 额度
+ **全站额度**（`GLOBAL_KEY = "__all__"`）+ `fail_mode`（closed=护钱包 / open=保可用）；
没给 `REDIS_URL` 时退回进程内存储（**只对单进程有效**，启动日志会警告）。

**本次（2026-10-14）做完的事**：
- 新增 `POST /search` + `service.py::handle_search`；`mcp/tools_impl.py::search_policy` 从
  `/chat?use_rerank=False` **改道到 `/search`**（旧路上服务会白跑一次生成，而工具只读 `sources`）。
- **量出来的账**（先量后改，不是估的）：
  - A/B 配对 20 题：`sources` 逐字段一致 **20/20**、渲染文本**逐字节一致 20/20**；
    `/search` 合计 **0 token**，旧路径同题 **5,912 token** ⇒ **每次调用省 369.5 token**
    （出处 `evalset/report-search-ab.{md,json}`，由 `scripts/check_artifact_sanity.py` 校内部自洽、
    `scripts/check_readme_numbers.py` 校 README 与它一致）。
  - agent 全链单遍：`search_policy` 13 次 **6,337 → 0 token**；工具侧 **1,314 → 973 token/题**；
    **含工具倍率 3.36× → 3.05×**（两次单遍运行，抖动不算精度；配对数字以 A/B 为准）。
- `limits.py`：**可证明的 0**（`n_calls=0`）不再记成 `usage_missing`（假痕会稀释真缺口）；
  `usage=None`（拒答路径"没拿到账"）仍然留痕。
- `scripts/verify_limits_e2e.py` 从 7 项扩到 **8 项**：第 ⑧ 项在**部署形态**下验 `/search`
  （给出处 / `usage.n_calls=0` / 拒答成立），② 里顺带验"桶空了 `/search` 也必须 429"。

**判据与变异现状**（都在本机真跑过）：
- `pytest tests -q` → **123 passed / 22 skipped**（跳过的两组需要 Redis；当时 Memurai 没起）。
- `scripts/mutate_check_artifact.py` → **18 条变异全检出**；`scripts/mutate_check_numbers.py` → **21 条全检出**。
- 本机 e2e（真 uvicorn `:8011` + Memurai AOF 开）→ **八项全过**。
- 反向验证：把 `_zero_usage()` 的 `n_calls` 改成 1 ⇒ ⑧ 当场报红，报出的正是后果本身；已还原（`git diff service.py` 为空）。

**提交进度**：`origin/main = 145d06f`。最近三个提交：
`145d06f`（e2e 补 ⑧）→ `5a72432`（`/search` 改道 + 判据 + 产物）→ `b2c8863`（heldout 构造流水线入库）。
**CI 已知绿灯到 run #53（`b2c8863`，三 job 全绿：快 27s / 慢 2m49s / 容器 4m25s）；`5a72432` 与 `145d06f` 的 run 尚未确认**
（不是没跑，是作者本机查不到：没有 `gh`，且 `github.com`/`api.github.com` 解析到非公网 IP）。

**停在哪儿 / 还没做的**：
- agent 侧只有 **token 账**，没有"工具选错率 / 多轮成本"的度量（报告自己写着"工具选错导致多轮的情况本报告覆盖不到"）。
- `ask_policy` 的返回体积**没压**（整段答案 + 出处），而 README 明写优化顺序是"先压工具返回的体积"。
- 线上部署**刻意不做**：只做到"一条命令能起"（compose）+ CI 容器 job 验证。

## 3. 关键决定（每条一行：选了什么 + 为什么）

1. **混合检索（向量 + BM25 + RRF）而不是纯向量** —— 实测两类翻车：口语"东西坏了能修吗"（文档写"保修"）纯 BM25 找不到；"A1001 运单号"纯向量前两名只差 0.008。
2. **RRF 用名次不用原始分** —— 向量分 0~1、BM25 分能到十几，量纲不同直接相加会被 BM25 淹没。
3. **本地小 embedding（fastembed bge-small-zh-v1.5，512 维）而不是调 API** —— CPU 可跑、无外部依赖；代价是启动要 ~11.4 s（README 实测）。
4. **精排交给 LLM（0~10 分，<5 拒答）而不是向量阈值** —— 263 块语料上两组分数**已经交叉**，`evalset/probe_threshold.py` 扫 0.30~0.95 找不到能同时放行全部应答题、拦住全部拒答题的阈值。
5. **拒答用"硬短路不喂资料"而不是提示词劝阻** —— 提示词会漏，喂了假资料模型就会照着编；可靠的做法是不喂。
6. **配额做三层（令牌桶 + 每身份日额度 + 全站日额度）** —— 有 key 在公网没配额等于把钱包挂门口；**全站额度是盲审 R1 逼出来的**（每身份一份时，换个 IP 就换一份新额度，闸门全绿而账单照烧）。
7. **判定放 Redis 用 Lua 原子执行、`now` 外部注入** —— 读-改-写分两次往返有竞态；注入 `now` 才能测时钟回拨与跨天。
8. **记账口径：拿不到就记 `usage_missing`，不许补 0；反过来"可证明的 0"不许记缺口** —— 补 0 让缺口看起来像"真没花钱"；假痕一多，真缺口就看不出来了。
9. **闸门 + 记账抽成 `_gated()`，`try/finally` 无条件记账** —— R4 那次任何异常都会绕过日额度，结果只扣了桶里一枚令牌。
10. **工具侧报账走 SIGNAL 标记行（一行中文 + `key=value` 尾巴）而不是 JSON** —— 模型读得懂、脚本能精确 parse，不用猜措辞。
11. **`/search` 只检索不生成，请求体刻意不留 `use_rerank` 开关** —— 整条链（工具报账 / 报告 / README 数字）都建立在"它恒 0 次 LLM 调用"上，留开关就是留后门。
12. **`/chat` 与 `/search` 共用 `_vec_reject()` / `_vec_sources()` / `_format_sources()`** —— 让"两条路给出不同答案"在**结构上不可能**，而不是靠注释承诺。
13. **每条判据都要能反向验证（配变异）；扫到 0 条/空转必须报红** —— 这个仓库吃过"探针只照一面，把没报错读成没问题"的亏。
14. **产物合理性检查（`scripts/check_artifact_sanity.py`）** —— 2026-10-11 坏环境跑出的产物把好产物覆盖成 `Recall 0/15`，README 跟着全错；现在"明显不是跑出来的"一律拦。
15. **放弃答案缓存（有依据的负结果）** —— 结构性理由只有一条：`rerank()` 的 prompt **逐字包含 query**，复用候选集会静默给出错误排序（`0.3136 < 0.7606` 是旁证，"0/20 精确重复"只是观测边界，不是证据）。
16. **放弃"多轮自选查询词"** —— 实测救回 **0 道**、误放行 0 道、成本翻 3 倍。
17. **部署只到"一条命令能起"（compose：service + redis，`--appendonly yes`），不部署线上** —— 作品集要的是"能起 + CI 验证"，不是运维负担。
18. **CI 红不拦合并（知情取舍）** —— 快/慢/容器三个 job 成本差约 500 倍（0.03s vs 15s 量级），拦住合并会把"跑得慢"变成"提交不了"。
19. **不记录用户问题原文（PII）** —— 代价是真实重复率不可观测；这条边界写进 README 与缓存产物，并由检查器强制。
20. **文档里的数字必须能从产物现算** —— 手写的数字迟早过期，而过期数字比没有更坏。

## 4. 踩过的坑（问题 → 怎么解决；没解决的标"未解决"）

- [已解决] **CI 快 job 红**：pytest 环境没装 redis 客户端 → `REQUIRE_REDIS=1` 直接失败（不是跳过）。修：CI 装 `redis==8.1.0`。
- [已解决] **重启类断言是假绿**：③⑦ 原来只检查重启命令退出码 0，`--restart-cmd "true"` 也能全绿。修：用 `boot_id`（服务）与 `redis run_id` 证明**重启真的发生过**。
- [已解决] **`subprocess.run(restart_cmd, capture_output=True)` 永久卡死**（孙进程持有管道，`timeout=` 救不了）。修：`DEVNULL` + `wait(timeout)` + `line_buffering=True`（否则卡住看不见）。
- [已解决] **记账漏在异常路径（R4）**：只在 `return` 前记账 ⇒ 抛异常时客户端**只在桶里扣了令牌**。修：`_gated()` 的 `finally` 里无条件记账。
- [已解决] **一次 `/chat` 的三次调用被记成一次**（`n_calls` 恒为 1、token 少算 58%）：覆盖写成累加 + `n_calls` 算的是一个从没人写过的线程属性。修：让假模型每次返回**不同**的 token 数才暴露出来；`_take_usage()` 真正写线程属性。
- [已解决] **指标重复计数**：本地 + 远端各加一遍，`/health` 报的是真实值的两倍。修：本地只装"没进远端"的部分；回归用例按**增量**断言。
- [已解决] **探针改错行**（文件里有两行内容完全相同）+ 缺 CRLF 容错。修：用更长的固定 anchor + 归一化 `\r\n`。
- [已解决] **pytest 经管道捕获的输出是 GBK** ⇒ 探针匹配中文串失败。修：`PYTHONIOENCODING=utf-8`。
- [已解决] **脚本打印 `⇒`/`✓` 在 GBK 下抛 `UnicodeEncodeError`，而报出来的那句话是假的**（钩子说"产物不合理"，实际一行都没检查）—— 已中 7 次。修：`sys.stdout.reconfigure(encoding="utf-8", errors="replace")`，且必须在**第一个模块级 `print` 之前**（`tests/test_guard.py` 有守门用例）。
- [已解决] **变异套件红了一整天没人知道**（重测后 anchor 过期）。修：更新 anchor 并把三套变异挂进 CI 快 job。
- [已解决] **一次重定向把协作方写的评审文件覆盖了**。修：从 `~/.claude/projects/**/*.jsonl` 里 Write 工具的入参恢复两份全文。
- [已解决] **协作方在 `limits.py` 留下 `TEMP-MUTATION` 却声称"仓库一字未改"**。修：等它跑完再还原；此后自己做完反向验证也一律用 `git diff <file>` 确认干净。
- [已解决] **e2e ⑧ 顺序放错**：② 先把令牌桶打空 ⇒ ⑧ 拿到 429 而不是 200（当场报红发现的）。修：⑧ 移到 ② **前面**，跑完把借的两枚令牌还回去（复用 `reset_client_state`），否则 ② 的"突发 N 次"不成立。
- [已解决] **Windows 上 `powershell -File x.ps1 | Select-Object -Last 1` 永不返回**（被启动的进程继承了 stdout 句柄）。修：别接管道。★ 附带一条：**前台命令超时会被整棵进程树杀掉**，会顺手带走刚起来的服务/Redis（本次踩了两次，症状是"服务突然没了"）。
- [未解决] `TRUST_PROXY_HEADERS=1` 时信任 `X-Forwarded-For` **首跳** —— 反代不清洗就等于把身份交给调用方伪造。
- [未解决] `/health` 依赖 Redis 延迟：存储连不上时健康检查也慢（本机实测连接超时那段时间 5 s 内不返回）。
- [未解决] `RATE_PER_MIN=0` 是"**永久拒绝**"而不是"不限"（只有 `DAILY_*=0` 才是不限）—— 极易配错，配错就是全拒。
- [未解决] `fail_mode=open` 时**不记账花费**（Redis 挂了照样放行且不留痕）。
- [未解决] `scripts/check_judge_rules.py` 有一条**按名字判定**的弱判据；另有一对"同形状"变异（检出但不携带信息量）。
- [未解决] **没有对外流量 + 刻意不记录问题文本** ⇒ 真实重复率结构上不可观测（所以缓存收益无法测量，这不是"没测"，是"测不了"）。
- [未解决] **本机跑 e2e 要手工起 Memurai 与 8011 服务**（脚本不自带启动）—— 见 §5.3。

## 5. 下次从哪接（按优先级）

1. **先确认 CI**：去看 `5a72432` 与 `145d06f` 两次 run 的绿灯。重点看**慢 job**（新用例 + 变异）与**容器 job**
   （八项 e2e；若红，第一眼看 ⑧ 那行 —— 它失败时会直接把后果打出来：`README 里「工具侧省下 6,337 token」就作废了`）。
   作者本机查不到 CI（见 §2），这是唯一一处"别人比作者更方便"的地方。
2. **压工具返回体积**（README 明写的优化顺序，也是唯一还没动的成本大头）：agent 侧贵在 **input（+120%）**，
   多出来的是每轮把工具结果（检索块全文 + 精排分）重新塞回上下文。当前 `search_policy` 只截 200 字，
   `ask_policy` 返回整段答案 + 出处。做法照这次的套路：**先量**（`python agent/token_cost.py --runs 1 --n 20 --out <另存一个产物名>.json`
   已经支持按工具名拆账，★别覆盖 `agent/_token_cost.json`）→ 改 → 用**配对 A/B** 证明"模型看到的东西等价"
   （抄 `scripts/ab_search_vs_chat.py`，产物入 `evalset/` 并登记进 `check_artifact_sanity.py`）。
3. **给 agent 补"工具选错率 / 多轮成本"的度量**（现在只有 token 账）：在 `agent/policy_agent.py` 的循环里记
   每轮选了哪个工具、是否被驳回、多轮是否必要，产物入 `evalset/` + 配检查器。这是报告自己标出来的空白。

**最小上手路径**（不依赖仓库外的任何脚本）：
```powershell
# 0) 依赖与语料：docs/kb/*.md 已入库（10 篇）；加文档 = 往 docs/kb/ 或 docs/inbox/ 丢一个带 front matter 的 .md
$py = "D:\Python-project\.venv\Scripts\python.exe"
# 1) 起一个"带真闸门"的本地服务（跑 e2e 用；需要本地 Redis，本机用 Memurai 并开 AOF）
$env:REDIS_URL="redis://127.0.0.1:6379/0"; $env:RATE_BURST="3"; $env:RATE_PER_MIN="1"
$env:DAILY_REQ_QUOTA="5"; $env:LIMIT_FAIL_MODE="closed"; $env:PORT="8011"; $py service.py
# 2) 起一个"限额全关"的服务（量 token 用；限流会把测量打断成 429）
$env:RATE_BURST="1000000"; $env:RATE_PER_MIN="1000000"; $env:DAILY_REQ_QUOTA="0"; $env:DAILY_TOKEN_QUOTA="0"; $env:PORT="8000"; $py service.py
# 3) 一条命令起完整形态（部署形态）
cp .env.example .env   # 填 DEEPSEEK_API_KEY
docker compose up -d --build      # 网页 http://127.0.0.1:8000 ，接口文档 /docs
```
（作者本机还有三个不入库的小脚本 `D:\dsh-work\_svc.ps1`（8011 带闸门）、`_redis.ps1`（起 Memurai，`-AppendOnly 1`）、
`_svc_measure.ps1`（8000 限额全关），它们只是上面那几行 env 的包装。）

## 6. 关键文件路径（每个一句话）

**服务与数据层**
- `service.py` —— 服务本体：检索/精排/拒答/生成管道 + `/chat`、`/search`、`/health`、`/limits`、`/guard-report`、网页；`_gated()` 是唯一的闸门+记账实现。
- `limits.py` —— 令牌桶 + 每身份日额度 + 全站额度 + 故障模式；顶部有一段"逐条取舍"的长注释。
- `kb/loader.py` —— 入库把关：把 `docs/` 树下的 `.md` 读成块，六道关卡拦脏数据（`docs/kb/` 先于 `docs/inbox/`）。
- `docs/kb/*.md` —— 已审核语料（10 篇）；`docs/inbox/网页抓取残留.md` 是待审核的那一篇。
- `Dockerfile` / `docker-compose.yml` / `env_compat.py` / `requirements*.txt` / `pytest.ini` —— 部署与依赖。

**agent / MCP 层**
- `mcp/tools_impl.py` —— 两个工具的实现（`search_policy`→`/search`、`ask_policy`→`/chat`）+ SIGNAL 标记行 + `_format_sources()` 唯一渲染。
- `mcp/server.py` / `mcp/tool_schemas.py` / `mcp/client.py` —— MCP server、工具 schema、跨进程客户端。
- `agent/policy_agent.py` / `agent/langgraph_agent.py` —— tool-calling 循环（LangGraph）。
- `agent/token_cost.py` —— 直打 vs agent 同批计量，按工具名拆账；`--out` 指定产物（**别覆盖 `agent/_token_cost.json`**）。
- `agent/refusal_regression.py` / `agent/render_regression_report.py` —— 拒答回归（61 题 × N 遍）+ 报告生成器（数字全从 JSON 读）。
- `agent/verify_direct_unchanged.py` —— 证明"补计量没改动模型行为"（只比结构化不变量）。

**判据 / 检查器（改代码后一定要跑）**
- `tests/test_guard.py` —— 守卫用例（拒答、阈值、闸门、只检索端点、脚本编码守卫…）。
- `tests/test_limits.py` —— 限流/配额逻辑，两种存储各跑一遍。
- `tests/test_eval_set.py` / `tests/test_llm_resilience.py` / `tests/test_loader.py` —— 评测集行为 / LLM 降级路径 / 入库。
- `scripts/check_readme_numbers.py` —— README 的每个数字 ↔ 产物现算（21 条变异守着它）。
- `scripts/check_artifact_sanity.py` —— 产物合理性（含清单反查：新产物忘登记会红）。
- `scripts/check_token_report.py` / `check_judge_rules.py` / `check_file_refs.py` / `check_push_paths.py` —— token 报告对账 / 判据纪律 / 路径引用 / 禁推模式（0 条路径必须报红）。
- `scripts/mutate_check*.py`（4 套）+ `scripts/install_hooks.py` —— 反向验证与本地钩子安装。
- `scripts/verify_limits_e2e.py` —— 真服务 + 真 Redis 的**八项**端到端验证（CI 容器 job 与本机同一份）。
- `scripts/ab_search_vs_chat.py` —— `/search` vs `/chat?use_rerank=False` 的配对 A/B（产物 `evalset/report-search-ab.*`）。

**评测/实验/证据**
- `evalset/report-*.md|json` —— 检索、重排、拒答回归、token 成本、缓存可行性、只检索端点 A/B 的**报告与产物**。
- `evalset/probe_*.py` —— 各层探针（阈值、融合、池大小、改写、rank…），结论都写在 README 对应小节。
- `evalset/heldout/` —— 独立题集：`questions.json`（44 应答题 + 61 拒答题）、`_pipeline/`（构造脚本）、`_blind_out/`（三轮盲答）、`source_policy.md`（该题集对应的语料）。
- `experiments/` —— 学习/演示脚本（LangGraph 四块、成本拆分等），**不是生产路径**。

**协议与门面**
- `CLAUDE.md` —— 给 AI 协作方的硬规矩（含"假红是假绿的入口"这条纪律）；`scripts/check_judge_rules.py` 会核"规矩有没有对应实现"。
- `README.md` —— 门面：结论、数字、取舍、已知边界（**要改数字先看它对账器**）。
- `_tasks/`（`PROTOCOL.md`、`dispatch.sh`、任务卡模板）与 `_reviews/`（cc/dsh 的评审结论）—— 多 agent 协作的凭证。
- 根目录 `cc_*.md` —— 与另一个 agent（cc）协作时的提示词与产出（成对：`cc_xxx_prompt.md` → `cc_xxx.md`）。
- `.github/workflows/ci.yml` —— 三个 job：快（加载器+限额，不装模型）/ 慢（守卫+评测+降级，装模型）/ 容器（compose 起服务 + 八项 e2e）。

## 7. 约定与偏好（照做，别重新发明）

- **语言与注释**：中文注释与文档；注释解释**为什么**和**踩过的坑**，不写"这行是赋值"式的废话。
- **数字纪律**：任何数字都要有**产物出处**，且优先让对账器**现算**（README/report ↔ 产物）；不许手写、不许把估算说成实测。
- **判据纪律**：① 每条判据都要能被**反向验证**（配变异用例）；② 扫描到 0 条/空转必须**报红**；③ "检出 ≠ 证明"（同源变异要如实标出）。见 `CLAUDE.md` 的 3 条硬规矩。
- **记账纪律**：不补 0、不把"没拿到"混成"确实是 0"；"可证明的 0"（`n_calls=0`）不算缺口。
- **安全/隐私**：知识库文本一律 `textContent` 渲染（**禁 `innerHTML`**）；**不记录用户问题原文**；`.env` 不入库。
- **脚本纪律**：打印 `⇒`/`✓`/`⚠` 的脚本必须在**第一个模块级 print 之前** `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`；跨进程捕获中文要 `PYTHONIOENCODING=utf-8`；中文串里嵌引号用 `「」`/`『』`（**不用 ASCII 双引号**）。
- **目录约定**：结论与出处分开 —— 报告/产物进 `evalset/`；一次性中间产物用 `_` 前缀并写进 `.gitignore`；**新产物必须登记进 `scripts/check_artifact_sanity.py`**（有反查，漏登记会红）。
- **加新入口（端点/工具）**：必须走 `_gated()`（闸门 + 无条件记账），并在 `scripts/verify_limits_e2e.py` 里补**部署形态**的断言 —— pytest 打的是进程内 `TestClient`，看不到接线那一层。
- **改造成本相关的东西**：先量（`agent/token_cost.py`）→ 改 → **配对 A/B** 证明"模型看到的东西等价"（抄 `scripts/ab_search_vs_chat.py`），别用两次单遍运行的差值当结论（抖动会骗人）。
- **提交纪律**：一批改动一个 commit，中文 subject（`feat(scope): ...` / `fix(...)` / `docs(...)` / `ci(...)`）；提交会过 pre-commit（数字对账）与 pre-push（判据纪律 / 路径引用 / token 报告）—— 两个钩子都必须绿；CI 红不拦合并，但**要有人看**。
- **环境**：Windows + PowerShell；Python 用 `D:\Python-project\.venv\Scripts\python.exe`；本地 Redis 用 Memurai（要开 AOF，否则 e2e ⑦ 会红且**红得有道理**）；量账用的服务必须**关掉限额**。
- **与外部 AI 协作**：任务卡与产出放 `_tasks/`、`_reviews/` 或根目录 `cc_*`；重要评审要**阶段性盲审**（不给对方我的结论），评审结论入库当凭证。

---

### 接手自评（作者写的，供你判断）

一个完全没参与过的人**能不能接手继续干**：**能，前提是他照 §5 的第 1 步先看 CI、并接受 §3.19 那条边界**（
没有线上流量 ⇒ 有些问题在这台机器上永远测不出结论）。他需要自己准备的只有一样东西：**一个可用的
`DEEPSEEK_API_KEY`**（没有也能起服务、能跑快 job 与大部分用例，只是 `/chat` 的命中题会 500 —— 这是刻意的，
缺 key 属于配置错误，不许被包装成"调用失败"）。仓库外没有任何**必需**的东西：本机那三个 `.ps1` 只是
§5 那几行 env 的包装，已经写进文档了。
