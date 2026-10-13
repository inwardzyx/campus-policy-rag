# -*- coding: utf-8 -*-
"""`limits.py` 的单元测试：限流桶 + 日配额 + 存储可插拔 + 故障模式。

★ 为什么每个用例都跑**两种存储**（内存 / Redis）：
  它们必须语义一致，而"一致"这件事只能靠同一批用例打两遍。
  只测内存 ⇒ Redis 那条路（Lua 脚本、TTL、increment）从没被执行过；
  只测 Redis ⇒ 本机没装 Redis 的人无法复现。

★ 为什么时间要**注入**而不是 sleep：
  令牌桶的补充速率是"每分钟 per_min 个"，用 sleep 测得等几十秒，又慢又飘。
  所以 `check(now=...)` / `record(now=...)` 收一个时间参数（生产路径用 time.time()）。
  ⇒ 这个参数不是"为了测试而加"的装饰：它让"补充"这一段真的可判。

★ Redis 不在时的行为：**跳过并说清**，不当成通过（`REQUIRE_REDIS=1` 时直接失败）。
  所以本机没 Redis 也能跑内存那半边；CI 里挂个 Redis 服务容器就两种都跑。
"""

from __future__ import annotations

import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

import limits as L

REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://127.0.0.1:6379/0")
REQUIRE_REDIS = os.environ.get("REQUIRE_REDIS", "0") not in ("", "0", "false")


def _redis_ok() -> tuple[bool, str]:
    try:
        import redis
    except ImportError as e:                       # pragma: no cover
        return False, f"没装 redis 客户端（{e}）"
    try:
        c = redis.Redis.from_url(REDIS_URL, socket_timeout=1.0, socket_connect_timeout=1.0)
        c.ping()
        return True, ""
    except Exception as e:                          # noqa: BLE001
        return False, str(e)


_OK, _WHY = _redis_ok()


@pytest.fixture(params=["memory", "redis"])
def store(request):
    """两种存储各跑一遍；Redis 不在时**大声跳过**（除非显式要求）。"""
    if request.param == "memory":
        yield L.InProcessStore()
        return
    if not _OK:
        msg = (f"Redis 不可用（{REDIS_URL}）：{_WHY}\n"
               f"  ⇒ 本用例只跑了内存那半边。要跑全部：\n"
               f"     1) 起一个 Redis（本机用 Memurai / 容器都行）\n"
               f"     2) 或设 REQUIRE_REDIS=1 让缺失变成失败（CI 里就是这么用的）")
        if REQUIRE_REDIS:
            pytest.fail(msg)
        pytest.skip(msg)
    s = L.RedisStore(REDIS_URL)
    yield s
    # 收尾：把本用例的 key 清掉（who 带 uuid ⇒ 前缀唯一）
    try:
        s._c().flushdb()
    except Exception:                               # noqa: BLE001
        pass


def make_limiter(store, **kw) -> L.Limiter:
    cfg = dict(burst=5, per_min=60.0, daily_req=100, daily_tokens=1000,
               fail_mode="closed", trust_proxy=False, allow_client_id=False)
    cfg.update(kw)
    return L.Limiter(store=store, limits=L.Limits(**cfg))


def who() -> str:
    return f"t-{uuid.uuid4().hex[:12]}"


# ==================================================================
# 令牌桶
# ==================================================================
def test_burst_then_denied(store):
    """桶容量就是"一分钟内能突发几次"：burst 次放行，第 burst+1 次拒绝。"""
    lim, w, now = make_limiter(store), who(), 1_000_000.0
    got = [lim.check(w, now=now).allowed for _ in range(7)]
    assert got == [True] * 5 + [False, False], f"期望 5 放行 + 2 拒绝，实际 {got}"
    d = lim.check(w, now=now)
    assert d.reason == "rate" and d.retry_after >= 1, f"拒绝原因/等待时间不对：{d}"
    assert d.http_status() == 429


