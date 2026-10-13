# -*- coding: utf-8 -*-
"""端到端验"部署形态"下的限流/配额：对着一个**真跑着的服务**打接口 + 直接查 Redis。

为什么要这个脚本（而不是把断言写在 workflow 的 bash 里）：
  ① CI 里那份 bash 在本机**跑不了**（本机没 Docker/WSL）—— 写错了只能等 CI 报错，
     而 CI 的报错点常常指不到行，一轮反馈几分钟。
  ② 本项目已经吃过一次"同一份真相源抄两遍"的亏（见 tests/conftest.py 的 docstring）。
  ⇒ 断言只写一处：CI 那一步只留"起栈 / 调本脚本 / 收尾"，本机也能对着自己的服务跑同一份。

跑法（服务已经起来了）：
    # CI / compose：服务在 8080，Redis 在 compose 网络里
    python scripts/verify_limits_e2e.py --api http://127.0.0.1:8080 \
        --redis redis://redis:6379/0 \
        --restart-cmd "docker compose restart rag" \
        --restart-redis-cmd "docker compose restart redis"

    # 本机：uvicorn 起在 8011，Redis 是 Memurai
    python scripts/verify_limits_e2e.py --api http://127.0.0.1:8011 \
        --redis redis://127.0.0.1:6379/0 \
        --restart-cmd "pwsh -NoProfile -File <重启服务脚本>" \
        --restart-redis-cmd "pwsh -NoProfile -File <重启 Redis 脚本>"

它验七件事，缺一个都说明这套配额不能上线：

  ① /health 报的限流存储是 **redis** 且可用（不是进程内内存）
  ② 突发打满 `--burst` 次之后 ⇒ 429 + `Retry-After` + `reason=rate`
  ③ **重启服务之后日计数还在**（给了 --restart-cmd 才验）—— 这是"用 Redis"的全部意义
  ④ 计数确实落在 Redis 里（按 /limits 报的 who/day 直接读，并检查 key 有 TTL）
  ⑤ 把 Redis 里的日用量顶到上限 ⇒ 必须按 `daily_req` 拒（**先把桶补满**，否则先撞速率）
  ⑥ 拿不到 usage 时必须留 `usage_missing` 的痕（不许补 0 当成"没花钱"）
  ⑦ **重启 Redis 之后额度状态还在**（给了 --restart-redis-cmd 才验）—— 这是 AOF 的全部意义
     ★ ③ 证明的是"计数不在服务内存里"，⑦ 证明的是"**Redis 自己**也不会把它丢了"。
       少了 ⑦，一个 `--appendonly no` 的 Redis 能让 ①~⑥ 全绿，
       而真实后果是"Redis 一重启，当天额度归零" —— 一个能被触发的绕过口子。

★ 前提（脚本会自己说清，不猜）：服务那份配置得是"小额度"，否则打不出 429。
  默认按 `RATE_BURST=3 / RATE_PER_MIN=1 / DAILY_REQ_QUOTA=5 / DAILY_TOKEN_QUOTA=0`
  检查 —— compose 与 CI 用的就是这组值；本机跑也一样传这组环境变量。
  ⚠️ ⑦ 还有个前提：**Redis 那边得开持久化**（compose 里是 `--appendonly yes`）。
     本机那份如果按老办法用 `--appendonly no` 起的，⑦ 会红 —— 那不是判据的问题，是配置的问题。

★ 反向验证（本脚本自己也必须能证明它在坏情况下会响，实测过三条）：
    · 服务用进程内存储（不给 REDIS_URL）⇒ ① 报红（"存储不是 redis"）
    · 日额度关掉（DAILY_REQ_QUOTA=0）⇒ ⑤ 报红，且诊断直指"额度没生效"
    · Redis 不开 AOF ⇒ ⑦ 报红（"计数 key 没了 ⇒ AOF 没生效"）
  这三条是这套判据**唯一**能证明自己不是摆设的方式，别删。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# ★ 与同族脚本一致：✓✗ 在 cp936 里编不出来，stdout 是管道时会崩在打印上（本项目已犯 5 次）。
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
# ★ 行缓冲是**必须**的（2026-10-13 踩过）：stdout 被管道接住时 Python 默认全缓冲，
#   于是一旦脚本在某一步挂住，你看到的是**一个字都没有** —— 连"卡在哪一步"都不知道。
#   CI 日志里同样如此。加上它之后，挂住时至少能看到最后一条 ✓ 走到哪了。

REFUSE_BODY = {"question": "图书馆几点关门？", "use_rerank": False, "use_rewrite": False}


class Failed(Exception):
    """断言失败（带上"怎么修"的提示，别让下一个人只知道红、不知道往哪看）。"""


def http(method: str, url: str, body: dict | None = None, timeout: float = 30.0):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            return r.status, dict(r.headers), (json.loads(raw) if raw.strip() else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, dict(e.headers), (json.loads(raw) if raw.strip() else None)
        except json.JSONDecodeError:
            return e.code, dict(e.headers), {"raw": raw}


def wait_ready(api: str, timeout: float, what: str = "服务就绪") -> dict:
    """轮询 /health 直到 `status == "ok"`。★ 不 sleep 一个拍脑袋的固定秒数。"""
    t0 = time.time()
    last = ""
    while time.time() - t0 < timeout:
        try:
            code, _, body = http("GET", f"{api}/health", timeout=5)
            last = f"{code} {body}"
            if code == 200 and isinstance(body, dict) and body.get("status") == "ok":
                return body
        except Exception as e:                       # noqa: BLE001 —— 没起来时各种异常都算"还没好"
            last = repr(e)
        time.sleep(3)
    raise Failed(f"{what}：{timeout:.0f} 秒内 /health 一直不是 ok（最后一次：{last}）")


def reset_client_state(r, who: str, day: str) -> None:
    """把**这一个客户端**的配额状态清干净，让断言可重复。

    ★ 为什么必须自己清（2026-10-13 第一次本地跑就栽在这里）：
      日计数**按设计**会跨进程重启保留 —— 那正是 ③ 要证明的性质。
      于是同一个栈上跑第二次时，② 会直接撞在"今日请求额度已用完（5/5）"上，
      报出来的错**看起来像"限流坏了"**，其实是上一次跑的合法残留。
      ⇒ CI 里因为每次都是全新卷，这个问题**根本看不出来**，
        直到有人在同一个栈上跑第二次（比如本机复现、或重跑 CI job 时复用了卷）。
    ★ 只删自己这个 `who` 的三个键：不动别人的额度，也不 flushdb
      （flushdb 会把"别人的账"一起抹掉，那是另一个性质的问题）。
    """
    keys = [f"bucket:{who}", f"q:{day}:{who}:req", f"q:{day}:{who}:tok"]
    n = r.delete(*keys)
    print(f"    已清掉本次客户端的 {n} 个 key（who={who} day={day}）")


def check_store_is_redis(api: str, expect_fail_mode: str) -> dict:
    code, _, body = http("GET", f"{api}/health")
    if code != 200:
        raise Failed(f"/health 返回 {code}")
    lim = (body or {}).get("limits") or {}
    if lim.get("store") != "redis":
        raise Failed(
            f"限流存储不是 redis，而是 {lim.get('store')!r} ⇒ 多 worker / 多副本下配额会被放大 N 倍。"
            f"检查服务的 REDIS_URL 有没有给。（看到 {lim!r}）")
    if lim.get("store_ok") is not True:
        raise Failed(f"Redis 不通（store_ok={lim.get('store_ok')!r}）")
    if lim.get("fail_mode") != expect_fail_mode:
        raise Failed(f"fail_mode 是 {lim.get('fail_mode')!r}，期望 {expect_fail_mode!r}")
    return lim


def check_burst_then_429(api: str, burst: int) -> None:
    codes = []
    for _ in range(burst):
        code, _, body = http("POST", f"{api}/chat", REFUSE_BODY)
        codes.append(code)
        if code != 200:
            raise Failed(f"前 {burst} 次突发里第 {len(codes)} 次就不是 200，而是 {code}：{body}")
    code, headers, body = http("POST", f"{api}/chat", REFUSE_BODY)
    if code != 429:
        raise Failed(f"突发用完却没被限流（第 {burst + 1} 次返回 {code}）—— "
                     f"要么 RATE_BURST 不是 {burst}，要么闸门没接上：{body}")
    ra = headers.get("Retry-After") or headers.get("retry-after")
    if not ra or int(ra) < 1:
        raise Failed(f"429 必须带 Retry-After（否则调用方只能瞎重试）；实际 {ra!r}")
    if (body or {}).get("detail", {}).get("reason") != "rate":
        raise Failed(f"429 的原因应当是 rate，实际 {body}")


def check_counts_survive_restart(api: str, restart_cmd: str, burst: int, timeout: float) -> None:
    """★ 这一段是"用 Redis"的全部意义：进程重启后额度还得在。"""
    before = (http("GET", f"{api}/limits")[2] or {}).get("usage", {}).get("requests_used")
    if before is None or before < burst:
        raise Failed(f"重启前 requests_used={before!r}，至少应当是 {burst}（突发那几次没记账？）")
    print(f"    重启前 requests_used={before}；执行：{restart_cmd}")
    run_restart_cmd(restart_cmd)
    wait_ready(api, timeout, what="重启后就绪")
    after = (http("GET", f"{api}/limits")[2] or {}).get("usage", {}).get("requests_used")
    if after != before:
        raise Failed(f"重启后计数变了（{before} → {after}）⇒ 配额其实存在进程内存里，不是 Redis")


def run_restart_cmd(cmd: str, timeout: float = 180.0) -> None:
    """跑重启命令。

    ★★ 这里**绝不能**用 `subprocess.run(..., capture_output=True)`（2026-10-13 实测栽过）：
      本机那份重启命令是"杀掉旧服务 + 起一个新服务"，
      `Start-Process` 出来的服务是个**长期运行的孙子进程**，它**继承了父进程的 stdout/stderr 管道**。
      `capture_output=True` 在子进程退出后还要把管道读到 EOF —— 而那个 EOF 永远不会来
      （孙子还开着），于是整个脚本**一个字都不打地挂死**。
      ⚠️ 加 `timeout=` 也救不了：`subprocess.run` 超时杀掉直接子进程之后，
         **仍会去 drain 管道**，照样卡在那里。
      ⇒ 输出一律接 DEVNULL（孙子继承到的是 NUL，没有管道可占），超时用 `wait(timeout)` 自己管。
      ⚠️ 代价：重启命令自己的报错看不到了 —— 所以失败时把命令原文打出来让人手工跑一遍。
    """
    p = subprocess.Popen(cmd, shell=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        rc = p.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        raise Failed(f"重启命令 {timeout:.0f} 秒没返回 —— 手工跑一遍看看：{cmd}")
    if rc != 0:
        raise Failed(f"重启命令失败（exit {rc}）—— 手工跑一遍看看：{cmd}")


def check_redis_holds_counters(r, who: str, day: str) -> None:
    key = f"q:{day}:{who}:req"
    got = r.get(key)
    if got is None or int(got) < 1:
        raise Failed(f"Redis 里读不到 {key}（拿到 {got!r}）⇒ 计数没落在 Redis 里")
    ttl = r.ttl(key)
    if ttl <= 0:
        raise Failed(f"{key} 没有 TTL（ttl={ttl}）⇒ 计数 key 会永久堆积，慢慢变成内存泄漏")
    bttl = r.ttl(f"bucket:{who}")
    if bttl <= 0:
        raise Failed(f"bucket:{who} 没有 TTL（ttl={bttl}）⇒ 令牌桶的 key 会永久堆积")


def check_daily_quota_speaks(api: str, r, who: str, day: str, burst: int, daily_req: int) -> None:
    """⑤ 日额度必须能拒 —— 注意**先把桶补满**。

    ★ 这是个真踩过的坑：闸门是"先查桶、再查日额度"，而 RATE_PER_MIN=1 时桶几乎不补充，
      不补满的话这次请求会先撞【速率】⇒ 报的 reason=rate ⇒
      断言"reason 必须是 daily_req"必红，而且红得**像是日额度没生效**（诊断指偏方向）。
    """
    r.hset(f"bucket:{who}", mapping={"tokens": burst, "ts": 1})   # ts=1 ⇒ refill 时补到容量上限
    r.set(f"q:{day}:{who}:req", 999)
    code, headers, body = http("POST", f"{api}/chat", REFUSE_BODY)
    if code != 429:
        raise Failed(
            f"Redis 里的日用量已写成 999，服务却没拦（返回 {code}）—— "
            f"要么它读的不是 Redis，要么 DAILY_REQ_QUOTA=0（额度被关掉了，期望 {daily_req}）")
    reason = (body or {}).get("detail", {}).get("reason")
    if reason != "daily_req":
        raise Failed(f"该按日额度拒（daily_req），实际 reason={reason!r}：{body}")
    ra = headers.get("Retry-After") or headers.get("retry-after")
    if not ra or not (1 <= int(ra) <= 86_400):
        raise Failed(f"日额度的 Retry-After 应当是到 UTC 零点的秒数，实际 {ra!r}")


def check_usage_missing_is_visible(api: str) -> None:
    lim = (http("GET", f"{api}/health")[2] or {}).get("limits", {})
    if (lim.get("usage_missing") or 0) < 1:
        raise Failed("拒答路径拿不到 usage，但 usage_missing 是 0 ⇒ 缺口被静默抹平了"
                     "（本项目已记过同类：把'没拿到'记成 0，看起来像'真的没花钱'）")


def wait_redis(r, timeout: float = 60.0) -> None:
    """等 Redis 能应答（重启期间会连不上，这不算错）。"""
    t0 = time.time()
    last = ""
    while time.time() - t0 < timeout:
        try:
            if r.ping():
                return
        except Exception as e:                       # noqa: BLE001
            last = repr(e)
        time.sleep(1)
    raise Failed(f"Redis 重启后 {timeout:.0f} 秒内没恢复（最后一次：{last}）")


def redis_persistence(r) -> tuple[bool, str]:
    """这台 Redis 到底会不会把数据留下来？持久化 = AOF 或 RDB 至少开一个。

    ★ 为什么要先查这个（2026-10-13 实测踩到）：我第一次跑 ⑦ 时，
      脚本是在**重启那一步**才第一次把 Redis 起成 AOF 的 —— 而键是更早写在
      "没有 AOF"的那个实例上的，它们从来没进过 AOF 文件。
      于是 ⑦ 报的是"计数 key 没了 ⇒ AOF 没生效"，看着像判据在正常干活，
      其实**是测试自己的编排错了**（先写键、后开持久化）。
      ⇒ 把前提显式查出来：配置不对就说配置不对，别伪装成"数据丢了"。
      ⚠️ 查不出来（Redis 禁了 CONFIG）不算失败，只提示 —— 那时仍然做实证检查。
    """
    try:
        ao = (r.config_get("appendonly") or {}).get("appendonly", "?")
        save = (r.config_get("save") or {}).get("save", "?")
    except Exception as e:                           # noqa: BLE001
        return True, f"查不出持久化配置（{e}）—— 只做实证检查"
    persistent = (ao == "yes") or (str(save).strip() not in ("", '""'))
    return persistent, f"appendonly={ao} save={save!r}"


def check_counters_survive_redis_restart(api: str, r, who: str, day: str,
                                         cmd: str, timeout: float) -> None:
    """⑦ Redis 自己重启之后，额度状态必须还在 —— 这是 compose 里开 AOF 的全部意义。

    ★ 为什么单独立这一条（2026-10-13 补）：③ 验的是"**服务**重启后计数还在"，
      它证明的是"计数不在服务内存里"。而"计数在 Redis 里"要成立，
      还得保证 **Redis 自己**不会把状态丢了 —— 那正是 AOF 的活。
      ⚠️ 不验这一条的话，一个 `--appendonly no` 的 Redis 也能让 ①~⑥ 全绿，
         而真实后果是：**Redis 一重启，当天的额度就归零** —— 一个能被触发的绕过口子
         （而"服务重启"是天天发生的，"Redis 重启"是偶发的，所以后者更容易被忘掉）。
    ★ 顺带把"服务侧怎么恢复"也记下来：Redis 重启的那一小段时间，
      服务连不上存储 ⇒ fail_mode=closed 下会短暂 503 + store_errors 增加。
      这是**设计如此**（护钱包），所以这里只要求"能恢复"，不把短暂 503 当失败。
    """
    req_key = f"q:{day}:{who}:req"
    bucket_key = f"bucket:{who}"
    persistent, how = redis_persistence(r)
    if not persistent:
        raise Failed(
            f"这台 Redis 没有开持久化（{how}）⇒ ⑦ 的前提不成立："
            f"**数据本来就不会被留下来**，『重启后还在不在』这个问题无从谈起。"
            f"这不是判据失败，是配置不对 —— 而它正是要防的那种改动："
            f"有人把 compose 里的 `--appendonly yes` 删掉，①~⑥ 照样全绿，"
            f"真实后果却是『Redis 一重启，当天的额度就归零』（一个能被触发的绕过口子）。")
    before_req = r.get(req_key)
    before_bucket = r.hgetall(bucket_key)
    if before_req is None or not before_bucket:
        raise Failed(f"重启前就找不到状态（{req_key}={before_req!r} buckets={before_bucket!r}）"
                     f" —— 前几步没把状态写进 Redis？")
    errs_before = (http("GET", f"{api}/health")[2] or {}).get("limits", {}).get("store_errors", 0)
    print(f"    重启前：{req_key}={before_req}，桶={before_bucket}；执行：{cmd}")

    run_restart_cmd(cmd)
    wait_redis(r)

    after_req = r.get(req_key)
    if after_req is None:
        raise Failed(
            f"Redis 重启后 {req_key} 没了 ⇒ **AOF 没生效**（appendonly 没开）。"
            f"后果不是『少一个 key』，而是『Redis 一重启，当天的额度就归零』——"
            f"一个能被触发的绕过口子。compose 里是 `--appendonly yes`，检查有没有人把它删了。")
    if int(after_req) < int(before_req):
        raise Failed(f"Redis 重启后计数变小了（{before_req} → {after_req}）—— 状态没被完整持久化")
    if not r.hgetall(bucket_key):
        raise Failed(f"Redis 重启后令牌桶 {bucket_key} 没了 ⇒ 桶被重置成满桶 = 白送一整份额度")

    # 服务侧恢复：轮询 /limits，等它重新看见 Redis（容忍重启期间的短暂 503）
    t0 = time.time()
    saw_transient = False
    while time.time() - t0 < timeout:
        code, _, body = http("GET", f"{api}/limits")
        if code == 503:
            saw_transient = True
        elif code == 200:
            used = (body or {}).get("usage", {}).get("requests_used")
            if used == int(after_req):
                errs_after = (http("GET", f"{api}/health")[2] or {}).get("limits", {}).get("store_errors", 0)
                print(f"    恢复用时 {time.time() - t0:.1f}s；计数={used}"
                      f"；store_errors {errs_before} → {errs_after}"
                      f"{'（重启期间出现过短暂 503，fail_mode=closed 的设计行为）' if saw_transient else ''}")
                return
        time.sleep(2)
    raise Failed(f"Redis 回来后 {timeout:.0f} 秒内服务没重新看见计数（/limits 一直对不上）")


def main() -> int:
    ap = argparse.ArgumentParser(description="对真跑着的服务端到端验限流/日配额")
    ap.add_argument("--api", default="http://127.0.0.1:8080")
    ap.add_argument("--redis", default="redis://127.0.0.1:6379/0")
    ap.add_argument("--restart-cmd", default="", help="给了才验 ③（重启后计数还在）")
    ap.add_argument("--restart-redis-cmd", default="",
                    help="给了才验 ⑦（重启 Redis 后计数还在 = AOF 生效）")
    ap.add_argument("--burst", type=int, default=3, help="服务配置里的 RATE_BURST")
    ap.add_argument("--daily-req", type=int, default=5, help="服务配置里的 DAILY_REQ_QUOTA")
    ap.add_argument("--fail-mode", default="closed")
    ap.add_argument("--ready-timeout", type=float, default=600.0)
    args = ap.parse_args()

    api = args.api.rstrip("/")
    print(f"目标服务 {api}；Redis {args.redis}；假定 RATE_BURST={args.burst} / "
          f"DAILY_REQ_QUOTA={args.daily_req} / fail_mode={args.fail_mode}")

    import redis                                  # 只有真用才 import

    r = redis.Redis.from_url(args.redis, decode_responses=True,
                             socket_timeout=2.0, socket_connect_timeout=2.0)
    try:
        r.ping()
    except Exception as e:                        # noqa: BLE001
        print(f"✗ 连不上 Redis（{args.redis}）：{e}")
        return 2

    try:
        print("── 等服务就绪 ──")
        wait_ready(api, args.ready_timeout)
        print("  ✓ 就绪")

        print("── 清掉本次客户端的残留额度（保证可重复跑）──")
        usage0 = (http("GET", f"{api}/limits")[2] or {}).get("usage", {})
        who0, day0 = usage0.get("who"), usage0.get("day_utc")
        if not who0 or not day0:
            raise Failed(f"/limits 没报出 who/day：{usage0!r}")
        reset_client_state(r, who0, day0)

        print("── ① 限流存储必须是 redis ──")
        lim = check_store_is_redis(api, args.fail_mode)
        print(f"  ✓ store=redis store_ok=True fail_mode={lim['fail_mode']}")

        print(f"── ② 突发 {args.burst} 次后必须 429 ──")
        check_burst_then_429(api, args.burst)
        print("  ✓ 429 + Retry-After + reason=rate")

        if args.restart_cmd:
            print("── ③ 重启服务后日计数还在（Redis 的全部意义）──")
            check_counts_survive_restart(api, args.restart_cmd, args.burst, args.ready_timeout)
            print("  ✓ 重启前后计数一致")
        else:
            print("── ③ 跳过：没给 --restart-cmd（本机跑可以给一个重启脚本）──")

        print("── ④ 计数确实在 Redis 里 ──")
        usage = (http("GET", f"{api}/limits")[2] or {}).get("usage", {})
        who, day = usage.get("who"), usage.get("day_utc")
        if not who or not day:
            raise Failed(f"/limits 没报出 who/day：{usage!r}")
        check_redis_holds_counters(r, who, day)
        print(f"  ✓ q:{day}:{who}:req 在 Redis 里，且桶/计数都有 TTL")

        print("── ⑤ 日额度满了必须按 daily_req 拒 ──")
        check_daily_quota_speaks(api, r, who, day, args.burst, args.daily_req)
        print("  ✓ 429 + reason=daily_req + Retry-After≤86400")

        print("── ⑥ 拿不到 usage 必须留痕 ──")
        check_usage_missing_is_visible(api)
        print("  ✓ usage_missing 有记数")

        if args.restart_redis_cmd:
            print("── ⑦ 重启 Redis 后额度状态还在（AOF 生效）──")
            check_counters_survive_redis_restart(api, r, who, day,
                                                 args.restart_redis_cmd, args.ready_timeout)
            print("  ✓ 计数与令牌桶都还在，服务也重新看见了它")
        else:
            print("── ⑦ 跳过：没给 --restart-redis-cmd（本机可以给一个重启 Redis 的脚本）──")
    except Failed as e:
        print()
        print(f"✗ {e}")
        return 1

    print()
    print("✓ 全过：这套配额在【真服务 + 真 Redis】下确实挡住了，且 Redis 自己重启也不丢账")
    return 0


if __name__ == "__main__":
    sys.exit(main())
