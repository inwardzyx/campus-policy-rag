> ★ 入库说明：这是 **cc（独立上下文）的盲审报告**，原文照收，只加这一行来源说明。
> 审的人是另一份上下文里的 agent，**它没看到我的任何结论**；卡里明确要求
> "不许把注释/docstring/README 里的自我评价当证据"。逐条复核后的处置见
> README〈盲审发现的缺口：哪些修了、哪些只记下来〉。
> 原始任务卡已一并入库：`cc-task-redteam-limits.md` / `cc-task-verify-truth.md`（它就是当时给它看的东西，含全部约束）。
# 红队盲审：`_review_rag-cs` 的限流 / 日配额（`700bc0e..a380b9d`）

署名：cc（外部审稿人）· 2026-10-13 · 只读仓库；唯一写过的地方是 `D:\dsh-work\`
评审对象与卡见任务书。**我没有看过被审代码作者的对话，也不把注释/docstring/README 当证据** ——
下面每条都落到"文件:行 + 我怎么知道的（实跑命令/输出 或 静态读数）"。

---

## 1. 我要回答的问题（复述）

1. 不改代码，有哪些路径能**绕过或放大**配额：身份伪造（`X-Client-Id` / `X-Forwarded-For` / IPv6 / NAT）、并发竞态、时钟、`fail_mode`、桶边界、先花后记。
2. 哪些闸门是**装饰**（看着有、拦不住/几乎不触发）。
3. 这层会不会**自己变成故障源**（Redis 半死/重启/慢、TTL、key 堆积、`/health` 被拖累）。
4. `/limits` 与 `/chat` 认的"同一个客户端"**会不会不一致**（错账）。
5. **多进程/多副本**下哪些地方失效或放大。
6. 还有哪些**我认为危险但没被问到**的点。
输出要求：发现表（严重度/位置/现象/为什么/复现/修法）+ 误报清单 + 查不了的 + 总评。

---

## 2. 我实际读了什么 / 跑了什么

**读（全量）**：`limits.py`（488 行，全部）、`scripts/verify_limits_e2e.py`（424 行，全部）、
`docker-compose.yml`、`.github/ci-compose.override.yml`、`Dockerfile`（关键段）、
`tests/test_limits.py`（前 120 行 + 用例名清单）、`tests/test_guard.py` 的 ⑧ 组（949–1100）、
`service.py` 的 196–360 / 458–530 / 672–800 / 795–1073 / 1150–1175、`README.md` 1685–1765、`.env.example` 的相关段。
**读（定位性 grep）**：`ci.yml`（pytest/compose 步骤）、全仓 `handle_chat|import service|FastAPI|uvicorn`（找旁路入口）、`requirements.txt` / `pytest.ini`。
**没读**：`kb/loader.py`、`evalset/`、`agent/`、`mcp/` 的业务逻辑（只查了它们的入口类型）、README 其余 ~140KB、`_reviews/`、`_tasks/`。

**跑（只读 / 只对 Redis db9，避开另一份评审用的 db0；探针脚本在 `D:\dsh-work\`）**：

| # | 命令 / 方式 | 关键输出 |
|---|---|---|
| 1 | `probe_limits_cc.py`（假 store 验指标合并） | 真值 4 → `/health` 口径报 **8**；真值 1 → 报 2；远端写失败时反而正确（3→3） |
| 2 | 同上（并发打日额度） | 日额度 5、并发 20 → **20 条全放行**，计数器最终 20（并发 40 → 40） |
| 3 | 同上（真 Redis db9，`day_ttl=8`） | 同刻连打 12 次 = 10 放行 + 2 拒（wait 3.0s）/ T+1s 拒(wait 2.001) / **回拨 600s 再回 T+4 → 仍拒(wait 2.001)，ts 未被写回过去** / 跳 100000s → 只补到 9（上限 10）/ `bucket` ttl=60、`q:...:req` ttl=8 |
| 4 | 同上（日额度路径） | 第 3 次 `reason=daily_req`（**且已吃掉一枚桶令牌**，第 4 次变 `reason=rate`）；`Retry-After=69874` = 到 UTC 零点 |
| 5 | `probe3_blackhole.py`（本机开一个只 accept 不回包的 socket 当"半死 Redis"） | `check()` **2.25s** 才 503；`stats()`（= `/health` 的 limits 字段）**9.14s** |
| 6 | `probe4.py`（配置解析 + 身份长度） | `TRUST_PROXY_HEADERS` 取 `False/FALSE/no/off/NO` ⇒ **全部解析成"开"**；XFF 长 4000 → `who` 长 4000（`X-Client-Id` 有 `[:64]`，XFF 没有） |
| 7 | `probe2.py`（0 的语义） | `RATE_PER_MIN=0` ⇒ 桶打空后**永久拒绝**（retry 恒 60）；`RATE_BURST=0` ⇒ 永远拒绝；而 `DAILY_*=0` ⇒ 不限 |
| 8 | `TEST_REDIS_URL=redis://127.0.0.1:6379/9 REQUIRE_REDIS=1 python -m pytest tests/test_limits.py -q -p no:cacheprovider` | **38 passed in 0.56s**（内存+Redis 两半都真跑了） |

