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

# ★ 本次 pytest 进程的标签（2026-10-13 加，起因是盲审 F4）：
#   所有测试用的 `who` 都带这个标签，收尾**只删含它的 key**。
#   第一版收尾是 `flushdb()` —— 清的是**整库**：如果 TEST_REDIS_URL 指着共享的 Redis
#   （默认 `redis://127.0.0.1:6379/0`，而本机服务用的就是 db 0），跑一次测试
#   就把别人的日额度一起抹了。注释当时却写着"只清自己的 key" —— 注释在说谎。
_RUN_TAG = uuid.uuid4().hex[:8]


def _skip_or_fail(msg: str) -> None:
    """Redis 不可用：默认**大声跳过**，`REQUIRE_REDIS=1` 时改成**失败**。

    ★ 为什么要这个开关：没有 Redis 时"跳过"会让 CI 看起来是绿的，
      而实际上一半判据没跑（默认那 23 passed / 15 skipped 就是这种假绿）。
      CI 里要用 `REQUIRE_REDIS=1`，让"Redis 没起来"变成一眼能看见的红。
    """
    if REQUIRE_REDIS:
        pytest.fail(msg)
    pytest.skip(msg)


@pytest.fixture(params=["memory", "redis"])
def store(request):
    """两种存储各跑一遍；Redis 不在时**大声跳过**（除非显式要求）。"""
    if request.param == "memory":
        yield L.InProcessStore()
        return
    if not _OK:
        _skip_or_fail(f"Redis 不可用（{REDIS_URL}）：{_WHY}\n"
                      f"  ⇒ 本用例只跑了内存那半边。要跑全部：\n"
                      f"     1) 起一个 Redis（本机用 Memurai / 容器都行）\n"
                      f"     2) 或设 REQUIRE_REDIS=1 让缺失变成失败（CI 里就是这么用的）")
    s = L.RedisStore(REDIS_URL)
    yield s
    _cleanup_run_keys(s)


@pytest.fixture
def redis_store():
    """只要真 Redis 的用例（TTL 那几条）。Redis 不在时同样走 `_skip_or_fail`。

    ★ 为什么单独留一个：那两条原来写的是 `@pytest.mark.skipif(not _OK, ...)` ——
      那个装饰器**不看 REQUIRE_REDIS**，于是"要求必须有 Redis"的 CI 里它们照样静默跳过。
      盲审 F5 就是指这个：开关声称"让缺失变成失败"，而这两条不认它。
    """
    if not _OK:
        _skip_or_fail(f"Redis 不可用（{REDIS_URL}）：{_WHY}")
    s = L.RedisStore(REDIS_URL)
    yield s
    _cleanup_run_keys(s)


def _cleanup_run_keys(s: L.RedisStore) -> None:
    """只删本次运行自己造的 key（含 `_RUN_TAG`）。

    ★ 别改回 `flushdb()`：它是"一键重置**所有人**的额度"，
      而默认 TEST_REDIS_URL 就是本机服务在用的那个库。
    """
    try:
        keys = list(s._c().scan_iter(f"*{_RUN_TAG}*"))
        if keys:
            s._c().delete(*keys)
    except Exception:                               # noqa: BLE001
        pass


def make_limiter(store, **kw) -> L.Limiter:
    cfg = dict(burst=5, per_min=60.0, daily_req=100, daily_tokens=1000,
               fail_mode="closed", trust_proxy=False, allow_client_id=False)
    cfg.update(kw)
    return L.Limiter(store=store, limits=L.Limits(**cfg))


