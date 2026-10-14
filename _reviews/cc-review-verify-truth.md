> ★ 入库说明：这是 **cc（独立上下文）的盲审报告**，原文照收，只加这一行来源说明。
> 审的人是另一份上下文里的 agent，**它没看到我的任何结论**；卡里明确要求
> "不许把注释/docstring/README 里的自我评价当证据"。逐条复核后的处置见
> README〈盲审发现的缺口：哪些修了、哪些只记下来〉。
> 原始任务卡已一并入库：`cc-task-redteam-limits.md` / `cc-task-verify-truth.md`（它就是当时给它看的东西，含全部约束）。
# 判据真实性盲审（cc 署）

日期：2026-10-13 · 范围：`main` 上 `700bc0e..a380b9d` 里承担"验证"职责的东西 ·
被审仓库：`D:\_review_rag-cs` @ `a380b9d` · 环境：本机（无 Docker/WSL）、
Redis = Memurai 已起（我用 `/13` 库做隔离实验；`/0` 全程未动）

---

## 1. 我要回答的问题（复述）

1. 哪一条断言在被测行为坏掉时仍然会绿（逐条给出"改成什么 ⇒ 还是绿"）；
2. `verify_limits_e2e.py` 七条里有没有能在系统坏掉时全绿的（含 ① 与 ③④⑤⑦ 的依赖、
   对服务配置的假定、`who/day` 取值、"先清空自己"会不会清掉要测的东西）；
3. 反向验证（"拿掉 X ⇒ 用例必须红"）本身可信吗——至少核实三条，"拿掉"是真的行为变更吗；
4. 变异测试有没有"同源变异"、有没有"没注入成功却不算失败"的路径；
5. CI 里有没有"看起来在跑、其实跑不到"的步骤；
6. README"四层证据"哪一层比它的证据强；
7. 没问到但真正危险的判据真实性问题。

---

## 2. 我实际读了什么 / 跑了什么

### 读了（全文或关键段）

- `tests/test_limits.py`（全 404 行）、`tests/test_guard.py` 的〈限流+日配额〉组（900–1100 行）
- `scripts/verify_limits_e2e.py`（全）、`scripts/check_file_refs.py`（全）、`mutate_check_refs.py`（全）
- `scripts/check_judge_rules.py`（全）、`mutate_check_numbers.py` / `mutate_check_token.py` /
  `mutate_check_artifact.py`（全）
- `.github/workflows/ci.yml`（全）、`scripts/hooks/pre-commit`、`pre-push`（全）
- `README.md`〈跑起来只要一条命令〉一节（1688–1763 行）
- `limits.py`（全 487 行）、`service.py` 的 `/health` `/limits` `/chat`（875–975 行）、
  `tests/conftest.py` 开头、`scripts/install_hooks.py`（119–278 行）、
  `scripts/check_push_paths.py`（62–162 行）、`docker-compose.yml`、`.github/ci-compose.override.yml`

**没读全（明说）**：`check_readme_numbers.py` / `check_artifact_sanity.py` /
`check_token_report.py` / `check_push_paths.py` 前半 / `test_guard.py` 其余约 900 行 /
`test_eval_set.py` / `service.py` 其余约 1050 行。
对这些文件的结论我是**经由变异套件实跑**间接得到的（它们在变异跑动时被真实调用），
没有逐行读——所以"判据内部逻辑是否自洽"这类问题我可能漏。

### 跑了（命令 + 关键输出）

| # | 命令 | 结果 |
|---|---|---|
| 1 | `pytest tests/test_limits.py`（`TEST_REDIS_URL=.../13`） | **38 passed**（两种存储都跑了）|
| 2 | 同上，但 `TEST_REDIS_URL` 指向不存在的端口（模拟没 Redis） | **23 passed, 15 skipped，exit 0** |
| 3 | 同上 + `REQUIRE_REDIS=1` | 13 errors（**仍有 2 skipped**），exit 1 |
| 4 | `pytest tests/`（全量，Redis 可达） | 122 passed，exit 0，40 秒 |
| 5 | `python scripts/check_file_refs.py` | exit 0：98 文件 / 472 处引用 |
| 6 | `python scripts/check_judge_rules.py` | exit 0，2 条提示（numbers / artifact 只改文档）|
| 7 | 四个变异套件实跑：`mutate_check_refs` / `mutate_check_numbers` / `mutate_check_artifact` / `mutate_check_token` | **4/4、17/17、14/14、21/21 全检出，exit 0** |
| 8 | `uvicorn service:app`（:8011，`REDIS_URL=.../13`，`RATE_BURST=3 / RATE_PER_MIN=1 / DAILY_REQ_QUOTA=5 / DAILY_TOKEN_QUOTA=0`）+ `verify_limits_e2e.py` 三轮 | 见下 |
| 9 | `git hook run pre-commit`；`install_hooks.py --check` | 横幅 `[pre-commit]` 打出、数字对账通过；check exit 0（钩子**已装**）|
| 10 | `printf '' \| python scripts/check_push_paths.py` | "涉及 **0** 条路径" + "✔ 没有命中禁推模式"，**exit 0** |
| 11 | 五个反向验证逐一实注入（README 第三层声称的五处全做了） | 五处全部按声称报红，见问题 3 |
| 12 | 我自己设计的"坏掉仍绿"注入 6 处 | 见第 3 节 F6 / F13 等 |

