# -*- coding: utf-8 -*-
"""限流 + 日配额：给服务装一个"钱包开关"。

★ 为什么需要这一层（不是"部署顺手加的装饰"）：
  /chat 的默认路径每次要花 **2 次大模型调用**（查询改写 + 精排）+ 1 次生成。
  服务一旦放到公网、又带着真 key，那么"没有配额"就等于**把钱包挂在门口**：
  任何人写个 10 行的循环就能把额度烧光。所以配额不是可选项，是上线的**前提**。

★ 三条设计取舍（都写在这里，因为它们都是"知情取舍"而不是遗忘）：

  ① **拒答路径也计数。** 拒答看似便宜，其实它已经花了改写 + 精排两次调用
     （见 service.py 里那条注释）。只给"答上来的"计数，等于给刷子留了最便宜的路。
     ⇒ 计数单位是【请求】，与答没答上无关；token 账再单独记一本。

  ② **限流用令牌桶（Lua 原子），不是固定窗口。**
     固定窗口实现最短（`INCR` + `EXPIRE`），但它在窗口边界允许 **2× 突发**
     （59 秒打满 + 61 秒再打满），而"防刷"防的正是突发。
     ⇒ 用令牌桶：容量 burst、按 per_min 连续补充。
     ★ 它必须原子 —— 一次请求要读-改-写三个字段，分两次往返就会有竞态
       （两个并发请求同时看到 tokens=1 ⇒ 都放行）。所以整段逻辑放进 Lua，
       由 Redis 单线程执行。**并且 `now` 由调用方传进去**，不用 `redis.call('TIME')` ——
       这样测试能控制时间，令牌补充那一段才可测（否则只能 sleep，测不快也不稳）。

  ③ **存储可插拔**：`REDIS_URL` 给了就用 Redis，没给就用进程内内存。
     内存版**只适合单进程**：uvicorn 多 worker 下每个进程各算各的 ⇒ 配额被放大 N 倍。
     ⇒ 启动时把用的是哪种、以及它的边界打进日志和 /health，不许静默降级成"看起来有配额"。

★ 已知边界（写清楚，免得读的人以为它管得比实际宽）：
  · 身份默认取 TCP 对端 IP。容器/反代后面那是**反代的 IP**，所有用户会共用一个桶 ——
    要按真实客户端分，必须开 `TRUST_PROXY_HEADERS=1` **并且**反代自己负责清洗
    `X-Forwarded-For`（不清洗就等于把身份交给调用方伪造）。
  · `X-Client-Id` 默认**不认**：它由调用方自己填，当身份用等于没限流。
    只在内部多租户场景显式开 `ALLOW_CLIENT_ID_HEADER=1`，且它不是鉴权。
  · 日 token 配额是"**先花后记**"：调用前只能看已经花掉多少 ⇒
    单个请求可能把当天的额度略微超出（超出幅度 = 一个请求的花费）。
    真正硬的是每分钟那道桶。这条不是 bug，是没法在调用前知道花费所导致的。
  · 日期按 **UTC** 切（不按本机时区），多副本部署时口径一致。
"""

from __future__ import annotations

import hashlib
import math
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

# ---- 指标名（/health 会照原样吐出来）----
M_DENIED_RATE = "denied_rate"
M_DENIED_QUOTA = "denied_quota"
M_STORE_ERRORS = "store_errors"
M_USAGE_MISSING = "usage_missing"      # 这次调用没拿到 usage ⇒ token 账少记一截
METRIC_NAMES = (M_DENIED_RATE, M_DENIED_QUOTA, M_STORE_ERRORS, M_USAGE_MISSING)

# ★ 全站额度键里的那个"身份"（盲审 R1）：它不是任何一个真实客户端，
#   而是"所有人加起来"。用固定串而不是哈希，是为了 `redis-cli --scan 'q:*'` 一眼能认出来。
GLOBAL_KEY = "__all__"

# ★ 内存版的清理参数（盲审 R2）。为什么是"顺手剪"而不是定时线程：
#   这个模块是纯逻辑、可以被任何进程 import；起线程会把生命周期问题引进来
#   （谁负责停？测试里漏一个线程就一直挂着）。阈值到了再剪，上界可控。
_PRUNE_AT = 5_000          # 条目数（日计数 + 桶）超过它就剪一次
_BUCKET_IDLE_TTL = 3_600   # 桶闲置超过一小时就丢（Redis 那边的 TTL 是 60s~分钟级，这里宽松些）