**没跑**：`docker compose`（本机无 Docker/WSL）、真实 HTTP 服务（没起 uvicorn）、重启 Redis（卡里禁止）。

---

## 3. 发现

问题→发现对照：Q1 → R1/R3/R7/R8/R9/R11；Q2 → R1/R3/R11；Q3 → R2/R5/R11/R12；Q4 → R1(c)+R14；Q5 → R1/R6/R15；Q6 → R2/R10/R13。

---

### R1 —— 身份可以无限分裂，而**整套配额没有任何全局上限** 【必修】

**位置**：`limits.py:318-333`（`client_id`）、`limits.py:364-408`（`check`）、`service.py:952-956`；`docker-compose.yml:63-74`

**现象**：配额键全部长成 `bucket:{who}` / `q:{day}:{who}:req|tok`（`limits.py:371/391/400/420/428`），
`who` 完全由"这次请求看起来从哪来"决定，而**没有任何一个键是不带 who 的**（我 grep 过 `limits.py` 全部
`f"..."` 构造的 key，只有这三类 + `metric:*`）。于是：

- (a) **换 IP = 换一个全新钱包。** 实测 `who` 就是原始对端串（`probe4`：`client_id("10.0.0.1", {...})` → `"ip:10.0.0.1"`），
  没有网段归一化。IPv6 下一个 /64 有 2^64 个地址；IPv4 用代理池同样成立。
  注：compose 默认 `HOST=0.0.0.0`（IPv4-only），所以 IPv6 那条要 `HOST=::` 或前面有反代才打开 —— 但 README 明确说公网部署**必须**加反代。
- (b) **开了 `TRUST_PROXY_HEADERS=1` 时更省事**：`limits.py:324-328` 取 XFF 的**第一跳**，注释写的理由是
  "约定：反代把自己追加在后面"。但 nginx 里最常见的那一行 `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;`
  **就是追加** —— 追加意味着第一跳正是调用方自己塞的值。⇒ 每个请求换一个 `X-Forwarded-For` 就换一个桶。
  这不是理论：README:1759-1760 说"要放公网前面还得有反代"，而反代配好之后**必须**开这个开关才按真实客户端分身份。
- (c) `ALLOW_CLIENT_ID_HEADER=1` 时同理（`limits.py:329-332`），而且因为 `/limits` 用同一个身份函数，
  还能**读别人的用量**（见 R14）。

**为什么是问题**：这一层的立项理由（`limits.py:4-7`、README:1703-1708）是"防止公网上被人刷爆 API 额度"——
**钱包是全站一个，配额却是每身份一份**。刷子换身份刷，所有闸门都是绿的，而账单一分不少。
`DAILY_REQ_QUOTA=200` 在这条路径下等价于"每个被我换出来的身份各 200 次"。

**怎么复现（不依赖 Docker）**：
- 身份分裂：`probe4.py` 已实测 `who` 随头/对端变化；对真服务就是 `curl -H 'X-Forwarded-For: <随机>'`（trust_proxy=1 时）。
- 无全局账：`grep -n 'q:\|bucket:' limits.py` 只有含 `{who}` 的键；`redis-cli --scan --pattern 'q:*'` 会随身份数线性增长。
- 分工放大（Q5 相关）：这不是多副本问题，**单副本同样成立**。