**关于第 8 项三轮的细节**（这是最值钱的一条证据链）：

- 第 1 轮（不给重启命令）：①②④⑤⑥ 全 ✓，③⑦ 打印"跳过"，
  但收尾照样打 `✓ 全过：这套配额在【真服务 + 真 Redis】下确实挡住了，且 Redis 自己重启也不丢账`，exit 0。
- 第 2 轮（`--restart-cmd "true" --restart-redis-cmd "true"`，**什么都没重启**）：
  七条**全部 ✓**（③ 打"重启前 requests_used=3；执行：true / ✓ 重启前后计数一致"；
  ⑦ 打"执行：true / ✓ 计数与令牌桶都还在"），exit 0。
- 第 3 轮（把服务换成"==usage=None 时不留痕==的变异版，Redis 里留着上一轮的
  `metric:usage_missing=6`）：**⑥ 依然 ✓**，而整轮请求一条痕都没留（metric 纹丝不动）；
  随后我删掉这个 metric 再跑**同一服务** ⇒ ⑥ 变红（`✗ …usage_missing 是 0`），exit 1。

### 还原纪律（临时改动都还原了，怎么确认的）

- 动手前把 10 个会被变异脚本触及的文件 + `limits.py`/`service.py`/`tests/*` 的 sha256
  记进 `/d/dsh-work/_cc_targets_hashes.txt` 与 `_cc_baseline_hashes.txt`。
- 我自己的注入一律 `try/finally` 原样写回；变异套件自带 finally 还原。
- 收尾核对：`sha256sum -c` 两份清单**全部一致**（0 行非 OK）；另有一份动手前的逐字节备份
  （`/d/dsh-work/_cc_backups/`），11 个被触及文件逐一 `cmp` **全部一致**；
  `git status --porcelain` 空。
- ⚠️ 中途踩到一个坑并如实写出来：我用过一次 `git checkout -- limits.py` 做还原，
  它写出的是 **CRLF 版**（本机 `core.autocrlf=true`，而工作区原文件是 LF），
  而**此时 `git status` 仍然报"干净"**——也就是说"git status 干净"证明不了字节一致。
  我是靠 sha256 对拍发现的（`17e8ece…` vs `e007970…`），把 CRLF 转回 LF 后再次对拍一致，
  再用 `git add`（内容零变化，index blob 未变）刷新 stat 缓存，现在 status 与字节都干净。
  ⇒ 本报告所有"已还原"以 **sha256 == 动手前快照** 为准，不以 git status 为准。
- 隔离用 `/13` 库在收尾时清空；`/0` 全程 0 key 未动。DB9 是另一份评审在用，
  我早期误把它的 `metric:store_errors` 覆盖成 `'5'`（原值未知），特此披露；
  我在观测期间看到 DB9 从有键变成空库——**不是我清的**，我没有直接证据是谁
  （能造成这种清空的代码路径只有那个 fixture 的 `flushdb`，见 F4）。

---

## 3. 发现

### F1（必修 · 判据在坏情况下不响）`scripts/verify_limits_e2e.py:157-167, 170-191, 270-332`

**③ 和 ⑦ 在"什么都没重启"时全绿**。脚本对 restart 命令的全部信任就是"退出码 0"，
没有任何独立证据证明服务/Redis 真的重启过。

- 复现（实跑）：`--restart-cmd "true" --restart-redis-cmd "true"` ⇒ 七条全 ✓，exit 0（见第 2 节第 8 项第 2 轮）。
- 同一件事的更多入口：restart 脚本写错服务名但仍 exit 0、指向另一个端口的脚本、
  为了"让 CI 稳一点"把命令改成 no-op —— 没有任何东西会拦。
- 一句话修法：让 `/health` 报进程启动时间（或 PID），③/⑦ 在跑完命令后断言"这个标识变了"。