def test_refill_is_continuous_not_per_minute_bucket(store):
    """补充是连续的：per_min=60 ⇒ 每秒补 1 个，不用等到下一分钟。"""
    lim, w, now = make_limiter(store), who(), 1_000_000.0
    for _ in range(5):
        assert lim.check(w, now=now).allowed
    assert not lim.check(w, now=now).allowed
    # 过 1.0 秒（补 1 个）⇒ 又能过一次，再过就又没了
    assert lim.check(w, now=now + 1.0).allowed
    assert not lim.check(w, now=now + 1.0).allowed


def test_retry_after_matches_deficit(store):
    """Retry-After 要能对上"还差多少令牌"：桶空时约 1/rate 秒，不该拍脑袋给个常数。"""
    lim, w, now = make_limiter(store, burst=2, per_min=6.0), who(), 1_000_000.0   # rate=0.1/s
    lim.check(w, now=now)
    lim.check(w, now=now)
    d = lim.check(w, now=now)
    assert d.reason == "rate"
    # 差 1 个令牌 = 10 秒；给的是向上取整的秒数
    assert 9 <= d.retry_after <= 11, f"Retry-After={d.retry_after}，期望约 10"


def test_bucket_does_not_overfill_when_idle(store):
    """闲很久也只会攒满到 burst，不会攒出一堆额度等着被突发打光。"""
    lim, w, now = make_limiter(store, burst=3, per_min=60.0), who(), 1_000_000.0
    got = [lim.check(w, now=now + 3600.0).allowed for _ in range(5)]
    assert got == [True, True, True, False, False], f"实际 {got}"


def test_clock_rollback_does_not_hand_out_a_free_refill(store):
    """时钟回拨既不该当下放行，也不该在时间追上来之后白送一整桶。

    ★ 这条是我**第二次**写的版本。第一版只断言"回拨当下被拒" ——
      而那个断言在**正确实现**和**错误实现**下都成立 ⇒ 一条假用例
      （反向验证时露的馅：去掉 Lua 里的回拨保护，它照样绿）。
      现在断言的是"回拨之后回到正常时间，只补上真实流逝的那 1 个"：
        · 正确实现（时间戳不往回写）⇒ 补 1 个 ⇒ [放行, 拒绝]
        · 错误实现（时间戳被写到过去）⇒ 真实时间追上来时按那 ~5000 秒补 ⇒ 补满 ⇒ [放行, 放行]
    """
    lim, w, t0 = make_limiter(store, burst=2, per_min=60.0), who(), 1_000_000.0
    assert lim.check(w, now=t0).allowed and lim.check(w, now=t0).allowed
    assert not lim.check(w, now=t0 - 5000.0).allowed, "回拨当下不该放行（桶本来就是空的）"
    got = [lim.check(w, now=t0 + 1.0).allowed, lim.check(w, now=t0 + 1.0).allowed]
    assert got == [True, False], (
        f"回拨之后只该按真实流逝的 1 秒补 1 个令牌，实际 {got} —— "
        f"两个都放行说明回拨把时间戳写到了过去、补发时多算了那 5000 秒")


def test_clients_do_not_share_buckets(store):
    lim, now = make_limiter(store, burst=1, per_min=60.0), 1_000_000.0
    a, b = who(), who()
    assert lim.check(a, now=now).allowed
    assert not lim.check(a, now=now).allowed
    assert lim.check(b, now=now).allowed, "两个客户端的桶串了"


def test_concurrent_requests_do_not_overshoot_memory_store():
    """并发下也不许多放行（内存实现必须靠锁，不能指望 GIL）。

    ★ 这条是**概率性**捕获，不是判据：没锁时能不能撞出来取决于 GIL 切不切。
      本机实测（拿掉锁、切换间隔压到 1µs、200 线程）：**连跑 3 遍只红 1 遍**。
      ⇒ 它留着看趋势可以，但"没锁就一定红"的保证由旁边那条确定性用例提供
        （`test_memory_store_serializes_on_a_lock`）。
    """
    import sys as _sys

    s = L.InProcessStore()
    lim = make_limiter(s, burst=4, per_min=0.0)      # per_min=0：不补充，只测并发
    w, now = who(), 1_000_000.0
    old = _sys.getswitchinterval()
    _sys.setswitchinterval(1e-6)
    try:
        with ThreadPoolExecutor(max_workers=200) as ex:
            results = list(ex.map(lambda _: lim.check(w, now=now).allowed, range(200)))
    finally:
        _sys.setswitchinterval(old)
    assert sum(results) == 4, f"并发下放行了 {sum(results)} 次，期望恰好 4 次（桶容量）"