**一句话修法**：① 加一个不带 who 的全局日额度键（`q:{day}:__all__:req|tok`，单独一组 env，`check` 里在身份判定之后无条件再查一次）；
② 若开 `TRUST_PROXY_HEADERS`，XFF 取**最右一跳**（= 你自家反代追加的那个），并把 nginx 示例写成 `$remote_addr`；
③ 身份先归一化（IPv6 取 /64）再哈希成定长。

---

### R2 —— 这层自己会成为攻击面：身份基数 ⇒ Redis 键无上限增长 ⇒ 没有 `maxmemory` ⇒ 把宿主/容器打爆 【必修】

**位置**：`docker-compose.yml:29`（`command: ["redis-server","--appendonly","yes","--save",""]`，**没有 maxmemory / maxmemory-policy**）、
`limits.py:229-233`（桶 TTL = `ceil(burst/rate)*2`，实测 60s）、`limits.py:243-247`（`day_ttl=172800`，2 天）、
`limits.py:284-290`（每次写都 `EXPIRE`）

**现象**：每个**新身份**一次请求 = 3 个新 key（bucket + req + tok），其中日键活 **2 天**。
攻击者用 R1 的手段换身份刷，Redis 内存随身份数线性涨，且没有任何上限：
`docker-compose.yml` 里 Redis 是默认配置（`maxmemory 0` = 不设限，`noeviction`），
容器也没给 `mem_limit`。

**为什么是问题**：被打爆的不是这个服务，是**这台机器**。Redis 一旦被 OOM kill / 内存打满：
`fail_mode=closed`（默认）⇒ **所有 /chat 一律 503**，即"攻击者用一个循环把整个服务按停"。
附带：AOF 会把这些垃圾一起落盘（`--appendonly yes`）。
内存版（没配 `REDIS_URL`，即裸 `python service.py`）**更糟**：`InProcessStore._buckets` / `._daily`
（`limits.py:140-142, 165-172`）**完全没有 TTL、没有清理**，字典只增不减；而这恰好也是本项目 README 说的"本机单进程跑可以留空"的默认路径。

**加剧项（同一条链上）**：`who` 里 XFF 部分**没有长度上限**（实测：4000 字符的 XFF → `who` 4000 → key 约 4KB，
两个键就是 8KB；而同一个函数里 `X-Client-Id` 老老实实截到 64）。**规模估算**：正常身份 ~100B×3；
用 4KB 的 XFF ⇒ 单请求可在 Redis 里落下 ~8KB 且活 2 天 ⇒ 放大 80 倍以上。
（`uvicorn/h11` 单请求头默认允许到几 KB 级 —— **这条我没实测**，本机没起服务，标为存疑部分。）

**怎么复现**：静态——`docker-compose.yml` 通读（确实没有 maxmemory）；动态——`probe` 里 `r.keys('*probeD*')`
确认三键形状与 TTL；增长的实证需要长时间跑，没做。
**一句话修法**：compose 的 Redis 加 `--maxmemory 256mb --maxmemory-policy allkeys-lru`；
身份哈希成定长（如 sha1 前 16 位）；XFF 也 `[:64]`；内存版给 `_daily` 加按天清理。

---

### R3 —— 两个"身份可信度"开关的布尔解析会**反向**：`False` / `no` / `off` 都被当成"开" 【必修（一行）】

**位置**：`limits.py:84-85`
```python
trust_proxy=(env.get("TRUST_PROXY_HEADERS") or "0").strip() not in ("", "0", "false"),
allow_client_id=(env.get("ALLOW_CLIENT_ID_HEADER") or "0").strip() not in ("", "0", "false"),
```

**现象（实测，`probe4.py`）**：

| 写入值 | 解析结果 |
|---|---|
| `0` / 空 / `false`（小写） | 关 ✅ |
| `False`、`FALSE`、`no`、`NO`、`off` | **开** ❌ |

**为什么是问题**：这两个开关决定"调用方的身份能不能伪造"。运维写 `TRUST_PROXY_HEADERS=False`
（看上去是"不要信"）实际打开了 R1(b) 那条伪造路径，而且**没有任何地方能看见**——
`Limiter.stats()`（`limits.py:449-455`）只报 `store / store_ok / fail_mode` 和四个指标，
**不报 trust_proxy / allow_client_id**，`/health` 里也就看不到。这属于"配错了也不报错"的那一类，
恰恰是本文件自己在 docstring 里点名要防的（`limits.py:26`）。
（同类不对称：`LIMIT_FAIL_MODE` 走了 `.lower()`，所以它没这个问题 —— 说明作者知道要做归一化，只是这两个忘了。）