### F2（必修 · 判据在跳过时仍宣称已验）`scripts/verify_limits_e2e.py:418`

**收尾句无条件宣称 ③⑦ 已验**。不给重启命令的那轮，③⑦ 明明打印了"跳过"，
最后一行还是 `✓ 全过：……且 Redis 自己重启也不丢账`（实测输出见第 2 节）。
"本机也能对着自己的服务跑同一份"正是他们设计里鼓励的用法，所以这不是理论场景。

- 一句话修法：收尾句按实际跑过的项拼（"已验证 5/7；③⑦ 未验——没给重启命令"）。

### F3（必修 · 判据在坏情况下仍响不了）`verify_limits_e2e.py:229-233` + `limits.py:296-301`

**⑥（`usage_missing` 留痕）可以靠上一轮的残留变绿**。`usage_missing` 是存在 Redis 里的
全局 metric（`bump` 不设 TTL、跨进程跨轮持久），而 `reset_client_state`（:106-120）
只清 `who` 名下 3 个 key、**不清 metric**。

- 复现（实跑 A/B）：服务改成"`usage=None` 不留痕"（record 的外层 else 改 `pass`），
  库里有上一轮的 `metric:usage_missing=6` ⇒ ⑥ ✓（整轮请求零痕）；删掉该 metric ⇒ ⑥ 红。
- 这会命中"同一个栈上跑第二次"——正是 `reset_client_state` 出场的那个场景。
- 一句话修法：⑥ 先记 metric 值，断言"跑完自己这轮请求后必须变大"（而不是 `>= 1`）。

### F4（必修 · 测试会破坏它本不该碰的状态）`tests/test_limits.py:63-69`

**Redis 半边 fixture 的收尾是 `flushdb()`**，注释却写"把本用例的 key 清掉
（who 带 uuid ⇒ 前缀唯一）"——两回事。实测：往 DB13 放一个"别的客户端"的 canary
（`q:2026-10-13:unrelated-client:req=77`）再跑 `pytest tests/test_limits.py`，
38 passed 之后**整库 dbsize=0**（连同不属于我的键一起抹掉，而且本机 AOF 开着，清空会落盘）。

- 为什么算"判据真实性"问题：默认 URL 是 `.../0`，与本机服务、e2e 脚本默认用的是同一个库。
  在"服务在跑，顺手跑个单测"的姿势下，单测会把服务的当日额度清零——
  把别的验证（手工观察、e2e）正在用的状态打脏。
- 一句话修法：照 `verify_limits_e2e.py` 的 `reset_client_state` 按 who/day 删自己那 3 个键。

### F5（必修 · 声称"在 CI 跑"其实没有）`.github/workflows/ci.yml:90,136,212` + `tests/test_limits.py:59`

**README 第一层证据（`test_limits.py` 两种存储各跑一遍）不在任何闸门里**，
且文件里的 CI 声称是假的。

- 实测：ci.yml 三个 job 只跑 `test_loader` / `test_guard` / `test_eval_set` /
  `test_llm_resilience`；`test_limits.py` 一次都不跑（grep 全文件确认）。
  `REQUIRE_REDIS` 全仓库**只有 `test_limits.py` 自己提到**，没有任何工作流/脚本设它——
  文件第 59 行"（CI 里就是这么用的）"是假话。
- 缺 Redis 时实测：`23 passed, 15 skipped, exit 0`（看起来全绿）；这正好也是
  README 第一层在**任何没有 Redis 的机器**上的真实形态。
- 一句话修法：慢 job 加 `services: redis:` + `REQUIRE_REDIS=1` 跑一次 test_limits.py；
  或把 README 那层降级成"本机跑过一次，无闸门"。

### F6（值得知道）`limits.py:474-477` · `tests/test_limits.py:210` · `tests/test_guard.py:1046` · `e2e:225`

**"Retry-After = 到 UTC 零点的秒数"没有任何判据**。三处断言都是区间 `1..86400`。
实测：`_secs_to_utc_midnight` 改成 `return 3600`（常数）⇒ 单测与 guard 的同名用例
**都仍然 pass**（e2e ⑤ 同款区间，同理）。
一句话修法：用注入时间对拍精确值（`now=1_000_000.0` ⇒ 恰 `36800`），一条等式即可。

### F7（值得知道）`scripts/check_push_paths.py:125-142`