# ==================================================================
# 配置
# ==================================================================
def _env_bool(env: dict[str, str], key: str, default: bool = False) -> bool:
    """环境变量里的布尔：**只认一组白名单为真，其余一律为假**。

    ★★ 盲审 R3 打的就是这里，而且是个安全反向：
      第一版写的是 `raw not in ("", "0", "false")` ⇒ `False` / `FALSE` / `no` / `off`
      **全被判成「开」**。这两个开关决定"调用方的身份能不能被伪造"（见 `client_id`），
      运维写 `TRUST_PROXY_HEADERS=False`（看上去是"不要信"）实际把它打开了，
      而且当时 `stats()` 也不报这两个 —— **配错了没有任何地方能看见**。
      ⇒ 白名单：只有明确写"是"的那些才算真；拼错/没见过的值一律当假（失败朝向安全那一边）。
    """
    raw = (env.get(key) or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("", "0", "false", "no", "off"):
        return False
    return default


@dataclass(frozen=True)
class Limits:
    """从环境变量读出来的那份配置（默认值都是"开发时够用、公网要按钱包调"）。"""

    burst: int = 10                 # 桶容量：一分钟内的突发上限
    per_min: float = 20.0           # 每分钟补充多少个令牌
    daily_req: int = 300            # 每个客户端每天最多几次请求（0 = 不限）
    daily_tokens: int = 300_000     # 每个客户端每天最多多少 token（0 = 不限）
    # ★★ 全站总额度（2026-10-13 加，起因是盲审 R1）：
    #   上面那两个是**每个身份**一份，而"钱包"是全站一个 —— 换 IP / 换一个头就换一份新额度。
    #   所以必须另有一组**不带身份**的键兜底，否则"防公网刷爆额度"这句话是假的。
    #   ★ 取舍要写清：全局额度用满 ⇒ **所有人都被拒**（503 到 UTC 零点）。
    #     这正是它该有的行为（护钱包优先），但值必须按钱包定，不能拍脑袋。
    #     默认给的是"演示/自部署"量级（2000 次 / 200 万 token 一天），0 = 关掉这一层。
    global_daily_req: int = 2_000
    global_daily_tokens: int = 2_000_000
    fail_mode: str = "closed"       # Redis 挂了怎么办：closed=拒绝服务（护钱包）/ open=放行
    trust_proxy: bool = False       # 是否信 X-Forwarded-For（反代自己清洗时才开）
    allow_client_id: bool = False   # 是否认 X-Client-Id 这个"自称的身份"
    redis_url: str | None = None    # 没给 ⇒ 进程内内存（单进程用）

    @staticmethod
    def from_env(env: dict[str, str]) -> "Limits":
        def num(key: str, default, cast):
            raw = (env.get(key) or "").strip()
            return default if raw == "" else cast(raw)

        return Limits(
            burst=num("RATE_BURST", 10, int),
            per_min=num("RATE_PER_MIN", 20.0, float),
            daily_req=num("DAILY_REQ_QUOTA", 300, int),
            daily_tokens=num("DAILY_TOKEN_QUOTA", 300_000, int),
            global_daily_req=num("GLOBAL_DAILY_REQ_QUOTA", 2_000, int),
            global_daily_tokens=num("GLOBAL_DAILY_TOKEN_QUOTA", 2_000_000, int),
            fail_mode=(env.get("LIMIT_FAIL_MODE") or "closed").strip().lower(),
            trust_proxy=_env_bool(env, "TRUST_PROXY_HEADERS"),
            allow_client_id=_env_bool(env, "ALLOW_CLIENT_ID_HEADER"),
            redis_url=(env.get("REDIS_URL") or "").strip() or None,
        )


@dataclass
class Decision:
    """一次配额判定的结果。`reason` 为空串表示放行。"""

    allowed: bool
    reason: str = ""                # "" | rate | daily_req | daily_tokens | global_req | global_tokens | store_down
    retry_after: int = 0            # 秒；429/503 的 Retry-After 用
    detail: str = ""

    def http_status(self) -> int:
        # ★ 这些用 **503** 而不是 429：429 的意思是"你太快了"，而这时该被怪的是我们。
        #   把两者混成一个码，运维看报表时分不清"被刷"和"自己坏了/没钱了"。
        #   全局额度用满也归这里：那意味着**服务今天不能干活了**，与调用方无关。
        return 503 if self.reason in ("store_down", "global_req", "global_tokens") else 429


class Store(Protocol):
    """两个实现要长得一样。故意只留这几个方法 —— 接口越大越难保证两边语义一致。"""

    name: str

    def bucket_allow(self, key: str, burst: int, per_min: float, now: float) -> tuple[bool, float]:
        """令牌桶：返回 (是否放行, 若不放行还需等几秒)。"""
        ...

    def incr_daily(self, key: str, n: int) -> int: ...
    def get_daily(self, key: str) -> int: ...
    def bump(self, metric: str, n: int = 1) -> int: ...
    def get_metric(self, metric: str) -> int: ...
    def ping(self) -> bool: ...


# ==================================================================
# 实现一：进程内（单进程 / 测试用）
# ==================================================================
class InProcessStore:
    """一个 dict + 一把锁。

    ★ 为什么还要有它：本地开发和单元测试不该强制装 Redis；
      而且"限流逻辑本身对不对"与"Redis 通不通"是两件事，混在一起测不出是哪个坏了。

    ★ 锁是必须的：service.py 的 /chat 是 `def`（同步）函数，FastAPI 会把它丢进
      线程池并发执行 ⇒ "读-改-写令牌"这三步会真的并发。
      第一版我打算不加锁（想的是"Python 有 GIL"），但 GIL 保证的是**单条字节码**原子，
      不保证"读 tokens → 算 → 写回"这一串原子。
    """

    name = "memory"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buckets: dict[str, tuple[float, float]] = {}     # key -> (tokens, ts)
        self._daily: dict[str, int] = {}
        self._metrics: dict[str, int] = {}

    def bucket_allow(self, key: str, burst: int, per_min: float, now: float) -> tuple[bool, float]:
        rate = per_min / 60.0
        with self._lock:
            self._maybe_prune(now)
            tokens, ts = self._buckets.get(key, (float(burst), now))
            delta = now - ts
            if delta < 0:
                # ★ 时钟回拨：只归零 delta 是不够的，**必须同时不把 ts 往回写** ——
                #   否则等真实时间追上来，补发会按"被重复计算的那段时间"一次补满，
                #   回拨就成了免费满血。语义详见 Redis 那份 Lua 里的长注释。
                delta, now = 0.0, ts
            tokens = min(float(burst), tokens + delta * rate)
            if tokens < 1.0:
                self._buckets[key] = (tokens, now)
                # ★ rate=0（桶不再补充）时给一个有限值而不是 `float('inf')`：
                #   inf 顺着 retry_after 传出去，`math.ceil(inf)` 会直接抛
                #   OverflowError —— 那是个**看起来像"限流器坏了"的 bug**。
                #   实测就是这么被抓到的（见 tests/test_limits.py 的并发用例）。
                return False, ((1.0 - tokens) / rate) if rate > 0 else 60.0
            self._buckets[key] = (tokens - 1.0, now)
            return True, 0.0

    def incr_daily(self, key: str, n: int) -> int:
        with self._lock:
            self._maybe_prune(time.time())
            self._daily[key] = self._daily.get(key, 0) + n
            return self._daily[key]

    # ---- 清理（内存版没有 TTL 机制，只能自己剪）----
    def _maybe_prune(self, now: float) -> None:
        """条目数超过阈值就顺手剪一次（**必须在持锁时调用**）。

        ★ 盲审 R2：内存版的 `_buckets` / `_daily` 原来是**只增不减**的字典 ——
          单进程挂一天、被人换着身份刷，进程内存就一路涨上去。
          Redis 那半边靠 TTL 兜底（每个 key 都 `EXPIRE`），这边没有 TTL 机制，
          所以只能"顺手剪"：**不做定时线程**（那会给这个纯逻辑模块引入生命周期问题）。
          代价写清楚：**在达到阈值之前不会剪**，也就是最坏情况下会多留
          `_PRUNE_AT` 条过期项 —— 这个上界是刻意的、也是可以算出来的。
        """
        if len(self._daily) + len(self._buckets) >= _PRUNE_AT:
            self._prune(now)

    def _prune(self, now: float) -> None:
        """丢掉"不是今天的"日计数与"闲置超过一小时"的桶。持锁调用。"""
        today = time.strftime("%Y-%m-%d", time.gmtime(now))
        self._daily = {k: v for k, v in self._daily.items() if today in k}
        self._buckets = {k: (t, ts) for k, (t, ts) in self._buckets.items()
                         if now - ts < _BUCKET_IDLE_TTL}

    def get_daily(self, key: str) -> int:
        with self._lock:
            return self._daily.get(key, 0)

    def bump(self, metric: str, n: int = 1) -> int:
        with self._lock:
            self._metrics[metric] = self._metrics.get(metric, 0) + n
            return self._metrics[metric]

    def get_metric(self, metric: str) -> int:
        with self._lock:
            return self._metrics.get(metric, 0)

    def ping(self) -> bool:
        return True


# ==================================================================
# 实现二：Redis（多进程 / 多副本共享）
# ==================================================================
# ★ 令牌桶的整段逻辑必须在服务端原子执行。Lua 脚本是 Redis 保证原子的标准做法。
#
# ★ `now` 由调用方传进来（不用 redis.call('TIME')）：
#   测试要能"把时间往前拨"来看令牌怎么补 —— 否则只能 time.sleep，既慢又不稳。
#
# ★ 返回 {是否放行, 还要等几毫秒}：把"算等待时间"也放在脚本里，
#   免得 Python 侧再读一次 tokens（又一次往返 + 又一次竞态）。
_BUCKET_LUA = """
local b = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(b[1])
local ts = tonumber(b[2])
local now = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local burst = tonumber(ARGV[3])
if tokens == nil then
  tokens = burst
  ts = now
end
local delta = now - ts
if delta < 0 then
  -- 时钟回拨（NTP 校时 / 容器迁移都会真的发生）。
  -- ★★ 这里**不能只把 delta 归零、却把 ts 写成 now**：那样时间戳被挪到过去，
  --   等真实时间追上来时，"补发"会把那段被重复计算的时间算进去 ⇒ 一次补满一整桶，
  --   也就是**回拨反而变成一次免费满血**。
  --   ⇒ 正确做法是 ts 原地不动（当这次请求没发生过，时间线不回退）。
  --   ★ 我第一版写的正是"只归零 delta"，而配的用例只断言"回拨当下不放行"——
  --     那条断言在**两种写法下都成立**，是一条假用例（反向验证时才露馅）。
  delta = 0
  now = ts
end
tokens = math.min(burst, tokens + delta * rate)
local wait_ms = 0
local allowed = 0
if tokens >= 1 then
  tokens = tokens - 1
  allowed = 1
else
  if rate > 0 then wait_ms = math.ceil((1 - tokens) / rate * 1000) else wait_ms = 60000 end
end
redis.call('HSET', KEYS[1], 'tokens', tostring(tokens), 'ts', tostring(now))
-- TTL：桶装满所需时间的 2 倍（至少 60 秒）。不设 TTL 的话 key 会无限堆积。
local ttl = 60
if rate > 0 then ttl = math.ceil(burst / rate) * 2 end
redis.call('EXPIRE', KEYS[1], ttl)
return {allowed, wait_ms}
"""


class RedisStore:
    """Redis 实现。连接是懒建的：没被用到就不连（本地测试不装 Redis 也能跑别的用例）。"""

    name = "redis"

    def __init__(self, url: str, day_ttl: int = 172_800) -> None:
        self.url = url
        self.day_ttl = day_ttl            # 2 天：跨过 UTC 日界还能查到昨天的数
        self._client: Any = None          # 懒建，见 _c()
        self._bucket = None

    def _c(self):
        if self._client is None:
            import redis                     # 只有真用 Redis 才 import

            # ★ socket_timeout 必须有：Redis 半死（TCP 通、不响应）时，
            #   默认行为是**无限等** ⇒ 所有 /chat 请求一起挂住。
            #   这是"依赖挂了把自己也拖死"的经典形态，所以限一个明确的短超时，
            #   超时就按 fail_mode 处理（closed=503 / open=放行并记 store_errors）。
            #
            # ★ 顺手关掉"维护通知"探测：redis-py 8.x 默认会试
            #   `CLIENT MAINT_NOTIFICATIONS`，那是 Redis 8 的子命令；
            #   在 Redis 7.x / Memurai 上每次连接都打一行
            #   "Failed to enable maintenance notifications: unknown subcommand"。
            #   噪音的害处不是难看，而是**把真警告淹掉**（本文件的项目里已记过
            #   "常开的警告等于没有警告"）。
            from redis.maint_notifications import MaintNotificationsConfig

            self._client = redis.Redis.from_url(
                self.url, decode_responses=True,
                socket_timeout=1.0, socket_connect_timeout=1.0,
                maint_notifications_config=MaintNotificationsConfig(enabled=False))
            self._bucket = self._client.register_script(_BUCKET_LUA)
        return self._client

    def bucket_allow(self, key: str, burst: int, per_min: float, now: float) -> tuple[bool, float]:
        # ★ 懒建这一步**每个方法都要做**。第一版这里忘了调 `_c()`，于是：
        #   第一次请求 `self._bucket` 还是 None ⇒ `'NoneType' object is not callable`
        #   ⇒ 判成 store_down；而 `_bump_local` 在记指标时**顺手**把客户端建出来了
        #   ⇒ 从第二次起一切正常。**只有第一次坏**的 bug 是最难发现的一类
        #   （监控上看是一条偶发 503、手工重试就好了）。
        #   ⇒ 参数化的用例（内存 + Redis 各跑一遍）当场把它抓了出来。
        self._c()
        allowed, wait_ms = self._bucket(keys=[key], args=[f"{now:.3f}", per_min / 60.0, burst])
        return bool(int(allowed)), float(wait_ms) / 1000.0

    def incr_daily(self, key: str, n: int) -> int:
        c = self._c()
        v = int(c.incrby(key, n))
        # ★ EXPIRE 每次都设：只在 v==n 时设会有个坑 —— 那个请求正好失败的话，
        #   这个 key 就**永远不过期**了（计数留在 Redis 里慢慢堆）。
        c.expire(key, self.day_ttl)
        return v

    def get_daily(self, key: str) -> int:
        v = self._c().get(key)
        return int(v) if v is not None else 0

    def bump(self, metric: str, n: int = 1) -> int:
        return int(self._c().incrby(f"metric:{metric}", n))

    def get_metric(self, metric: str) -> int:
        v = self._c().get(f"metric:{metric}")
        return int(v) if v is not None else 0

    def ping(self) -> bool:
        try:
            return bool(self._c().ping())
        except Exception:
            return False


# ==================================================================
# 判定逻辑（与存储无关，可单独测）
# ==================================================================
def utc_day(now: float) -> str:
    """UTC 日：`2026-10-13`。多副本部署时口径要一致，所以不按本机时区。"""
    return time.strftime("%Y-%m-%d", time.gmtime(now))


def client_id(remote: str | None, headers: dict[str, str], limits: Limits) -> str:
    """这次请求算在谁头上。

    ★ 顺序是**从最不可伪造到最可伪造**：反代头的可信度取决于反代有没有清洗，
      自称头永远不可信。默认只认 TCP 对端地址（`remote`）。

    ★★ 定长兜底（2026-10-13 加，起因是盲审 R2）：
      身份串会直接进 Redis 的 key（`bucket:{who}` / `q:{day}:{who}:req`）。
      第一版对 XFF 那段**没有长度上限** —— 实测 4000 字符的 `X-Forwarded-For`
      会造出约 4KB 的 who、两个 key 就是 8KB，还能活两天 ⇒ 换着身份刷就能把 Redis 撑爆。
      （同一个函数里 `X-Client-Id` 倒是老老实实截到 64 —— 一处做了、另一处漏了。）
      ⇒ 现在：各分支先各自截断，最后再兜一道"超过 64 字符就哈希成定长"。
        注意**保留可读形式**是刻意的：正常情况 `ip:1.2.3.4` 一眼能认，
        只有异常长的才变成 `h:<16 位>`（那时可读性本来也没意义了）。
    """
    who = None
    if limits.trust_proxy:
        xff = (headers.get("x-forwarded-for") or "").strip()
        if xff:
            # 取第一跳 = 最靠近客户端的那一跳（约定：反代把自己追加在后面）
            who = f"ip:{xff.split(',')[0].strip()[:64] or (remote or 'unknown')}"
    # ⚠️ 这里**不是 elif**：原实现的语义是"信了反代但这一跳没有 XFF ⇒ 继续看自称头"。
    #    改成 elif 会让 (trust_proxy=True, allow_client_id=True, 无 XFF) 直接落到对端 IP，
    #    那是一条**悄悄改变身份口径**的改动 —— 语义要原样保住。
    if who is None and limits.allow_client_id:
        cid = (headers.get("x-client-id") or "").strip()
        if cid:
            who = f"cid:{cid[:64]}"
    if who is None:
        who = f"ip:{remote or 'unknown'}"
    if len(who) > 64:
        return "h:" + hashlib.sha1(who.encode("utf-8")).hexdigest()[:16]
    return who


@dataclass
class Limiter:
    """把 store + limits 拼成一个能问"这次能不能过"的对象。"""

    store: Store
    limits: Limits
    log: Any = None                                  # 给个 logger 就记日志，不给就闭嘴
    _local_metrics: dict[str, int] = field(default_factory=dict)

    # ---- 内部：所有 store 调用都要过这里，好统一处理"存储挂了"----
    def _call(self, fn, default):
        try:
            return fn()
        except Exception as e:                       # noqa: BLE001 —— 存储层的任何异常都算"挂了"
            self._bump_local(M_STORE_ERRORS)
            if self.log is not None:
                self.log.warning("限流存储出错（%s）；fail_mode=%s", e, self.limits.fail_mode)
            return default

    def _bump_local(self, metric: str, n: int = 1) -> None:
        """记一个指标：**只在远端写失败时**才落到本地。

        ★★ 盲审 R6 抓到的就是这个函数的第一版：它**先加本地、再写远端**，
          而 `_merge_metric` 读的是 `remote + local` ⇒ 一切正常时**每个指标都被算两遍**。
          实测：真实被拒 2 次，`/health` 报 `denied_rate = 4`。
          ⚠️ 这类 bug 的恶心之处在于**只在健康时错**：Redis 挂掉时远端写不进，
             `remote(0) + local(n)` 反而是对的 —— 于是"平时报两倍、坏了才准"，
             谁看监控都会先怀疑是流量变了。
          ⇒ 现在本地那份**只装"还没进远端"的部分**，两边加起来才是真值。
        """
        try:
            self.store.bump(metric, n)
            return                                   # 远端收下了 —— 本地不再重复记
        except Exception:                            # noqa: BLE001
            # 存储都挂了还往上抛，会把"记指标"变成新的故障点 ⇒ 只留本地计数。
            self._local_metrics[metric] = self._local_metrics.get(metric, 0) + n

    # ---- 对外 ----
    def check(self, who: str, now: float | None = None) -> Decision:
        """进 /chat 之前问一次。"""
        now = time.time() if now is None else now
        day = utc_day(now)
        lim = self.limits

        res = self._call(
            lambda: self.store.bucket_allow(f"bucket:{who}", lim.burst, lim.per_min, now),
            default=None)
        # ★ 注意 `_call` 在存储炸了时返回的是 **None（一个值）**，不是 (False, 0) ——
        #   第一版这里直接 `allowed, wait = self._call(...)`，于是存储一挂就
        #   `TypeError: cannot unpack non-iterable NoneType`，而那是**500**，
        #   恰好把"该报 503 的故障"变成了另一种故障。
        if res is None:
            # 存储挂了：closed ⇒ 拒绝（护钱包），open ⇒ 放行（可用性优先）
            if lim.fail_mode == "open":
                return Decision(True, detail="store_down 但 fail_mode=open，本次放行")
            return Decision(False, reason="store_down", retry_after=5,
                            detail="限流存储不可用；fail_mode=closed ⇒ 拒绝服务（宁可不服务，也不烧钱）")
        allowed, wait = res
        if not allowed:
            self._bump_local(M_DENIED_RATE)
            secs = 3600 if not math.isfinite(wait) else max(1, math.ceil(wait))
            return Decision(False, reason="rate", retry_after=secs,
                            detail=f"超过每分钟 {lim.per_min:g} 次的速率限制")

        if lim.daily_req > 0:
            used = self._call(lambda: self.store.get_daily(f"q:{day}:{who}:req"), default=None)
            if used is None:
                return self._store_down_decision()
            if used >= lim.daily_req:
                self._bump_local(M_DENIED_QUOTA)
                return Decision(False, reason="daily_req", retry_after=_secs_to_utc_midnight(now),
                                detail=f"今日请求额度已用完（{used}/{lim.daily_req}）")

        if lim.daily_tokens > 0:
            used_tok = self._call(lambda: self.store.get_daily(f"q:{day}:{who}:tok"), default=None)
            if used_tok is None:
                return self._store_down_decision()
            if used_tok >= lim.daily_tokens:
                self._bump_local(M_DENIED_QUOTA)
                return Decision(False, reason="daily_tokens", retry_after=_secs_to_utc_midnight(now),
                                detail=f"今日 token 额度已用完（{used_tok}/{lim.daily_tokens}）")

        # ★★ 全站总额度（盲审 R1）：**不带身份**的键，最后一道，也是护钱包的那一道。
        #   为什么放在最后查：前面那几条能给出更具体的拒绝原因（"你的额度用完了"），
        #   而这条的含义是"**服务**今天没钱了" —— 给谁都一样，所以放最后。
        #   ⚠️ 顺序不改判"是否被拦"，只改"报哪条原因"：全局用满时，前面几条也可能同时不满足，
        #      但那时更该看到的是 daily_req 这种"你能自己解决"的信息。
        if lim.global_daily_req > 0:
            g = self._call(lambda: self.store.get_daily(f"q:{day}:{GLOBAL_KEY}:req"), default=None)
            if g is None:
                return self._store_down_decision()
            if g >= lim.global_daily_req:
                self._bump_local(M_DENIED_QUOTA)
                return Decision(False, reason="global_req", retry_after=_secs_to_utc_midnight(now),
                                detail=f"全站今日请求额度已用完（{g}/{lim.global_daily_req}）"
                                       f"—— 这一层防的是『钱包被刷爆』，不是『你太频繁』")
        if lim.global_daily_tokens > 0:
            gt = self._call(lambda: self.store.get_daily(f"q:{day}:{GLOBAL_KEY}:tok"), default=None)
            if gt is None:
                return self._store_down_decision()
            if gt >= lim.global_daily_tokens:
                self._bump_local(M_DENIED_QUOTA)
                return Decision(False, reason="global_tokens", retry_after=_secs_to_utc_midnight(now),
                                detail=f"全站今日 token 额度已用完（{gt}/{lim.global_daily_tokens}）")

        return Decision(True)

    def record(self, who: str, usage: dict | None, now: float | None = None) -> None:
        """请求处理完之后记一次账。

        ★ 请求数和 token 数分开记，是因为它们的用途不同：
          请求数能**事前**判（硬闸），token 数只能**事后**记（软闸，见模块 docstring 边界 ③）。
        ★ usage 取不到时**不许补 0**（本项目已记过这条：补 0 会让"没拿到"看起来像"没花费"），
          改为把 usage_missing 记上，让 /health 能看出"token 账有缺口"。
        """
        now = time.time() if now is None else now
        day = utc_day(now)
        self._call(lambda: self.store.incr_daily(f"q:{day}:{who}:req", 1), default=None)
        # ★ 全站计数与身份计数**一起记**（盲审 R1）：只记身份那一份的话，
        #   全局那道闸永远读到 0，等于没接。两处必须同进同出。
        self._call(lambda: self.store.incr_daily(f"q:{day}:{GLOBAL_KEY}:req", 1), default=None)
        if usage:
            total = usage.get("total") or usage.get("total_tokens") or 0
            try:
                total = int(total)
            except (TypeError, ValueError):
                total = 0
            if total > 0:
                self._call(lambda: self.store.incr_daily(f"q:{day}:{who}:tok", total), default=None)
                self._call(lambda: self.store.incr_daily(f"q:{day}:{GLOBAL_KEY}:tok", total),
                           default=None)
            else:
                self._bump_local(M_USAGE_MISSING)
        else:
            self._bump_local(M_USAGE_MISSING)

    def usage_today(self, who: str, now: float | None = None) -> dict:
        """给 /limits 用：这个客户端今天用了多少、上限多少（外加全站那一层的口径）。"""
        now = time.time() if now is None else now
        day = utc_day(now)
        return {
            "who": who,
            "day_utc": day,
            "requests_used": self._call(lambda: self.store.get_daily(f"q:{day}:{who}:req"), default=-1),
            "tokens_used": self._call(lambda: self.store.get_daily(f"q:{day}:{who}:tok"), default=-1),
            "requests_limit": self.limits.daily_req,
            "tokens_limit": self.limits.daily_tokens,
            "rate_burst": self.limits.burst,
            "rate_per_min": self.limits.per_min,
            # ★ 全站那一层也报出来（盲审 R1）：不然"我明明没超自己额度却被拒"这件事
            #   在 /limits 里看不出来，只能去翻 Redis。
            "global": {
                "requests_used": self._call(
                    lambda: self.store.get_daily(f"q:{day}:{GLOBAL_KEY}:req"), default=-1),
                "tokens_used": self._call(
                    lambda: self.store.get_daily(f"q:{day}:{GLOBAL_KEY}:tok"), default=-1),
                "requests_limit": self.limits.global_daily_req,
                "tokens_limit": self.limits.global_daily_tokens,
            },
        }

    def stats(self) -> dict:
        """给 /health 用：全局指标 + 现在是哪种存储。"""
        out = {m: self._merge_metric(m) for m in METRIC_NAMES}
        out["store"] = self.store.name
        out["store_ok"] = self._call(lambda: self.store.ping(), default=False)
        out["fail_mode"] = self.limits.fail_mode
        # ★ 把两个"身份可信度"开关也报出来（盲审 R3）：它们决定"调用方能不能自称身份"，
        #   而第一版 /health 看不到它们 ⇒ 开关配反了（`False` 被判成开）也没有任何地方能看见。
        #   `/health` 本来就是"部署完第一眼看的那个接口"，口径必须在这里。
        out["trust_proxy_headers"] = self.limits.trust_proxy
        out["allow_client_id_header"] = self.limits.allow_client_id
        # ★ 全站额度（盲审 R1）：这是"钱包还剩多少"的那道闸，口径同样必须在一眼能看到的地方。
        out["global_daily_req"] = self.limits.global_daily_req
        out["global_daily_tokens"] = self.limits.global_daily_tokens
        return out

    def _merge_metric(self, metric: str) -> int:
        """本地计数 + 存储里的计数。

        ★ 为什么要合并：Redis 挂的那一小段时间里，`bump` 写不进去、只能落在本地
          （见 _bump_local 的 except）——不合并的话，正好在故障期间发生的
          store_errors 会**恰好消失在它该出现的地方**。
        """
        remote = self._call(lambda: self.store.get_metric(metric), default=0)
        return int(remote or 0) + self._local_metrics.get(metric, 0)

    def _store_down_decision(self) -> Decision:
        if self.limits.fail_mode == "open":
            return Decision(True, detail="store_down 但 fail_mode=open，本次放行")
        return Decision(False, reason="store_down", retry_after=5,
                        detail="限流存储不可用；fail_mode=closed ⇒ 拒绝服务（宁可不服务，也不烧钱）")


def _secs_to_utc_midnight(now: float) -> int:
    """到 UTC 零点还有多少秒（日额度的 Retry-After 用这个，才对得上恢复时间）。"""
    day = 86_400
    return max(1, int(day - (now % day)))


def build_limiter(env: dict[str, str], log: Any = None) -> Limiter:
    """按环境变量造一个 limiter。服务启动时调一次。"""
    limits = Limits.from_env(env)
    if limits.redis_url:
        store: Store = RedisStore(limits.redis_url)
    else:
        store = InProcessStore()
    return Limiter(store=store, limits=limits, log=log)