**怎么复现**：`python -c "import limits;print(limits.Limits.from_env({'TRUST_PROXY_HEADERS':'False'}).trust_proxy)"` → `True`。
**一句话修法**：`.strip().lower() in {"1","true","yes","on"}`；顺手把这两个开关加进 `stats()`。

---

### R4 —— 管道抛异常时这次请求**不进日额度**（`record` 不在 `finally` 里） 【值得知道】

**位置**：`service.py:963-966`
```python
resp = handle_chat(req)
usage = resp.get("usage") if isinstance(resp, dict) else getattr(resp, "usage", None)
LIMITER.record(who, usage)
```

**现象**：`handle_chat` 只接 `LLMCallError`（`service.py:992/1005/1059`），任何别的异常会一路冒到 FastAPI → 500，
而 `LIMITER.record` 一次都不执行 ⇒ 这次请求**只在桶里扣了一枚令牌，日请求额度上完全不存在**。

**一条已存在、可达的具体路径**：没配 `DEEPSEEK_API_KEY` 时，`RAG.llm` 在构造期抛 pydantic `ValidationError`
（`service.py:486-497` 明说这是"刻意的 500"，`service.py:324-331` 再确认一次），
所以该状态下**每一次默认路径 /chat 都是 500 且都不记账**：`/limits` 的 `requests_used` 永远是 0，
日额度那道闸全废，只剩桶（默认 20/分钟 ⇒ 28,800 次/天）。
（本机没起服务，**这条是代码路径推理，未实测**；但 CI 的 docker job 明确不配 key，那个 job 里的 /chat 就是这个状态。）

**为什么是问题**：README:1713 把"拒答也计数"作为设计要点写出来了，方向是对的 —— 但"异常也计数"漏了，
而被漏掉的那条分支正好是"没有任何成功路径约束"的那条。配额的记账应该是**无条件**的。

**一句话修法**：
```python
usage = None
try:
    resp = handle_chat(req)
    usage = resp.get("usage") if isinstance(resp, dict) else getattr(resp, "usage", None)
    return resp
finally:
    LIMITER.record(who, usage)
```

---

### R5 —— `/health` 的延迟挂在 Redis 上：半死时实测 9.1s，而 compose 健康检查给它的预算是 3s 【值得知道】

**位置**：`limits.py:449-455`（`stats()`）+ `limits.py:457-465`（`_merge_metric`，4 个指标各一次读）+ `limits.py:303-307`（`ping`）、
`service.py:892-901`（`/health` 里调 `LIMITER.stats()`）、`docker-compose.yml:85-90`（健康检查 `urlopen(..., timeout=3)`）

**现象（实测，`probe3_blackhole.py`：本机起一个只 accept、不回包的 socket 冒充"TCP 通但不响应"的 Redis）**：
- `limiter.check()`（= `/chat` 的闸门）**2.25s** 才返回 503；
- `limiter.stats()`（= `/health` 的 `limits` 字段）**9.14s**（5 次串行命令，每次都要等满 1s socket 超时 + 重连）。

**为什么是问题**：① 编排/监控会**在 Redis 生病时把服务本身判成不健康**（3s 超时必炸），
而"限流存储不可用"和"服务进程死了"是两件该分开报警的事；
② `/health` 是同步 `def` ⇒ 占线程池线程最多 ~9s，而它**不设闸**（`service.py:941` 的设计意图是好的），
任何未认证的调用方刷 `/health`（每次 5 条 Redis 命令）就能一边占满 40 个线程、一边给 Redis 加压；
③ 同族的 `RedisStore` 对"内存版"的 `ping()` 恒 True（`limits.py:183-184`），所以这套探针在内存版下**永远绿**，
它只对 Redis 有感 —— 这不是 bug，但意味着"store_ok=true"不能当作"限流在工作"的证据。

**一句话修法**：`stats()` 里把指标读成"一次 pipeline + 短超时（如 0.2s）"，失败就整体报 `store_ok=false`；
健康检查别把依赖的可用性算进自身活性（或把 `/health` 拆成 `/healthz`（进程）与 `/readyz`（依赖））。

---

### R6 —— 指标合并**重复计数**：`/health` 报的 `denied_rate`/`usage_missing` 是真实值的 ~2 倍 【值得知道】

