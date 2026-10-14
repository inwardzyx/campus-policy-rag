# -*- coding: utf-8 -*-
"""会话历史存储 —— 只服务于"多轮追问"，**不是**日志，也不是分析用的数据。

================================================================================
★★★ 这一层为什么必须单独说清它"存了什么、存多久、谁读得到"（2026-10-14）
================================================================================
本项目原来有一条边界：「**不记录用户问题原文**」（写在 `HANDOFF.md` 与缓存实验产物里，
理由是问题文本属个人信息）。加多轮之后，Redis 里就会躺着问题原文 —— 那条边界**必须重新划**，
而不是留矛盾给面试官发现。重新划之后的准确口径是：

    **会话级临时存储：只存最近 N 轮的"问题原文"，TTL 30 分钟，
      仅用于服务本次对话；不进日志、不入持久化库、不做行为分析。**

★ 为什么"不记录原文"这个说法本来就不精确：任何服务都必须持有问题原文才能回答它
  （至少在内存里、在请求生命周期内）。真正能承诺、也能被验证的是
  **"不留存"**：不写日志、不落库、过期即消失。
⇒ 所以这里的 `history:` 键是**临时态**，不是"用户提问记录"。

★ 另一个必须如实写下的边界：**Redis 的 AOF**。
  `docker-compose.yml` 里 Redis 带 `--appendonly yes`（为了验"重启后额度还在"），
  所以 `history:` 会被写进容器卷里的 AOF 文件，**由 Redis 的 TTL 到期清除**。
  这条不能装作没有 —— 部署时若要更严，就给会话换一个独立实例/db 并关掉 AOF。

★ 一个还没做的、已经想清的点（**别当成遗漏**）：
  会话里的问法是"真实 query 分布"的第一笔数据（本项目此前登记过"结构上为零"）。
  要用它，必须**显式征得同意**（申请邀请码时明写、默认不存）。这次**没做** ——
  因为"存会话以支持多轮"和"留下来做分析"是两件事，混在一起会让前者也说不清。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Protocol

logger = logging.getLogger("session")

# 三条口径都在这里，改的时候一起看
DEFAULT_MAX_TURNS = 5          # 只留最近 5 轮（再多对"指代"没用，却多存一份原文）
DEFAULT_TTL_SEC = 1800         # 30 分钟：够一次连续对话，又不至于变成"留存"
KEY_PREFIX = "history:"        # ★ 键空间与限流分家（limits.py 用的是 bucket:/q:）


class SessionStore(Protocol):
    """会话历史存储。**刻意不塞进 `limits.py` 的 `Store` Protocol**。

    ★ 为什么分家（而不是"反正都是 Redis，合一个"）：
      两者的**生命周期与清理语义完全不同**：
        · 限流：计数类，按 UTC 日界滚，过期是"清账"
        · 会话：内容类，按最后活动时间续期，过期是"用户离开了"
      塞进一个 Protocol 会让两边互相妥协（比如被迫共用 TTL 参数），
      而"耦合一个以后要分开的东西"正是本项目踩过的那类坑。
    """

    name: str

    def append_turn(self, session_id: str, question: str, max_turns: int,
                    ttl_sec: int) -> bool:
        """把这一轮的**问题原文**追加进会话。返回 False = 没存上（= 降级）。"""
        ...

    def get_questions(self, session_id: str) -> list[str]:
        """取回最近的问题（**不含本轮**，调用方在写完之前调）。失败返回空列表。"""
        ...


class InProcessSessionStore:
    """没配 Redis 时的退回实现。★ 只对单进程有效（多 worker 各存各的）。

    ⚠️ 与限流那边同一条已知边界：uvicorn 多 worker / 多副本下每个进程一份，
       会话会"时有时无"。所以 `name` 会被报出去，让调用方知道自己在哪种模式。
    """

    name = "memory"

    def __init__(self) -> None:
        self._d: dict[str, tuple[list[str], float]] = {}
        self._lock = threading.Lock()

    def _live(self, session_id: str, now: float) -> list[str]:
        item = self._d.get(session_id)
        if not item:
            return []
        qs, expire_at = item
        if now >= expire_at:
            self._d.pop(session_id, None)      # 过期即删（不留残影）
            return []
        return qs

    def append_turn(self, session_id: str, question: str, max_turns: int,
                    ttl_sec: int) -> bool:
        now = time.time()
        with self._lock:
            qs = self._live(session_id, now)
            qs = (qs + [question])[-max_turns:]
            self._d[session_id] = (qs, now + ttl_sec)
            # 顺手清理彻底过期的键，免得长期跑下来堆一堆死会话
            for k in [k for k, (_, e) in self._d.items() if now >= e]:
                self._d.pop(k, None)
        return True

    def get_questions(self, session_id: str) -> list[str]:
        with self._lock:
            return list(self._live(session_id, time.time()))


class RedisSessionStore:
    """Redis 实现（懒连接，与 `limits.RedisStore` 同一套连接参数）。

    ★ 三条硬约束，都是照抄限流那边踩出来的经验：
      ① **socket_timeout 必须有** —— Redis 半死时默认无限等，所有请求一起挂住；
      ② 关掉维护通知探测（redis-py 8.x 默认会试 Redis 8 的子命令，在 7.x/Memurai 上刷警告，
         而"常开的警告等于没有警告"）；
      ③ **每个方法都要先 `_c()`** —— 限流那边吃过"只有第一次坏"的 bug
         （第一次 self._client 还是 None）。
    """

    name = "redis"

    def __init__(self, url: str) -> None:
        self.url = url
        self._client: Any = None

    def _c(self):
        if self._client is None:
            import redis

            from redis.maint_notifications import MaintNotificationsConfig

            self._client = redis.Redis.from_url(
                self.url, decode_responses=True,
                socket_timeout=1.0, socket_connect_timeout=1.0,
                maint_notifications_config=MaintNotificationsConfig(enabled=False))
        return self._client

    def append_turn(self, session_id: str, question: str, max_turns: int,
                    ttl_sec: int) -> bool:
        """LPUSH + LTRIM + EXPIRE 一次往返做完（用 pipeline，别三次往返）。

        ★ `EXPIRE` **每次都设**：只在第一次设的话，正好那次失败了这个键就永不过期
          （限流那边 `incr_daily` 的注释记过同一件事）。
        """
        try:
            c = self._c()
            key = KEY_PREFIX + session_id
            pipe = c.pipeline()
            pipe.lpush(key, question)
            pipe.ltrim(key, 0, max_turns - 1)
            pipe.expire(key, ttl_sec)
            pipe.execute()
            return True
        except Exception as e:                       # noqa: BLE001
            # ★ 存不上 = 降级成"没有历史"，**不抛** —— 绝不能因为会话存储挂了就答不了问题
            logger.warning("会话写入失败（%s），本轮按无历史继续", type(e).__name__)
            return False

    def get_questions(self, session_id: str) -> list[str]:
        try:
            c = self._c()
            vals = c.lrange(KEY_PREFIX + session_id, 0, -1)
            # LPUSH 是最新在前 ⇒ 反转成时间顺序，模型读起来自然
            return list(reversed([v for v in vals if v]))
        except Exception as e:                       # noqa: BLE001
            logger.warning("会话读取失败（%s），本轮按无历史继续", type(e).__name__)
            return []


def build_session_store(env: dict, log: logging.Logger | None = None) -> SessionStore:
    """按 `REDIS_URL` 选实现 —— 与限流共用同一个环境变量，但**各自建自己的客户端**。

    ★ 为什么不复用 `LIMITER.store` 那个实例：它是 `limits.Store`，
      没有会话那套方法；为了复用而给那边加方法，就是把两件事重新粘一起。
    """
    url = (env.get("REDIS_URL") or "").strip()
    if url:
        return RedisSessionStore(url)
    if log:
        log.warning("会话历史用的是【进程内存储】⇒ 只对单进程有效；"
                    "uvicorn 多 worker / 多副本下会话会时有时无。要共享请配 REDIS_URL。")
    return InProcessSessionStore()