def who() -> str:
    # ★ 带上 `_RUN_TAG`：收尾靠它认出"哪些 key 是本次运行造的"（见 _cleanup_run_keys）。
    return f"t-{_RUN_TAG}-{uuid.uuid4().hex[:8]}"


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
    ★★ 同样用**增量**（原因见 `test_metrics_are_not_double_counted`）：
      第一版写 `>= 2` 读的是累计值 —— 在共享 Redis 上只要有历史残留，
      把行为改坏它照样绿（盲审 F3 打的就是这个形状）。
    """
    lim, w, now = make_limiter(store), who(), 1_000_000.0
    base = lim.stats()["usage_missing"]
    lim.record(w, usage=None, now=now)               # 完全没拿到
    lim.record(w, usage={"total": 0}, now=now)       # 拿到了但 total 是 0
    assert lim.usage_today(w, now=now)["tokens_used"] == 0      # 不补 0 进 token 账
    delta = lim.stats()["usage_missing"] - base
    assert delta == 2, f"两次都拿不到 usage，usage_missing 只涨了 {delta}"


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
    assert L.client_id("10.0.0.1", {"x-forwarded-for": "9.9.9.9, 10.0.0.1"}, lim) == "ip:9.9.9.9"
    # 没有反代头时退回自称头
    assert L.client_id("10.0.0.1", {"x-client-id": "内部-1"}, lim) == "cid:内部-1"
    # 两个都没有 ⇒ 退回对端地址
    assert L.client_id("10.0.0.1", {}, lim) == "ip:10.0.0.1"
    # ★ 同一个客户端不能因为"这次带不带 XFF"被算成两个人（2026-10-13 修）：
    #   原实现的 XFF 分支返回的是**裸 IP**（没有 `ip:` 前缀），而兜底分支带前缀
    #   ⇒ 同一个 IP 会落进两个不同的桶（`9.9.9.9` 与 `ip:9.9.9.9`）—— 这就是
    #   "身份可以被拆开"的一种，而且不需要攻击者做任何事、只要请求头时有时无。
    assert (L.client_id("9.9.9.9", {"x-forwarded-for": "9.9.9.9"}, lim)
            == L.client_id("9.9.9.9", {}, lim) == "ip:9.9.9.9")


def test_identity_is_bounded_even_with_a_huge_header():
    """R2：身份串直接进 Redis 的 key ⇒ 必须有长度上限。

    ★ 原来 XFF 那段没有任何截断（同一个函数里 `X-Client-Id` 倒是截到 64）：
      实测 4000 字符的 XFF 会造出约 4KB 的 `who`、两个 key 就是 8KB，还能活两天。
    """
    lim = L.Limits(trust_proxy=True)
    got = L.client_id("1.1.1.1", {"x-forwarded-for": "a" * 5000}, lim)
    assert got.startswith("h:") and len(got) == 18, f"没被收敛成定长：{got[:40]!r}…({len(got)})"
    # 正常长度仍然保持可读（不然 `redis-cli --scan` 里全是哈希，排障会变难）
    assert L.client_id("127.0.0.1", {}, lim) == "ip:127.0.0.1"


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


def test_redis_keys_have_ttl_and_namespace(redis_store):
    """Redis 里的 key 必须有 TTL 与可读前缀。

    ★ TTL 那条盯的是一个真实的坑：如果只在"第一次"设 `EXPIRE`，
      而恰巧那次请求失败/进程死掉，这个计数 key 就**永远留在 Redis 里**，
      慢慢堆成内存泄漏。所以每次 incr 都要设。
    """
    s = redis_store
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


def test_redis_day_ttl_covers_the_boundary(redis_store):
    """日 key 的 TTL 要跨得过 UTC 日界（不然 23:59 记的账 00:00 就查不到了）。"""
    s = redis_store
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


# ==================================================================
# 盲审（2026-10-13）打出来的三条：各配一个回归用例
# ==================================================================
def test_env_bool_reads_false_like_values_as_false():
    """R3：`False` / `FALSE` / `no` / `off` 必须都是**假**。

    ★ 这两开关决定"调用方能不能自称身份"，把它们读反是**安全反向**：
      运维写 `TRUST_PROXY_HEADERS=False`（意思是"不要信"）却把它打开了。
      第一版判据只覆盖了 `"true"` 这一个真值、`""`/`"0"` 这两个假值，
      恰好漏掉的就是实际会被写出来的那几种（大小写混淆、`no`、`off`）。
    """
    for v in ("0", "", "false", "False", "FALSE", "no", "NO", "off", "OFF", "  off  "):
        got = L.Limits.from_env({"TRUST_PROXY_HEADERS": v, "ALLOW_CLIENT_ID_HEADER": v})
        assert got.trust_proxy is False and got.allow_client_id is False, f"{v!r} 被判成「开」"
    for v in ("1", "true", "TRUE", "True", "yes", "on"):
        got = L.Limits.from_env({"TRUST_PROXY_HEADERS": v, "ALLOW_CLIENT_ID_HEADER": v})
        assert got.trust_proxy is True and got.allow_client_id is True, f"{v!r} 没被认成「开」"
    # 拼错的、没见过的值 ⇒ 一律当假（失败要朝向安全那一边）
    assert L.Limits.from_env({"TRUST_PROXY_HEADERS": "ture"}).trust_proxy is False


def test_stats_reports_identity_switches(store):
    """R3 的另一半：这两个开关必须能从 /health 看见。

    ★ 配错看不见 = 配错永远不会被发现。`/health` 是部署后第一眼看的接口。
    """
    lim = make_limiter(store, trust_proxy=True, allow_client_id=True)
    st = lim.stats()
    assert st["trust_proxy_headers"] is True
    assert st["allow_client_id_header"] is True


def test_metrics_are_not_double_counted(store):
    """R6：指标只算一遍。

    ★ 第一版 `_bump_local` 先加本地、再写远端，而 `stats()` 读的是
      `远端 + 本地` ⇒ **一切正常时每个指标都被算两遍**（实测 2 次被拒报成 4）。
      恶心之处在于它只在健康时错：Redis 挂掉时远端写不进，`0 + n` 反而是对的。
    ★★ 判据用**增量**，不读累计值：`metric:*` 是全局无限期的计数，
      Redis 那份会跨用例累加（我自己就写错过一版，读到 20 还以为是重复计数）。
      增量口径才问得对问题：「这几步**带来了**几次」。
    """
    lim = make_limiter(store, burst=1, per_min=0.0, daily_req=0, daily_tokens=0)
    w, now = who(), 1_000_000.0
    base = lim.stats()["denied_rate"]
    assert lim.check(w, now=now).allowed                 # 第一次放行（burst=1）
    assert not lim.check(w, now=now).allowed             # 之后两次都被拒
    assert not lim.check(w, now=now).allowed
    delta = lim.stats()["denied_rate"] - base
    assert delta == 2, f"真实被拒 2 次，指标涨了 {delta} ⇒ 被重复计数了"


# ==================================================================
# 盲审 R1：全站总额度（"钱包"那一层）
# ==================================================================
def _reset_global(store, now: float) -> None:
    """把"全站"那两个键清掉。

    ★★ 为什么必须清：全站计数**按设计就是跨身份共享的**（那正是它的意义），
      于是它也会**跨用例共享** —— 前一个用例记下的次数会把这一条的断言顶绿。
      这也是为什么这一层的判据不能像 per-identity 那样靠 uuid 隔离。
      （e2e 脚本里的 reset 出于同一个理由也要清它。）
    """
    day = L.utc_day(now)
    keys = [f"q:{day}:{L.GLOBAL_KEY}:req", f"q:{day}:{L.GLOBAL_KEY}:tok"]
    if isinstance(store, L.RedisStore):
        store._c().delete(*keys)
    else:
        for k in keys:
            store._daily.pop(k, None)                # 白盒：内存版没有 delete


def test_global_daily_quota_caps_all_identities_together(store):
    """R1：全站额度是所有人的总和 —— **换身份换不掉它**。

    ★ 这条盯的是这层的立项理由："防止公网上被人刷爆 API 额度"。
      第一版只有 `q:{day}:{who}:*` 这种带身份的键 ⇒ 钱包是全站一个、配额却是每身份一份，
      换 IP / 换一个头就等于换一份新额度，**所有闸门都是绿的，而账单照烧**。
    """
    lim = make_limiter(store, burst=100, per_min=6000.0, daily_req=0, daily_tokens=0,
                       global_daily_req=2, global_daily_tokens=0)
    a, b, now = who(), who(), 1_000_000.0
    _reset_global(store, now)
    assert lim.check(a, now=now).allowed
    lim.record(a, usage=None, now=now)
    assert lim.check(b, now=now).allowed, "第二个身份应当还能过（全站才用了 1/2）"
    lim.record(b, usage=None, now=now)
    d = lim.check(who(), now=now)                    # 第三个身份
    assert d.reason == "global_req", f"全站额度用满了却没拦住：{d}"
    assert d.http_status() == 503, "全站额度用满意味着『服务今天不干活了』⇒ 503（不是 429）"
    assert 1 <= d.retry_after <= 86_400


def test_global_quota_can_be_disabled(store):
    """0 = 关掉这一层（自部署/演示时用）。"""
    lim = make_limiter(store, burst=100, per_min=6000.0, daily_req=0, daily_tokens=0,
                       global_daily_req=0, global_daily_tokens=0)
    w, now = who(), 1_000_000.0
    _reset_global(store, now)
    for _ in range(5):
        assert lim.check(w, now=now).allowed
        lim.record(w, usage=None, now=now)


def test_global_token_quota(store):
    """token 那本账也必须有全站一层（先花后记 ⇒ 下一次请求才被拦）。"""
    lim = make_limiter(store, burst=100, per_min=6000.0, daily_req=0, daily_tokens=0,
                       global_daily_req=0, global_daily_tokens=100)
    now = 1_000_000.0
    _reset_global(store, now)
    lim.record(who(), usage={"total": 60}, now=now)      # 两个不同身份各花 60
    lim.record(who(), usage={"total": 60}, now=now)
    d = lim.check(who(), now=now)
    assert d.reason == "global_tokens" and d.http_status() == 503, d


def test_usage_today_reports_the_global_layer(store):
    """`/limits` 要能看出"我没超自己额度、但全站满了" —— 否则被拒的原因只能去翻 Redis。"""
    lim = make_limiter(store, burst=100, per_min=6000.0, daily_req=0, daily_tokens=0,
                       global_daily_req=7, global_daily_tokens=99)
    w, now = who(), 1_000_000.0
    _reset_global(store, now)
    lim.record(w, usage={"total": 5}, now=now)
    got = lim.usage_today(w, now=now)
    assert got["global"]["requests_used"] == 1 and got["global"]["requests_limit"] == 7
    assert got["global"]["tokens_used"] == 5 and got["global"]["tokens_limit"] == 99
    st = lim.stats()
    assert st["global_daily_req"] == 7 and st["global_daily_tokens"] == 99


# ==================================================================
# 盲审 R2：内存版的清理（Redis 靠 TTL，内存版只能自己剪）
# ==================================================================
def test_memory_store_prunes_stale_entries():
    """内存版必须剪掉"不是今天的"日计数与闲置太久的桶。

    ★ 原来 `_buckets` / `_daily` 只增不减：单进程挂一天、被换着身份刷，
      进程内存一路涨（Redis 那边有 TTL 兜底，这边没有）。
    """
    s = L.InProcessStore()
    now = 1_000_000.0
    today, yesterday = L.utc_day(now), L.utc_day(now - 86_400)
    s._daily[f"q:{today}:someone:req"] = 3
    s._daily[f"q:{yesterday}:someone:req"] = 9
    s._buckets["bucket:live"] = (1.0, now - 10)
    s._buckets["bucket:stale"] = (1.0, now - 7200)
    s._prune(now)
    assert f"q:{today}:someone:req" in s._daily, "把今天的计数剪掉了"
    assert f"q:{yesterday}:someone:req" not in s._daily, "昨天的计数没剪掉"
    assert "bucket:live" in s._buckets and "bucket:stale" not in s._buckets


def test_memory_store_prunes_automatically_when_it_grows(monkeypatch):
    """长到阈值就顺手剪一次（不需要定时线程）。"""
    monkeypatch.setattr(L, "_PRUNE_AT", 3)
    s = L.InProcessStore()
    now = 1_000_000.0
    yesterday = L.utc_day(now - 86_400)
    for i in range(4):
        s._daily[f"q:{yesterday}:x{i}:req"] = 1
    lim = make_limiter(s, burst=5, per_min=60.0, daily_req=0, daily_tokens=0)
    lim.check("someone", now=now)                     # 触发一次 bucket_allow ⇒ 顺手剪
    assert not any(yesterday in k for k in s._daily), f"没剪：{list(s._daily)[:3]}"