**位置**：`limits.py:355-361`（`_bump_local` 先加本地、**再**写远端，两边都成功时各 +1）、`limits.py:457-465`（`_merge_metric = remote + local`）

**现象（实测，`probe_limits_cc.py` A 段）**：真值 4 → `_merge_metric` 报 **8**；真值 1 → 报 **2**。
反过来，远端写失败时是**对的**（3 → 3）—— 也就是说它只在"一切正常"的时候错。

**为什么是问题**：这是这一层唯一的可观测窗口（README:1728 自己也说"必须有个地方能看见"），
而 docstring（`limits.py:457-462`）写的合并理由是"故障期间的计数会恰好消失在它该出现的地方"——
意图对，实现把"故障期本地兜底"和"正常期远端账"混在了一个计数器里。
多副本下每个副本再各叠一次本地计数 ⇒ 告警阈值、面板一律偏大（Q5 相关）。
**一句话修法**：本地只在 `except` 分支里加（成功路径不加），或 `_merge_metric` 取 `max(remote, local)` 并注释清楚语义。

---

### R7 —— 同一个配置块里 `0` 的语义是**相反**的 【值得知道】

**位置**：`limits.py:64-66`（`daily_req=0` / `daily_tokens=0` = 不限，见 `check` 里的 `if lim.daily_req > 0`）、
`limits.py:144-163` 与 `limits.py:227`（`rate=0` ⇒ `wait` 恒 60s、桶**永不补充**）

**现象（实测，`probe2.py`）**：`RATE_BURST=3, RATE_PER_MIN=0` ⇒ 前 3 次放行，之后**永远拒绝**（retry 恒 60）；
`RATE_BURST=0` ⇒ 永远拒绝。对照：`DAILY_REQ_QUOTA=0` / `DAILY_TOKEN_QUOTA=0` ⇒ 关闭该闸门（CI 里就这么用的：`ci.yml:276`）。

**为什么是问题**：想"关掉速率限制"的人写 `RATE_PER_MIN=0`，得到的是**全站拒绝服务**，
而且现象是 429（"你太快了"），诊断会朝"限流器坏了"的方向跑偏。
**一句话修法**：`per_min <= 0` 也定义成"不限"（并在启动日志里明说"速率闸已关闭"），或直接让它在启动时**拒绝启动**。

---

### R8 —— 日请求额度有并发窗口（上限 = burst），且**被日额度拒绝的请求仍在吃桶令牌** 【值得知道】

**位置**：`limits.py:390-397`（`used = get_daily(...)` 判完才 `record` 加一，见 `limits.py:420`）、`service.py:956-965`（读与写之间隔着整段 `handle_chat`）

**现象（实测）**：① `probe_limits_cc.py` B 段：日额度 5、并发 20 → **20 条全部放行**，计数器最终 20；
真实链路里桶是原子的（`probe` C 段：同刻 12 次严格 10 放行 + 2 拒），所以单波次超发 ≤ `burst-1`（默认 ≤9），
但**窗口长度 = 一次 `handle_chat` 的耗时**（默认路径要调 3 次模型，秒级）。
② `probe` D 段：`burst=3, daily_req=2` 时第 3 次被 `daily_req` 拒，而**那枚桶令牌已经被扣掉了** ⇒ 第 4 次报的是 `reason=rate`。
`verify_limits_e2e.py:207-213` 的注释承认了这个副作用的诊断后果（"诊断指偏方向"），但**只修了脚本，没修语义**。

**为什么是问题**：日额度不是硬闸（README:1714 只承认 token 额度是"先花后记"，其实**请求数也是**）；
被日额度挡住的人同时被速率惩罚，`/limits` 使用者会看到与真实原因不符的 reason。
**一句话修法**：请求数改成"先 `INCR` 再判返回值"（一次往返就原子，`INCR` 返回 > 限额就拒并把这次 `DECR` 回去），
顺带把窗口消掉；拒绝顺序上先判日额度再扣桶（或两码事各自独立）。

---

### R9 —— 桶里"差 1e-14 个令牌"会被浮点字符串回写成整数 1 【存疑（影响可忽略）】

**位置**：`limits.py:229`（`redis.call('HSET', KEYS[1], 'tokens', tostring(tokens), ...)`）+ `limits.py:223`（`if tokens >= 1`）

