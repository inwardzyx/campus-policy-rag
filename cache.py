# -*- coding: utf-8 -*-
"""进程内 LRU 缓存（给"改写"和"精排"用）。

## 为什么只缓这两处
一次 `/chat` 有 **3 次 LLM 调用**（改写 → 精排 → 生成）。压测显示并发 8 时吞吐不再上升、
p95 翻倍（`experiments/load-test.json`）——瓶颈在调用次数，不在 CPU。
其中**改写**和**精排**是"同输入 ⇒ 同输出"的纯函数（temperature=0），可以安全缓存；
**生成**不能缓：同一问题的答案文本不该被跨请求复用（用户要看到的是针对本轮上下文的回答）。

## 正确性边界
- key 必须包含**所有影响输出的输入**（问题、历史问题、候选块的指纹、事件时点）；
- 命中的值只用于**检索与排序**，不跨请求复用最终答案；
- 文档增删后清空（`clear()`），避免"旧候选的分数"污染新语料；
- 线程安全：FastAPI 的同步端点跑在线程池里，必须加锁。

## 默认关闭
`CACHE_ENABLED=1` 才启用。先量、再决定默认 —— 与 `INDEX_TEXT_MODE` 同一套纪律。
"""
from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from typing import Any, Callable

DEFAULT_MAX_ITEMS = 512


def key_of(*parts: Any) -> str:
    """稳定 key：把参与计算的输入拼起来取 sha256（不存原文，避免内存里留用户问题）。"""
    h = hashlib.sha256()
    for part in parts:
        h.update(repr(part).encode("utf-8"))
        h.update(b"\x1f")
    return h.hexdigest()


class LRUCache:
    def __init__(self, max_items: int = DEFAULT_MAX_ITEMS) -> None:
        self.max_items = max(1, int(max_items))
        self._data: OrderedDict[str, Any] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Any | None:
        with self._lock:
            if key not in self._data:
                self.misses += 1
                return None
            self._data.move_to_end(key)
            self.hits += 1
            return self._data[key]

    def put(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self.max_items:
                self._data.popitem(last=False)

    def get_or_compute(self, key: str, factory: Callable[[], Any]) -> Any:
        hit = self.get(key)
        if hit is not None:
            return hit
        value = factory()
        self.put(key, value)
        return value

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def stats(self) -> dict:
        total = self.hits + self.misses
        return {"items": len(self._data), "max_items": self.max_items,
                "hits": self.hits, "misses": self.misses,
                "hit_rate": round(self.hits / total, 4) if total else None}