**出口安全检查没有"0 条路径不许报绿"的下限**。实测：`printf '' | python scripts/check_push_paths.py`
⇒ "涉及 **0** 条路径" + `✔ 没有命中禁推模式`，exit 0。同族其它检查器**都有**下限断言
（check_file_refs `n_refs==0` 报红、check_judge_rules `scanned==0` 报红），
唯独这条风险最高（真实事故 7252a69）的没有；而它自己的注释里恰好记着历史 bug
"传了个空集合，检查形同虚设"。
一句话修法：stdin 协议下算不出任何路径（且不是删分支）⇒ 报红。

### F8（值得知道）`scripts/check_judge_rules.py:216`

**② 号规则"变异要改产物侧"仍在认名字**。实测：把 `mutate_check_token.py` 里的
`DATA_MUTATIONS` 改名（结构一字不动）⇒ 立刻输出
"`mutate_check_token.py：所有变异都只改【文档】，没有一条改产物`"这句**假提示**。
这与它自己 docstring 里"判结构不判名字"的规矩相抵（② 只提示、不拦，所以是噪音级）。
一句话修法：数据侧按结构判（如"变异目标里出现非 .md 文件的条目数 ≥ 1"）。

### F9（值得知道）`scripts/mutate_check_token.py:117-124`

**"整张三遍表被删"这条变异名不副实**：它的 old 只换掉表里**第 1 行**，
与下一条"少写一遍"是**同一形状**——实跑两条的诊断一字不差
（"报告三遍表 2 行，JSON 里 3 遍"）。按本项目自己 CLAUDE.md 的规矩（同源变异不携带信息量），
这两条等于一条。我另外手动把三行**全删**跑了一次：检查器 exit 1 **会红**——
所以覆盖面不缺，缺的是这条变异声称的那个方向。
一句话修法：换成别的数据形状（整块表连表头删掉、或行数/数据反向不匹配）。

### F10（值得知道）`verify_limits_e2e.py:215` + `limits.py:296-297`

**⑤ 把刚验过"必须有 TTL"的 key 写成了永久 key**。`r.set()` 不带 expire 会清掉原 TTL。
实测跑完后：`q:2026-10-13:ip:127.0.0.1:req = 999, ttl = -1`。
`metric:*` 同样无 TTL（可能是设计如此，但没人写为什么）。
一句话修法：⑤ 的 `r.set` 补一个 expire（或收尾删除）。

### F11（值得知道）`scripts/check_judge_rules.py:61-62`

**"判据纪律"三条只覆盖文件名匹配 `scripts/check_*.py` / `mutate_check_*.py` 的脚本**——
`verify_limits_e2e.py` 这类同样在承担判据职责的文件在管辖之外。讽刺点：第 ③ 条规矩正是
"用例缺失必须算失败"，而 e2e ③⑦ 跳过却报"全过"，没人管（它是另一份文件）。
一句话修法：把 e2e 显式纳入（或至少让它的"跳过"在输出里计数并进入收尾句，见 F2）。

### F12（值得知道）`tests/test_limits.py:363,385`

**`REQUIRE_REDIS=1` 也漏两个**：这两条用 `skipif` 而非 fixture，缺 Redis 时照跳。
实测：`REQUIRE_REDIS=1` + 无 Redis ⇒ 13 errors **但仍有 2 skipped**。
一句话修法：这两条也走 fixture，或在 skipif 条件里同样尊重 `REQUIRE_REDIS`。

### F13（存疑）`tests/test_limits.py:386-389` vs `limits.py:284-290`

**`test_redis_day_ttl_covers_the_boundary` 断言的是类的默认常量，不是实际施加的 TTL**。
实测：把 `incr_daily` 的 `c.expire(key, self.day_ttl)` 改成 `c.expire(key, 3600)`
（行为真的变了：日 key 只活 1 小时）⇒ 该用例与 `test_redis_keys_have_ttl_and_namespace`
**都仍然 pass**。影响有限（目前没有消费者读"昨天的 key"），但名字声称的边界语义无判据。
一句话修法：断言实际 key 的 ttl 与 `day_ttl` 一致。

### F14（存疑）`scripts/check_judge_rules.py:211-216`

**② 对 `mutate_check_artifact` 的提示可能本身是假提示**：该检查器的"产物"就是
`evalset/*.md` 报告本身，而规则按扩展名（json/py/jsonl）判"有没有改产物"，
于是把"改的就是产物"的套件报成"没有一条改产物"。提示级，但正是会被当噪音关掉的那种。
一句话修法：按"变异打到的文件是不是该对账器登记的检查对象"判。

### F15（存疑）`verify_limits_e2e.py:197` vs `160`

**④ 的计数断言是 `>= 1`**（③ 才要求 `>= burst`）。不带 `--restart-cmd` 时，
"每次请求都记账"没有任何断言在查——一个只记第一笔的坏实现能过整轮。
一句话修法：④ 改成 `>= burst`（② 刚打过 burst 次）。