**现象（实测，`probe` C 段）**：denial 路径把 `tokens=0.99999999999999` 写回，Lua 的 `tostring` 用 `%.14g`
把它写成 `"1"`，于是**下一个请求（同一时刻）直接放行**，`wait` 从 0.001s 变成 0。
没有累积效应（每枚令牌最多"提前" 0.001s 被拿走），现实影响 ≈ 0，所以只标"存疑"。
**一句话修法**：判定写成 `tokens >= 1 - 1e-9`，或存 `math.floor(tokens*1e6)/1e6`。

---

### R10 —— 只有"速率"闸，没有"并发/在途成本"闸 【值得知道】

**位置**：`service.py:930`（`/chat` 是同步 `def` ⇒ 走 anyio 线程池，默认 40 线程）、`service.py:224`（`_LLM_POOL = 4`）、`service.py:346-359`（单次调用最长等 `LLM_TIMEOUT_SEC=30s`，一个请求最多 3 次 ⇒ 最长可占线程 ~90s）

**现象**：一个请求可以占住一个线程最长 ~90s；只要有 **4 个身份**（`burst=10` 各给 10 并发）就能占满 40 个线程，
之后正常用户排队/超时。配额是"按身份限速"，**没有任何东西限制在途请求数**（README:1761 承认"没做反压排队"）。
**为什么是问题**：这是 R1（身份可分裂）的廉价放大器：不需要烧钱，只要占住线程池就能让服务不可用。
**一句话修法**：给 `/chat` 加一个全局在途信号量（超限直接 503/429），或在反代层限并发。

---

### R11 —— `fail_mode=open` 期间的花费在账本上**完全消失**（没有本地缓存放回） 【值得知道】

**位置**：`limits.py:467-471` + `limits.py:379-382`（open ⇒ 直接放行）、`limits.py:410-432`（`record` 里 `_call` 失败只落本地指标，见 `limits.py:346-353`）

**现象**：Redis 不可用 + `open` ⇒ 每个请求都放行，而 `record` 同样写不进 Redis（也不落盘、不缓存），
只增加本进程的 `_local_metrics`（重启即失）。恢复之后日计数仍是 0 ⇒ **整个故障期的花费在账本上不存在**。
**完整利用链（R2 → R11）**：先用 R2 把 Redis 打挂/打 OOM ⇒ 若运维为了可用性选了 `open`（README:1715 明确把它当可选档），
⇒ 故障期内无限刷、且事后无痕。`closed` 挡得住这条（代价是整站 503）—— 也就是说**两种档位在这一刻都不好**，
真正的缺口是"open 时没有本地兜底账"。
**一句话修法**：open 期间把 `(who, day, n)` 落到本地有界队列/文件，恢复后回放；至少按小时把"未记账次数"打进指标并告警。

---

### R12 —— `tests/test_limits.py` 的收尾会对整库 `flushdb`（= 一键重置所有人的日额度） 【值得知道】

**位置**：`tests/test_limits.py:63-69`
```python
s = L.RedisStore(REDIS_URL)      # REDIS_URL 默认 redis://127.0.0.1:6379/0
yield s
try: s._c().flushdb()            # ← 收尾清库：删的是【整个 db】，不是本用例的 key
```
**现象**：跑一次单测会把 `TEST_REDIS_URL` 指向的那个 db **整个清空**。
对照同仓的 `scripts/verify_limits_e2e.py:106-121`：那边专门写了"只删自己这个 who 的三个键，不动别人的额度，也不 flushdb"。
**为什么是问题**：本机 `.env` / compose 用的就是 `db0`；只要有人把 `TEST_REDIS_URL` 指到正在服务的那台 Redis
（默认值恰好就是 `127.0.0.1:6379/0`，与 README 给本机起的 Memurai 地址**一模一样**），
"跑个单测"就等于**把当天所有客户端的额度清零**——一个安静的全站配额重置按钮。
（我这次特意用了 db9 才敢跑。）
**一句话修法**：收尾改成 `delete(*本用例用到的 key)`（用例里 `who` 是 uuid，天生可枚举），或至少把默认 `TEST_REDIS_URL` 指到 `db 15`。

---

### R13 —— 38 个限流判定用例**不在 CI 里**（README 却把它列为"不是摆设"的第 1 条证据） 【值得知道】