def test_memory_store_serializes_on_a_lock():
    """确定性版本：主线程占着锁，工作线程就必须进不去。

    ★ 这条盯的是"**令牌桶这条路**有没有被串行化"这个语义，
      所以两种日配额都关掉（`daily_req=0, daily_tokens=0`）——
      否则 `check()` 会接着去问 `get_daily`，而那个方法本来就拿同一把锁：
      工作线程会卡在那里、主线程看到的仍是"被阻塞"⇒ **用例照样绿，
      但它证明的是别的方法拿了锁，不是 bucket_allow 拿了锁**。
      （第一版正是这样：把 bucket_allow 的锁拿掉之后，本用例仍然通过。）
    """
    import threading

    s = L.InProcessStore()
    lim = make_limiter(s, burst=1, per_min=0.0, daily_req=0, daily_tokens=0)
    done = threading.Event()
    with s._lock:
        t = threading.Thread(
            target=lambda: (lim.check("t-lock", now=1.0), done.set()), daemon=True)
        t.start()
        blocked = not done.wait(0.3)
    t.join(5)
    assert blocked, "bucket_allow 没拿锁：主线程占着锁它也照样进去了"
    assert done.is_set(), "放锁之后工作线程还没跑完"


# ==================================================================
# 日配额
# ==================================================================
def test_daily_request_quota(store):
    lim, w, now = make_limiter(store, burst=1000, per_min=6000.0, daily_req=3), who(), 1_000_000.0
    for _ in range(3):
        assert lim.check(w, now=now).allowed
        lim.record(w, usage=None, now=now)
    d = lim.check(w, now=now)
    assert (d.reason, d.http_status()) == ("daily_req", 429), f"实际 {d}"
    assert 1 <= d.retry_after <= 86_400, (
        f"Retry-After 应当是『到 UTC 零点』的秒数，实际 {d.retry_after}")


def test_daily_token_quota_counts_recorded_usage(store):
    lim, w, now = make_limiter(store, burst=1000, per_min=6000.0,
                               daily_req=0, daily_tokens=500), who(), 1_000_000.0
    lim.record(w, usage={"total": 400}, now=now)
    assert lim.check(w, now=now).allowed, "才花 400/500 就拒了"
    lim.record(w, usage={"total": 100}, now=now)     # 累计 500 ⇒ 用满
    d = lim.check(w, now=now)
    assert (d.reason, d.http_status()) == ("daily_tokens", 429), f"实际 {d}"


def test_token_quota_accepts_both_usage_shapes(store):
    """`usage` 有两种来源：ChatResponse 的 {total} 与 langchain 的 {total_tokens}。

    ★ 只认一种的后果很安静：token 账永远是 0 ⇒ 日 token 配额形同虚设，而 /health 看着正常。
    """
    lim, w, now = make_limiter(store, burst=1000, per_min=6000.0,
                               daily_req=0, daily_tokens=100), who(), 1_000_000.0
    lim.record(w, usage={"total_tokens": 60}, now=now)
    lim.record(w, usage={"total": 60}, now=now)
    assert lim.check(w, now=now).reason == "daily_tokens", "两种 usage 形状没有都算进账"


def test_quota_resets_next_utc_day(store):
    lim, w = make_limiter(store, burst=1000, per_min=6000.0, daily_req=1), who()
    day1 = 1_000_000.0
    lim.record(w, usage=None, now=day1)
    assert lim.check(w, now=day1).reason == "daily_req"
    # 跨到下一个 UTC 日
    nxt = day1 + 86_400
    assert L.utc_day(nxt) != L.utc_day(day1)
    assert lim.check(w, now=nxt).allowed, "跨天之后额度没恢复"


