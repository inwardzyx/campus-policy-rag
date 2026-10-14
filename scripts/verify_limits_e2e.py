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

它验八件事，缺一个都说明这套配额不能上线：

  ① /health 报的限流存储是 **redis** 且可用（不是进程内内存）
  ② 突发打满 `--burst` 次之后 ⇒ 429 + `Retry-After` + `reason=rate`
     ★ 2026-10-14 起顺带验 **`/search` 也在同一道闸后面**（桶空了它也得 429）——
       不然新端点就是个"免费入口"，而它能吃 CPU（向量 + BM25 + RRF）
  ③ **重启服务之后日计数还在**（给了 --restart-cmd 才验）—— 这是"用 Redis"的全部意义
  ④ 计数确实落在 Redis 里（按 /limits 报的 who/day 直接读，并检查 key 有 TTL）
  ⑤ 把 Redis 里的日用量顶到上限 ⇒ 必须按 `daily_req` 拒（**先把桶补满**，否则先撞速率）
  ⑥ 拿不到 usage 时必须留 `usage_missing` 的痕（不许补 0 当成"没花钱"）
  ⑦ **重启 Redis 之后额度状态还在**（给了 --restart-redis-cmd 才验）—— 这是 AOF 的全部意义
     ★ ③ 证明的是"计数不在服务内存里"，⑦ 证明的是"**Redis 自己**也不会把它丢了"。
       少了 ⑦，一个 `--appendonly no` 的 Redis 能让 ①~⑥ 全绿，
       而真实后果是"Redis 一重启，当天额度归零" —— 一个能被触发的绕过口子。
  ⑧ **只检索端点 `/search` 在部署形态下也成立**（2026-10-14 加，见
     `check_search_endpoint`）：给出处、**不烧 token**、拒答照样拒。
     ★ 这条为什么必须在**这里**而不能只在 pytest 里：pytest 打的是进程内的
       `TestClient`，而这里打的是真 uvicorn + 真 Redis；新端点最容易坏的一层
       恰恰是接线（路由注册 / 请求响应模型 / 闸门有没有接上）。

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

