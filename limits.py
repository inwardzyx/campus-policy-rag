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


# ==================================================================
# 配置
# ==================================================================
@dataclass(frozen=True)
class Limits:
    """从环境变量读出来的那份配置（默认值都是"开发时够用、公网要按钱包调"）。"""

    burst: int = 10                 # 桶容量：一分钟内的突发上限
    per_min: float = 20.0           # 每分钟补充多少个令牌
    daily_req: int = 300            # 每个客户端每天最多几次请求（0 = 不限）
    daily_tokens: int = 300_000     # 每个客户端每天最多多少 token（0 = 不限）
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
            fail_mode=(env.get("LIMIT_FAIL_MODE") or "closed").strip().lower(),
            trust_proxy=(env.get("TRUST_PROXY_HEADERS") or "0").strip() not in ("", "0", "false"),
            allow_client_id=(env.get("ALLOW_CLIENT_ID_HEADER") or "0").strip() not in ("", "0", "false"),
            redis_url=(env.get("REDIS_URL") or "").strip() or None,
        )


@dataclass
class Decision:
    """一次配额判定的结果。`reason` 为空串表示放行。"""

    allowed: bool
    reason: str = ""                # "" | rate | daily_req | daily_tokens | store_down
    retry_after: int = 0            # 秒；429/503 的 Retry-After 用
    detail: str = ""

    def http_status(self) -> int:
        # ★ 存储挂了是 **503** 不是 429：429 的意思是"你太快了"，而这时该被怪的是我们。
        #   把两者混成一个码，运维看报表时分不清"被刷"和"自己坏了"。
        return 503 if self.reason == "store_down" else 429


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
            self._daily[key] = self._daily.get(key, 0) + n
            return self._daily[key]

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
    """
    if limits.trust_proxy:
        xff = (headers.get("x-forwarded-for") or "").strip()
        if xff:
            # 取第一跳 = 最靠近客户端的那一跳（约定：反代把自己追加在后面）
            return xff.split(",")[0].strip() or (remote or "unknown")
    if limits.allow_client_id:
        cid = (headers.get("x-client-id") or "").strip()
        if cid:
            return f"cid:{cid[:64]}"
    return f"ip:{remote or 'unknown'}"


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
        self._local_metrics[metric] = self._local_metrics.get(metric, 0) + n
        try:
            self.store.bump(metric, n)
        except Exception:                            # noqa: BLE001
            # 存储都挂了还往上抛，会把"记指标"变成新的故障点 ⇒ 只留本地计数。
            pass

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
        if usage:
            total = usage.get("total") or usage.get("total_tokens") or 0
            try:
                total = int(total)
            except (TypeError, ValueError):
                total = 0
            if total > 0:
                self._call(lambda: self.store.incr_daily(f"q:{day}:{who}:tok", total), default=None)
            else:
                self._bump_local(M_USAGE_MISSING)
        else:
            self._bump_local(M_USAGE_MISSING)

    def usage_today(self, who: str, now: float | None = None) -> dict:
        """给 /limits 用：这个客户端今天用了多少、上限多少。"""
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
        }

    def stats(self) -> dict:
        """给 /health 用：全局指标 + 现在是哪种存储。"""
        out = {m: self._merge_metric(m) for m in METRIC_NAMES}
        out["store"] = self.store.name
        out["store_ok"] = self._call(lambda: self.store.ping(), default=False)
        out["fail_mode"] = self.limits.fail_mode
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