def test_missing_usage_is_counted_not_treated_as_zero(store):
    """拿不到 usage 时：不写 token 账，但必须留下 `usage_missing` 的痕。

    ★ 本项目在 token 计量上已经栽过一次同类：把"没拿到"记成 0，
      于是缺口看起来像"真的没花钱"。这里同样不许静默。
    """
    lim, w, now = make_limiter(store), who(), 1_000_000.0
    lim.record(w, usage=None, now=now)
    lim.record(w, usage={"total": 0}, now=now)
    assert lim.usage_today(w, now=now)["tokens_used"] == 0
    assert lim.stats()["usage_missing"] >= 2, f"usage_missing 没记上：{lim.stats()}"


# ==================================================================
# 存储挂了：closed 护钱包 / open 保可用
# ==================================================================
class _BrokenStore:
    """每次调用都炸，模拟"Redis 半死"。"""

    name = "broken"

    def _boom(self, *_a, **_kw):
        raise RuntimeError("connection refused (模拟)")

    bucket_allow = get_daily = get_metric = _boom
    incr_daily = bump = _boom

    def ping(self):
        return False


def test_store_down_fails_closed_by_default():
    lim, w, now = make_limiter(_BrokenStore(), fail_mode="closed"), who(), 1_000_000.0
    d = lim.check(w, now=now)
    assert not d.allowed and d.reason == "store_down"
    assert d.http_status() == 503, "存储挂了该报 503（我们自己坏了），不是 429（你太快了）"
    assert d.retry_after >= 1
    assert lim.stats()["store_errors"] >= 1, "故障没被记进 store_errors"


def test_store_down_fails_open_when_configured():
    lim, w, now = make_limiter(_BrokenStore(), fail_mode="open"), who(), 1_000_000.0
    d = lim.check(w, now=now)
    assert d.allowed and d.reason == "", f"fail_mode=open 时应当放行，实际 {d}"


def test_store_errors_visible_even_while_store_is_down():
    """Redis 挂的那一小段时间里发生的错误，不许恰好消失在它该出现的地方。

    本地计数 + 存储计数要合并：只读存储的话，故障期间的 store_errors 全是 0。
    """
    lim, w, now = make_limiter(_BrokenStore(), fail_mode="open"), who(), 1_000_000.0
    for _ in range(3):
        lim.check(w, now=now)
    assert lim.stats()["store_errors"] >= 3


# ==================================================================
# 身份：谁算在谁头上
# ==================================================================
def test_identity_defaults_to_socket_peer_and_ignores_spoofable_headers():
    """默认配置下，自称的头一个都不认。"""
    lim = L.Limits()
    assert L.client_id("1.2.3.4", {}, lim) == "ip:1.2.3.4"
    assert L.client_id("1.2.3.4", {"x-client-id": "别人"}, lim) == "ip:1.2.3.4"
    assert L.client_id("1.2.3.4", {"x-forwarded-for": "9.9.9.9"}, lim) == "ip:1.2.3.4"


def test_identity_honors_headers_only_when_explicitly_enabled():
    lim = L.Limits(trust_proxy=True, allow_client_id=True)
    # 反代头：取第一跳
    assert L.client_id("10.0.0.1", {"x-forwarded-for": "9.9.9.9, 10.0.0.1"}, lim) == "9.9.9.9"
    # 没有反代头时退回自称头
    assert L.client_id("10.0.0.1", {"x-client-id": "内部-1"}, lim) == "cid:内部-1"
    # 两个都没有 ⇒ 退回对端地址
    assert L.client_id("10.0.0.1", {}, lim) == "ip:10.0.0.1"


def test_identity_header_is_truncated():
    """自称头长度不设限 ⇒ 一个超长 header 就能把 Redis 的 key 撑爆（内存放大）。"""
    lim = L.Limits(allow_client_id=True)
    got = L.client_id("1.1.1.1", {"x-client-id": "x" * 5000}, lim)
    assert len(got) <= 80, f"身份串长度 {len(got)} 没有截断"