# 全站额度键里的那个"身份"（与 limits.GLOBAL_KEY 同值）。这里写死是为了让本脚本
# **不 import limits**（它跑在宿主机上、面对的是远端服务，不该依赖那份代码）。
# ⚠️ 两处要一起改：改 limits.GLOBAL_KEY 时这里也得改，否则 reset 清不到全站计数。
L_GLOBAL_KEY = "__all__"


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
    ★ 全站那两个键（`q:{day}:__all__:*`）也要清：它**跨身份共享**，
      同样会跨"这一次运行"累积 —— 不清的话跑得多了会撞上全站额度，
      而报出来的是"服务没钱了"，与本脚本想验的东西无关。
    """
    keys = [f"bucket:{who}", f"q:{day}:{who}:req", f"q:{day}:{who}:tok",
            f"q:{day}:{L_GLOBAL_KEY}:req", f"q:{day}:{L_GLOBAL_KEY}:tok"]
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


SEARCH_HIT_BODY = {"question": "请假一天以内由谁审批？", "top_k": 3}
SEARCH_REFUSE_BODY = {"question": "图书馆几点关门？", "top_k": 3}


def check_search_endpoint(api: str) -> None:
    """⑧ 只检索端点 `/search` 在**部署形态**下也得是它说的那样（2026-10-14 加）。

    三件事，都不需要 DEEPSEEK_API_KEY（拒答题不生成，`/search` 更是永远不生成）：
      ① 存在且能用：命中题给出处、`knowledge_hit=true`、**不启用精排**；
      ② **不烧 token**：`usage.n_calls == 0` 且 `total_tokens == 0`
         （这是"工具侧省下 6,337 token"那条结论的**服务端凭据** —— 它一旦不成立，
          README 里那套成本数字全部作废）；
      ③ 拒答照样成立：库里没有的题 `knowledge_hit=false` 且 `sources=[]`
         （"顺手把生成关掉、也顺手把拒答关掉"是这条路上最容易犯的错）。
    ⚠️ 第 ④ 件事（它在不在闸门后面）不在这里验：那必须**等桶被打空之后**才看得见，
       所以写在 `check_burst_then_429` 里。
    """
    code, _, body = http("POST", f"{api}/search", SEARCH_HIT_BODY)
    if code != 200 or not isinstance(body, dict):
        raise Failed(f"/search 没通（{code}）：{body}")
    if not (body.get("sources") or []):
        raise Failed("/search 对库里有的问题一条出处都没给 —— 端点接错了？")
    if body.get("knowledge_hit") is not True:
        raise Failed(f"/search 命中题的 knowledge_hit 不是 true：{body.get('knowledge_hit')!r}")
    if body.get("rerank_used") is not False:
        raise Failed(f"/search 不该用精排（那会再调一次模型）："
                     f"rerank_used={body.get('rerank_used')!r}")
    u = body.get("usage") or {}
    if u.get("n_calls") != 0 or int(u.get("total_tokens") or 0) != 0:
        raise Failed(f"/search 花了 token（usage={u}）—— 它应当一个 LLM 调用点都没有。"
                     f"★ 这条不成立，README 里「工具侧省下 6,337 token」就作废了")

    code, _, body = http("POST", f"{api}/search", SEARCH_REFUSE_BODY)
    if code != 200 or not isinstance(body, dict):
        raise Failed(f"/search 拒答题没通（{code}）：{body}")
    if body.get("knowledge_hit") is not False or (body.get("sources") or []):
        raise Failed(f"/search 对库里没有的问题没拒答：knowledge_hit="
                     f"{body.get('knowledge_hit')!r} sources={len(body.get('sources') or [])} 条")


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

    # ★ 2026-10-14 加：只检索端点必须在**同一道闸**后面。
    #   它照样吃 CPU（向量 + BM25 + RRF），不设闸就是开了个免费入口；
    #   而"新入口忘了接闸门"正是 R4 那次的病（新入口自己抄一遍、然后漏一处）。
    code, _, body = http("POST", f"{api}/search", SEARCH_HIT_BODY)
    if code != 429:
        raise Failed(f"桶打空之后 /search 还是 {code} —— 新端点没接闸门（免费入口）：{body}")
    if (body or {}).get("detail", {}).get("reason") != "rate":
        raise Failed(f"/search 的 429 原因应当是 rate，实际 {body}")


def boot_id(api: str) -> str:
    """拿服务本次进程的 boot_id（`/health` 报的）。

    ★ 为什么需要它（盲审 F1，我自己写的假绿）：③⑦ 的声称是"**重启之后** X 还在"，
      而第一版只检查"重启命令的退出码是 0"。实测 `--restart-cmd "true"`（什么也没重启）
      ⇒ 七条断言**全绿**：计数当然没变，因为压根没重启。
      ⇒ 判据必须能区分"重启过"与"命令返回 0"：boot_id 只在进程启动时生成一次，
        重启后必须对不上；对得上就说明**这次重启没发生**，后面的结论一概不算。
    """
    code, _, body = http("GET", f"{api}/health")
    if code != 200 or not isinstance(body, dict):
        raise Failed(f"/health 取不到（{code}）")
    got = body.get("boot_id")
    if not got:
        raise Failed("服务没有报 boot_id ⇒ 它是旧版本的进程（没重启到新代码？）")
    return str(got)


def redis_run_id(r) -> str:
    """Redis 本次运行的 run_id（`INFO server` 里那个）。同一个用途：证明重启真的发生了。"""
    info = r.info("server")
    rid = info.get("run_id")
    if not rid:
        raise Failed("Redis 没报 run_id ⇒ 没法证明它真的重启过")
    return str(rid)


def check_counts_survive_restart(api: str, restart_cmd: str, burst: int, timeout: float) -> None:
    """★ 这一段是"用 Redis"的全部意义：进程重启后额度还得在。"""
    before = (http("GET", f"{api}/limits")[2] or {}).get("usage", {}).get("requests_used")
    if before is None or before < burst:
        raise Failed(f"重启前 requests_used={before!r}，至少应当是 {burst}（突发那几次没记账？）")
    boot_before = boot_id(api)
    print(f"    重启前 requests_used={before} boot_id={boot_before}；执行：{restart_cmd}")
    run_restart_cmd(restart_cmd)
    wait_ready(api, timeout, what="重启后就绪")
    boot_after = boot_id(api)
    if boot_after == boot_before:
        raise Failed(
            f"重启命令跑完了，但服务的 boot_id 没变（还是 {boot_before}）"
            f"⇒ **这次重启没有真的发生**（比如命令是个 no-op、或重启的是别的容器）。"
            f"这种情况下『重启后计数还在』是废话，不算证据。命令：{restart_cmd}")
    after = (http("GET", f"{api}/limits")[2] or {}).get("usage", {}).get("requests_used")
    if after != before:
        raise Failed(f"重启后计数变了（{before} → {after}）⇒ 配额其实存在进程内存里，不是 Redis")
    print(f"    重启后 boot_id={boot_after}（变了 ⇒ 确实重启过），计数仍为 {after}")


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
    # ★ 带 ex（盲审 F10）：第一版 `r.set(...)` 不带过期 ⇒ 脚本自己往 Redis 里丢了一个
    #   **永不过期**的 key。判据顺手制造垃圾，是很讽刺的一件事。
    r.set(f"q:{day}:{who}:req", 999, ex=3600)
    code, headers, body = http("POST", f"{api}/chat", REFUSE_BODY)
    if code != 429:
        raise Failed(
            f"Redis 里的日用量已写成 999，服务却没拦（返回 {code}）—— "
            f"要么它读的不是 Redis，要么 DAILY_REQ_QUOTA=0（额度被关掉了，期望 {daily_req}）")
    reason = (body or {}).get("detail", {}).get("reason")
    if reason != "daily_req":
        raise Failed(f"该按日额度拒（daily_req），实际 reason={reason!r}：{body}")
    ra = headers.get("Retry-After") or headers.get("retry-after")
    # ★ 断言"到 UTC 零点"要真的算一遍（盲审 F6）：第一版只查 `1 <= ra <= 86400` ——
    #   把实现改成返回常数 3600，那条断言**照样通过**，而"什么时候恢复"就变成假的。
    expect = 86_400 - (time.time() % 86_400)
    if not ra or abs(int(ra) - expect) > 10:
        raise Failed(f"日额度的 Retry-After 应当是『到 UTC 零点』的秒数"
                     f"（此刻约 {expect:.0f}），实际 {ra!r}")


def check_usage_missing_is_visible(api: str, r, who: str, day: str, burst: int) -> None:
    """⑥ 拿不到 usage 时，**这一次**必须留下痕 —— 用增量判，不看历史累计值。

    ★★ 盲审 F3 打的就是这里的第一版：它断言 `usage_missing >= 1`。
      可 `usage_missing` 是 Redis 里一个**全局且无 TTL 的累计指标**，
      而脚本开头的 reset 只清 `who` 那三个键、不清指标 ——
      于是只要**历史上**有过一次，这条断言就永远绿。
      cc 做过 A/B：把被测行为改成"None 不留痕"（`limits.py` 里 `_bump_local` 那行换成 `pass`），
      ⑥ 仍然 ✓；只有先把 `metric:usage_missing` 删掉，它才变红。**那是一条假判据。**
      ⇒ 现在读基线 → 触发一次"必然拿不到 usage"的请求 → 要求 **+≥1**。
    """
    def metric() -> int:
        v = r.get("metric:usage_missing")
        return int(v) if v is not None else 0

    # 保证这次请求能过闸门（桶在前面几步已经被打空）
    r.hset(f"bucket:{who}", mapping={"tokens": burst, "ts": 1})
    m0 = metric()
    code, _, body = http("POST", f"{api}/chat", REFUSE_BODY)
    if code != 200:
        raise Failed(f"为验 ⑥ 发的这次请求被闸门挡了（{code}）：{body}")
    m1 = metric()
    if m1 <= m0:
        raise Failed(
            f"这次请求必然拿不到 usage（拒答路径不调模型），但 usage_missing 没涨"
            f"（{m0} → {m1}）⇒ 缺口被静默抹平了"
            f"（本项目已记过同类：把『没拿到』记成 0，看起来像『真的没花钱』）")
    print(f"    usage_missing {m0} → {m1}（增量口径；历史累计值不作数）")


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
    run_before = redis_run_id(r)                 # ★ 证明"重启真的发生了"（盲审 F1）
    print(f"    重启前：{req_key}={before_req}，桶={before_bucket}，"
          f"Redis run_id={run_before[:8]}；执行：{cmd}")

    run_restart_cmd(cmd)
    wait_redis(r)
    run_after = redis_run_id(r)
    if run_after == run_before:
        raise Failed(
            f"重启命令跑完了，但 Redis 的 run_id 没变（还是 {run_before[:8]}）"
            f"⇒ **Redis 并没有真的重启**，那么『重启后额度还在』是废话，不算证据。"
            f"命令：{cmd}")

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

    ran: list[str] = []          # ★ 只记"真的跑过并通过"的项（盲审 F2：跳过的绝不许被算进结论）
    skipped: list[str] = []
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

        # ★ ⑧ 必须排在 ② **前面**（2026-10-14 踩到）：② 会把桶打空，
        #   而 ⑧ 要验的是"这条路给出处、不烧 token、拒答也在"，请求必须先被放行。
        #   ⚠️ 但 ⑧ 自己会用掉两枚令牌 ⇒ 跑完**把它借的那两枚还回去**（同一个 reset），
        #   否则紧接着的 ②「突发 --burst 次」就不成立了（第一版就这么红了一次）。
        print("── ⑧ 只检索端点 /search：给出处、不烧 token、拒答也在 ──")
        check_search_endpoint(api)
        reset_client_state(r, who0, day0)          # ★ 还令牌，见上面的注释
        ran.append("⑧")
        print("  ✓ /search 通了：命中题给出处、usage.n_calls=0、拒答题 knowledge_hit=false"
              "（已把这枚令牌还回去）")

        print("── ① 限流存储必须是 redis ──")
        lim = check_store_is_redis(api, args.fail_mode)
        ran.append("①")
        print(f"  ✓ store=redis store_ok=True fail_mode={lim['fail_mode']}")

        print(f"── ② 突发 {args.burst} 次后必须 429 ──")
        check_burst_then_429(api, args.burst)
        ran.append("②")
        print("  ✓ 429 + Retry-After + reason=rate（顺带验了 /search 也在同一道闸后面）")

        if args.restart_cmd:
            print("── ③ 重启服务后日计数还在（Redis 的全部意义）──")
            check_counts_survive_restart(api, args.restart_cmd, args.burst, args.ready_timeout)
            ran.append("③")
            print("  ✓ 确实重启过，且重启前后计数一致")
        else:
            skipped.append("③（没给 --restart-cmd）")
            print("── ③ 跳过：没给 --restart-cmd（本机跑可以给一个重启脚本）──")

        print("── ④ 计数确实在 Redis 里 ──")
        usage = (http("GET", f"{api}/limits")[2] or {}).get("usage", {})
        who, day = usage.get("who"), usage.get("day_utc")
        if not who or not day:
            raise Failed(f"/limits 没报出 who/day：{usage!r}")
        check_redis_holds_counters(r, who, day)
        ran.append("④")
        print(f"  ✓ q:{day}:{who}:req 在 Redis 里，且桶/计数都有 TTL")

        # ★ ⑥ 必须排在 ⑤ 前面：⑤ 会把日用量写成 999，之后任何请求都会被 429 挡在
        #   闸门外面、进不到管道，而 ⑥ 需要的正是"一次真的走完管道、且拿不到 usage 的请求"。
        print("── ⑥ 拿不到 usage 必须留痕（增量口径）──")
        check_usage_missing_is_visible(api, r, who, day, args.burst)
        ran.append("⑥")
        print("  ✓ 这一次请求让 usage_missing 涨了")

        print("── ⑤ 日额度满了必须按 daily_req 拒 ──")
        check_daily_quota_speaks(api, r, who, day, args.burst, args.daily_req)
        ran.append("⑤")
        print("  ✓ 429 + reason=daily_req + Retry-After≤86400")

        if args.restart_redis_cmd:
            print("── ⑦ 重启 Redis 后额度状态还在（AOF 生效）──")
            check_counters_survive_redis_restart(api, r, who, day,
                                                 args.restart_redis_cmd, args.ready_timeout)
            ran.append("⑦")
            print("  ✓ Redis 确实重启过，计数与令牌桶都还在，服务也重新看见了它")
        else:
            skipped.append("⑦（没给 --restart-redis-cmd）")
            print("── ⑦ 跳过：没给 --restart-redis-cmd（本机可以给一个重启 Redis 的脚本）──")
    except Failed as e:
        print()
        print(f"✗ {e}")
        return 1

    # ★★ 收尾只说**真跑过**的（盲审 F2）：第一版无条件宣称"全过……且 Redis 自己重启也不丢账"，
    #    而 ③⑦ 被跳过时那句话是假的 —— 跳过 ≠ 通过，两者绝不能在输出上长得一样。
    print()
    if skipped:
        print(f"✓ 已跑的 {len(ran)} 项全过（{'、'.join(ran)}）")
        print(f"⚠ 未验的 {len(skipped)} 项：{'、'.join(skipped)}")
        print("  ⇒ 这两项要的是『重启之后还在』，没有重启命令就等于**没测** —— "
              "本脚本不替它们下结论。")
        return 0
    print(f"✓ 八项全过：这套配额在【真服务 + 真 Redis】下挡住了，"
          f"且服务重启、Redis 重启都不丢账（两项都验过『重启真的发生了』）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