### F16（存疑）`scripts/mutate_check_numbers.py:286` 等

**变异脚本的还原写回是 `write_text(newline="")`（LF）**：在 `autocrlf=true` 的 clone 里，
被打靶的文件若是 `w/crlf`，还原后会被静默转成 LF（字节变了、git status 仍干净）。
本仓库**有约 100 个文件是 `w/crlf`**（含 `mutate_check_numbers.py` / `mutate_check_artifact.py`
自己）——但变异套件实际写回的那几个目标（README.md、experiments 实验记录、
三份 evalset 报告、check_artifact_sanity.py）**目前都是 `w/lf`**，所以今天不构成损坏；
只是"还原 ≠ 逐字节"的另一半风险（他们注释里只处理了 LF→CRLF 这一半）。
一句话修法：还原用 `read_bytes/write_bytes` 原样写回。

---

## 4. 我怀疑过但复核后判定为误报的

1. **"本地钩子没装"——错的**。`.git/hooks/` 里只有 `*.sample` 让我一度要写成发现，
   但 `core.hooksPath=scripts/hooks` 已设：`install_hooks.py --check` exit 0，
   `git hook run pre-commit` 实跑打出横幅并"数字对账通过"。空 `.git/hooks` 是 hooksPath
   设计的正常形态。（这正好是 install_hooks 防的那类误判，我差点踩进去。）
2. **"去掉时钟回拨保护可能不响"**：我推演过"完全不设防"的变体似乎也能通过那条断言。
   复核后排除：不设防时 tokens 变负、"负债 + 后续 delta"在数学上与正确状态等价（不会多补发）；
   真正会多补发的只有"delta 归零 **且** ts 写回过去"（第一版 bug 的形状），
   而用例断言的正是它。实跑 Lua / 内存两处注入都红。
3. **"拿掉内存锁后 `test_burst_then_denied` 仍绿"**——不是洞：那条用例本来不测并发
   （相邻两条才测）；它绿是设计内的控制组。
4. **"整张三遍表删光检查器不响"**——实跑：会红（exit 1）。F9 只剩"变异名不实"。
5. **CI 里的 `|| true`**（收尾 `down -v`、失败时倒日志）：是清理与诊断步骤，不是断言，
   不算"看着在跑其实跑不到"。
6. **`check_file_refs` 的 `refcheck-ignore`**：跳过多少行会打印出来
   （实测"13 行按 refcheck-ignore 跳过"），不是静默白名单。设计如此。

---

## 5. 我查不了的

- **没有 Docker/WSL**：compose 起栈那一步、容器 job、Dockerfile、⑦ 在 compose 配置
  （`--appendonly yes --save ""`）下的真实行为都没实跑；我对这些路径的结论全部来自静态阅读。
- **CI run #46 的日志我没读**（这台机器上拿不到），只核了 ci.yml 本身 + 本机等价实跑。
- **`mutate_check_token` 依赖 `agent/_token_cost.json`（gitignored）**：我只能证明
  "这一份数据下 21 条全检出"；换机器/换批数据后的行为没有证据。
- 提交历史里"钩子检查通过了 / CI 绿了"之类的**自述我没有核实**。
- 我对 `service.py` 只读了 `/health`、`/limits`、`/chat` 与 `handle_chat` 头尾；
  其余约 1050 行没读，若有别的入口与限流交互，我可能漏。
- ⑦ 在**真的重启 Redis** 下的行为我没有实跑（任务要求不动那台 Redis）。

---

## 6. 一句话总评

这套验证**支撑得住**的最强结论是："限流/日配额在真服务 + 真 Redis 下确实接上了；
五个实现级反向验证（锁 / EXPIRE / 回拨 / 闸门 / 记账）全部成立且我逐一实跑过"。
**支撑不住**的是"每条判据都在盯它名字声称的事"——我实跑出 8 处"被检行为坏掉仍绿 /
仍在宣称"（F1–F7、F13），其中 F1（重启判据在什么都没重启时全绿）与 F3（⑥ 靠上一轮的账变绿）
最值得先修。
**不该说的话**：① "CI 里两种存储各跑一遍 / REQUIRE_REDIS=1（CI 里就是这么用的）"——没这回事；
② 不给重启命令时那句"✓ 全过……Redis 自己重启也不丢账"；
③ 把 ⑦ 概括成"AOF 生效"——它实际验的是"重启命令真的执行了 + 重启后持久化状态还在"，
而前一半没有任何判据。