def test_today_counters_are_readable_for_the_endpoint(store):
    lim, w, now = make_limiter(store), who(), 1_000_000.0
    lim.record(w, usage={"total": 42}, now=now)
    got = lim.usage_today(w, now=now)
    assert got["requests_used"] == 1 and got["tokens_used"] == 42
    assert got["requests_limit"] == 100 and got["tokens_limit"] == 1000


# ==================================================================
# 配置解析（环境变量 → Limits）
# ==================================================================
def test_from_env_parses_and_defaults():
    got = L.Limits.from_env({})
    assert (got.burst, got.per_min, got.daily_req, got.daily_tokens) == (10, 20.0, 300, 300_000)
    assert got.fail_mode == "closed" and got.redis_url is None
    got2 = L.Limits.from_env({
        "RATE_BURST": "3", "RATE_PER_MIN": "6.5", "DAILY_REQ_QUOTA": "7",
        "DAILY_TOKEN_QUOTA": "8", "LIMIT_FAIL_MODE": "OPEN",
        "TRUST_PROXY_HEADERS": "1", "ALLOW_CLIENT_ID_HEADER": "true",
        "REDIS_URL": "redis://x:6379/1"})
    assert (got2.burst, got2.per_min, got2.daily_req, got2.daily_tokens) == (3, 6.5, 7, 8)
    assert got2.fail_mode == "open" and got2.trust_proxy and got2.allow_client_id
    assert got2.redis_url == "redis://x:6379/1"


def test_from_env_rejects_garbage_loudly():
    """配错了要炸，不许静默回落到默认值 —— 那会让人以为配额生效了。"""
    with pytest.raises(ValueError):
        L.Limits.from_env({"RATE_BURST": "十次"})


@pytest.mark.skipif(not _OK, reason=f"Redis 不可用（{REDIS_URL}）：{_WHY}")
def test_redis_keys_have_ttl_and_namespace():
    """Redis 里的 key 必须有 TTL 与可读前缀。

    ★ TTL 那条盯的是一个真实的坑：如果只在"第一次"设 `EXPIRE`，
      而恰巧那次请求失败/进程死掉，这个计数 key 就**永远留在 Redis 里**，
      慢慢堆成内存泄漏。所以每次 incr 都要设。
    """
    s = L.RedisStore(REDIS_URL)
    lim = make_limiter(s, burst=3, per_min=60.0, daily_req=5)
    w, now = who(), 1_000_000.0
    lim.check(w, now=now)
    lim.record(w, usage={"total": 7}, now=now)
    c = s._c()
    prefix = f"q:{L.utc_day(now)}:{w}"
    assert c.get(f"{prefix}:req") == "1"
    assert c.get(f"{prefix}:tok") == "7"
    assert c.ttl(f"{prefix}:req") > 0, "日配额的 key 没有 TTL ⇒ 会永久堆积"
    assert c.ttl(f"{prefix}:tok") > 0
    assert c.ttl(f"bucket:{w}") > 0, "令牌桶的 key 没有 TTL ⇒ 会永久堆积"


@pytest.mark.skipif(not _OK, reason=f"Redis 不可用（{REDIS_URL}）：{_WHY}")
def test_redis_day_ttl_covers_the_boundary():
    """日 key 的 TTL 要跨得过 UTC 日界（不然 23:59 记的账 00:00 就查不到了）。"""
    s = L.RedisStore(REDIS_URL)
    assert s.day_ttl >= 86_400 * 2 - 1


def test_real_time_default_path_still_works(store):
    """不传 `now` 时走生产路径（`time.time()`）：能连过两次，且当日计数读得出来。

    ★ 这条是给"注入时间"这个设计兜底的：所有用例都用假时间，
      万一 `now=None` 那条分支写错了（比如默认值写成了 0），
      生产路径会变成一个"永远不补充/永远拒绝"的桶，而上面所有用例照样绿。
    """
    lim, w = make_limiter(store, burst=2, per_min=60.0), who()
    assert lim.check(w).allowed
    assert lim.check(w).allowed
    lim.record(w, usage=None)
    assert lim.usage_today(w)["requests_used"] == 1
    assert lim.usage_today(w)["day_utc"] == L.utc_day(time.time())