**位置**：`.github/workflows/ci.yml:90 / 136 / 212`（三处 pytest，分别跑 `test_loader.py`、`test_guard.py test_eval_set.py test_llm_resilience.py`、容器内 `test_guard.py`）；
`README.md:1732-1734` 把 `tests/test_limits.py` 作为第一条证据。
**现象**：`grep -n test_limits .github/workflows/ci.yml` → **零命中**（只有一句提到 e2e 脚本的注释）。
`pytest.ini` 的 `testpaths = tests` 只在裸跑 `pytest`（无参数）时生效，而 CI 没有这种调用。
⇒ **"CI 全绿"这句话不覆盖限流判定逻辑本身**：令牌桶补充/回拨/并发、TTL、故障模式、身份识别都没在 CI 里跑过。
**为什么是问题**：这正是"判据存在但没接线"的形态；而且即使接上，若不给 `REQUIRE_REDIS=1`，
Redis 那半边会**静默 skip**（`tests/test_limits.py:55-61`）—— 看起来更绿了。
**怎么复现**：`TEST_REDIS_URL=redis://127.0.0.1:6379/9 REQUIRE_REDIS=1 pytest tests/test_limits.py -q` → 本机 **38 passed**（今天真跑到），说明它本身是好的，只是没接进 CI。
**一句话修法**：慢 job 里加一行（附一个 redis service + `REQUIRE_REDIS=1`）。

---

### R14 —— Q4 的直接回答：`/limits` 与 `/chat` 的身份**是同一个人**，但那个"人"可以是别人 【值得知道】

**位置**：`service.py:904-917`（`/limits`）与 `service.py:952-955`（`/chat`）

**结论**：两处**逐字相同** —— 同一个 `limits_mod.client_id(...)`、同一份 `request.client.host`、
同一份小写化后的 headers、同一个 `LIMITER.limits`，所以**不会**出现"记在 A、按 B 判"的实现级错账。
我原本怀疑的几条（端口造成桶分裂、`/limits` 与 `/chat` 归一化不同）**经复核都不成立**（见第 4 节）。
**真正的错账来自"身份本身可自称"**：
- 开 `ALLOW_CLIENT_ID_HEADER=1`（文档定位是"内部多租户"）时，**任何调用方都能把请求记到别人账上**：
  `curl -H 'X-Client-Id: <受害者>' .../chat` ⇒ 消耗受害者的日额度；
  `curl -H 'X-Client-Id: <受害者>' .../limits` ⇒ **读**受害者的用量。
  代码注释（`limits.py:32-33`）写的是"它不是鉴权"，但**"记到别人头上"这个后果没有写出来**。
- 开 `TRUST_PROXY_HEADERS=1` 且反代追加 XFF 时同理（R1b）。
**一句话修法**：这两个开关的文档里补一句"它会让他人替你（或你替他人）花配额"；内部多租户场景应在反代/网关做鉴权后再注入身份，而不是读客户端自带的头。

---

### R15 —— Q5 的直接回答：多进程/多副本下哪里失效 【值得知道】

| 位置 | 机制 | 后果 |
|---|---|---|
| 内存版存储（`limits.py:24-26` 自己承认） | 每进程各算各的桶与日计数 | 配额 × 副本数（README:1726 已写）—— 注意 `Dockerfile:77` 的 `CMD ["python","service.py"]` 是**单进程**，所以这要有人手动 `--workers` 才会遇到 |
| `_local_metrics`（`limits.py:343, 355-361`） | 本地计数只活在自己的进程里 | 每个副本的 `/health` 数字各不相同且都偏大（R6），做面板/告警会错 |
| 时钟（R1 之外的时钟项，见第 4 节） | 桶的时间基准是"最后一次写它的那个进程的钟"；回拨保护会让 **领先**的时钟把桶冻住（更严），落后时钟写回被拒 | **实测回拨保护方向是对的**（`probe` C 段），所以副本间秒级时钟偏差只会让限流**更严**，不会被用来刷额度 —— 这条是澄清，不是问题 |
| 身份（R1） | 每副本共享 Redis ⇒ 桶是全局的；但"身份"本身可分裂 | 与副本数无关，单副本一样漏 |

---

## 4. 我怀疑过、但复核后判为**误报**的（写清为什么排除）

