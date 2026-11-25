"""从发布门禁平台（config-svc / release-svc）取**版本化的 prompt 模板**。

## 为什么 campus 要读这个

在此之前，"改 prompt"是改 `service.py` 里的字符串 —— 没有版本号、没有上线前对比、
出事只能改回去再发一版。现在模板变成 config-svc 里的一条版本记录：

- **默认**（不带请求头）：取 release-svc 的**当前生效版本**（指针），也就是"线上正在用的那版"；
- **带 `X-Config-Version: N`**：用第 N 版 —— 门禁评测候选版本时就走这条，
  这样评测的是"这次要发的那版"，而不是"线上现状"。

## 三条设计约束（都是踩坑换来的）

1. **默认路径必须逐字节不变**：不带版本、或平台不可达时，用 `service.py` 里那两段原字符串。
   `tests/test_context.py` 断言了完整 prompt 文本，改动默认措辞会立刻红。
2. **平台不可达 ⇒ 降级不报错**：问答服务不能因为"配置中心挂了"就整体不可用。
   降级时 `source="default"`，并在响应里报出来（**可见的降级**，不是静默）。
3. **缓存**：生效指针缓存 5 秒（改一版要尽快生效，但也不能每个请求都打一次）；
   版本内容按 (key, version) 缓存——版本是不可变的，缓存永不失效，但要限大小防止内存泄漏。
"""
from __future__ import annotations

import contextvars
import json
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

CONFIG_URL = os.environ.get("CONFIG_SVC_URL", "").rstrip("/")
RELEASE_URL = os.environ.get("RELEASE_SVC_URL", "").rstrip("/")
PROMPT_KEY = os.environ.get("PROMPT_KEY", "policy_qa")
ENV = os.environ.get("RELEASE_ENV", "prod")
POINTER_TTL_SEC = float(os.environ.get("CONFIG_POINTER_TTL", "5"))
TIMEOUT_SEC = float(os.environ.get("CONFIG_TIMEOUT", "1.0"))
MAX_CACHE = 64

_lock = threading.Lock()
_payload_cache: dict[tuple[str, int], dict] = {}
_pointer_cache: dict[str, tuple[float, Optional[dict]]] = {}


@dataclass
class PromptConfig:
    """一次请求要用的模板。`templates=None` ⇒ 用内置默认（逐字节不变）。"""

    templates: Optional[dict] = None
    version: Optional[int] = None
    source: str = "default"          # default / pointer / header
    note: str = ""


def enabled() -> bool:
    return bool(CONFIG_URL and RELEASE_URL)


def _get_json(url: str) -> Optional[dict]:
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as r:
            return json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None


def _payload(prompt_key: str, version: int) -> Optional[dict]:
    key = (prompt_key, version)
    with _lock:
        if key in _payload_cache:
            return _payload_cache[key]
    body = _get_json(f"{CONFIG_URL}/api/config/versions/{prompt_key}/{version}")
    payload = (body or {}).get("payload")
    if isinstance(payload, dict):
        with _lock:
            if len(_payload_cache) > MAX_CACHE:      # 简单上限：超了就清（版本不会太多）
                _payload_cache.clear()
            _payload_cache[key] = payload
    return payload if isinstance(payload, dict) else None


def active_version(prompt_key: str) -> Optional[dict]:
    """release-svc 的当前生效发布（指针）。带 5 秒缓存。"""
    now = time.time()
    with _lock:
        hit = _pointer_cache.get(prompt_key)
        if hit and now - hit[0] < POINTER_TTL_SEC:
            return hit[1]
    body = _get_json(f"{RELEASE_URL}/api/releases/active?env={ENV}&promptKey={prompt_key}")
    with _lock:
        _pointer_cache[prompt_key] = (now, body)
    return body


def resolve(header_version: Optional[str] = None, prompt_key: Optional[str] = None) -> PromptConfig:
    """决定这次请求用哪一版模板。

    ★ 优先请求头（门禁评测候选版本），其次生效指针（线上答案），都不行就内置默认。
    """
    key = prompt_key or PROMPT_KEY
    if not enabled():
        return PromptConfig(source="default", note="CONFIG_SVC_URL/RELEASE_SVC_URL 未配置")

    if header_version:
        try:
            ver = int(str(header_version).strip())
        except (TypeError, ValueError):
            return PromptConfig(source="default", note=f"X-Config-Version 不是整数: {header_version!r}")
        tpl = _payload(key, ver)
        if tpl and tpl.get("templates"):
            return PromptConfig(templates=tpl["templates"], version=ver, source="header")
        return PromptConfig(version=ver, source="default",
                            note=f"版本 {ver} 没有可用 templates，退回默认模板")

    active = active_version(key)
    if active:
        ver = active.get("version")
        tpl = _payload(key, int(ver)) if isinstance(ver, int) else None
        if tpl and tpl.get("templates"):
            return PromptConfig(templates=tpl["templates"], version=int(ver), source="pointer")
        return PromptConfig(version=int(ver) if isinstance(ver, int) else None,
                            source="default", note="生效版本没有可用 templates")
    return PromptConfig(source="default", note="平台没有生效版本（或不可达）")

#: 本次请求要用的模板（ContextVar）。为什么用 ContextVar 而不是给 answer() 加参数：
#: campus 里 rag.answer / handle_chat 的签名被十几个测试桩依赖，加参数要连带改桩，
#: 而 ContextVar 对调用方透明 —— 同一线程/任务内可见，请求结束由端点 pop 掉。
_CURRENT: contextvars.ContextVar = contextvars.ContextVar('arg_prompt_templates', default=None)


def push_templates(cfg: 'PromptConfig'):
    return _CURRENT.set(getattr(cfg, 'templates', None))


def pop_templates(token) -> None:
    try:
        _CURRENT.reset(token)
    except (ValueError, RuntimeError):
        _CURRENT.set(None)


def current_templates():
    return _CURRENT.get()