1. **"时钟回拨能换来一次免费满血"** —— 排除。`limits.py:208-219` 的 Lua 在 `delta<0` 时把 `ts` **原地不动**，
   我实测把 `now` 拨回 600s 再拨回 `T+4`：结果仍是 `(False, 2.001)`，桶态 `ts=1000004`（没有被写回过去）。实现是对的。
2. **"并发会让令牌桶超发"** —— 排除。桶的读-改-写在 Redis Lua 里原子：同刻连打 12 次 = 严格 10 放行 + 2 拒，`wait` 折算也准（3.0s / 2.001s）。超发只发生在**日计数**那条（R8）。
3. **"TTL 缺失 ⇒ key 永久堆积"** —— 排除。实测 `bucket:{who}` TTL=60s（= `ceil(burst/rate)*2`），日键 TTL=172800s；`incr_daily` 每次都续期（`limits.py:284-290`）是有意为之且理由成立。
4. **"Redis 重启会把当天额度清零"** —— 排除（配置层）。compose 里 `--appendonly yes --save ""`，且 `verify_limits_e2e.py:270-332` 专门验这一条。
   ★ 我没有实测（卡里禁止重启 Redis），但我读了脚本的判据：它会**先断言持久化开着**再谈数据在不在，这个顺序是对的。
5. **"`/limits` 和 `/chat` 会认成两个客户端"** —— 排除，见 R14（代码逐字相同）。
6. **"IPv6/端口会造成桶分裂"** —— 一半排除。`request.client.host` 是 `(host, port)` 的 host，**不含端口**，端口不构成分裂；
   分裂来自地址本身（一个 /64 可以换 2^64 个地址，归到 R1a）。
7. **"422 请求不计数是漏洞"** —— 排除。非法请求体在 pydantic 就挡掉（`service.py:846-857`），没进管道也没花钱，不计数是对的。
8. **"`X-Client-Id` 会覆盖 `X-Forwarded-For` 造成不一致"** —— 排除。`client_id` 里 XFF 分支优先且两个端点共用同一函数（`limits.py:324-333`）。
9. **"`redis` 包没装会让限流静默失效"** —— 排除（方向上）。`requirements.txt:44` 钉了 `redis==8.1.0`；即便 import 失败，也是走 `_call` 的异常路径 ⇒ `store_down`，closed 下是 503，不是静默放行。

---

## 5. 我查不了的（卡在哪）

1. **任何 Docker / compose / CI 的运行时行为**：本机没有 Docker/WSL（卡里也说了）。`docker-compose.yml`、`ci-compose.override.yml`、`ci.yml` 的 compose 段**只做了静态阅读**。
2. **⑦（重启 Redis 后 AOF 是否真的保住了计数）**：卡里明确不许重启那台 Redis（另一份评审同时在跑），未实测。
3. **R4 的"500 ⇒ 不记账"**：需要起真服务 + 真 key 缺失状态，本机没起服务（起服务要下模型、还会往共享 Redis 写），**未实测**，已标注。
4. **`uvicorn/h11` 实际接受多长的 `X-Forwarded-For`**：决定 R2 的放大倍数（我只实测了 `client_id` 不做截断这一层），未起服务 ⇒ 未实测。
5. **多副本 / 多 worker 的真实行为**：本机只跑单进程，R15 里除"内存版 ×N"外都是我按代码读出来的推理，**没有第二份进程实测**。
6. **反向验证（"五处注入全红"，README:1738-1741）**：仓里没有对应的注入脚本（`scripts/mutate_check*.py` 都是给文档对账脚本用的），我无法复现这个声明，只能标"未复核"。
7. **README 其余 ~140KB / `_tasks/` / `_reviews/`**：没读，本次结论不覆盖它们。

---

## 6. 一句话总评

**现在还不能直接放公网。** 单身份视角下这层是真的（桶原子、回拨保护、TTL、AOF、e2e 判据我都实测/读到过），
但它把"钱包"当成了"每个身份各一个钱包"——换 IP / 换一个头就能换钱包（R1），而这个动作顺手还能把 Redis 撑爆、把服务按停（R2）；
再加上两个 fail-open 的配置措辞（R3）和一份"只在一切正常时才错"的指标（R6）。
上线前最小动作：**全局日额度 + Redis `maxmemory` + 身份归一化并哈希 + R3 的布尔解析 + R4 的 `finally` 记账**，
并把 R13（`test_limits.py` 接进 CI）一起做掉。

