# -*- coding: utf-8 -*-
"""租户注册表（S2-1）。

## 边界（先写清楚，免得被当成"完整多租户"）
这一版解决的是**身份 + 命名空间隔离 + 可审计**：
- 每个租户有自己的 API key（只存 sha256，不存明文）与允许的 namespace 列表；
- `/chat`、`/search` 会校验"这个租户能不能用这个 namespace"；
- 审计事件带 `tenant`，管理 API 能按租户导出。

**还没做**（写在这里，别当已完成）：每租户独立配额、写接口（上传/删除）的鉴权、
计费、控制台。这些在 HANDOFF 的 S2-2/S2-3 里。

## 为什么默认关闭
`MULTI_TENANT=1` 才开启校验；否则一切走 `default` 租户（= 现有单租户行为），
保证已有部署、测试、脚本一行不改就能跑。默认值必须向后兼容，否则"加个功能"会变成"全挂"。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

DEFAULT_ID = "default"


def hash_key(key: str) -> str:
    return hashlib.sha256(str(key).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Tenant:
    id: str
    name: str
    #: None = 不受限（默认租户/单租户模式）；空元组 = 一个都不许
    namespaces: tuple[str, ...] | None = None
    enabled: bool = True
    created_at: str = ""
    keys: tuple[str, ...] = ()               # 只存 sha256

    def allows(self, namespace: str | None) -> bool:
        if self.namespaces is None:
            return True
        ns = str(namespace or "")
        return any(ns == n or ns.startswith(n.rstrip("/") + "/") for n in self.namespaces)

    def public(self) -> dict[str, Any]:
        """对外展示用：**不含** key 哈希。"""
        return {"id": self.id, "name": self.name,
                "namespaces": list(self.namespaces) if self.namespaces is not None else None,
                "enabled": self.enabled, "created_at": self.created_at,
                "n_keys": len(self.keys)}


class Registry:
    def __init__(self, path: Path, *, enabled: bool = False):
        self.path = Path(path)
        self.enabled = enabled
        self._tenants: dict[str, Tenant] = {}

    # ---------- 载入/保存 ----------
    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Registry":
        path = Path((env.get("TENANTS_PATH") or "kb_data/tenants.json"))
        enabled = (env.get("MULTI_TENANT") or "").strip().lower() in ("1", "true", "yes", "on")
        reg = cls(path, enabled=enabled)
        reg.load()
        return reg

    def load(self) -> None:
        if not self.path.is_file():
            return
        data = json.loads(self.path.read_text(encoding="utf-8"))
        for item in data.get("tenants") or []:
            ns = item.get("namespaces")
            t = Tenant(id=str(item["id"]), name=str(item.get("name") or item["id"]),
                       namespaces=None if ns is None else tuple(ns),
                       enabled=bool(item.get("enabled", True)),
                       created_at=str(item.get("created_at") or ""),
                       keys=tuple(item.get("keys") or []))
            self._tenants[t.id] = t

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"tenants": [
            {"id": t.id, "name": t.name,
             "namespaces": None if t.namespaces is None else list(t.namespaces),
             "enabled": t.enabled, "created_at": t.created_at, "keys": list(t.keys)}
            for t in self._tenants.values()]}
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8", newline="\n")

    # ---------- 查询 ----------
    def default(self) -> Tenant:
        return Tenant(id=DEFAULT_ID, name="默认租户（单租户模式）")

    def get(self, tenant_id: str) -> Tenant | None:
        return self._tenants.get(str(tenant_id))

    def list(self) -> list[Tenant]:
        return list(self._tenants.values())

    def resolve_key(self, plain_key: str) -> Tenant | None:
        """按明文 key 找租户（比对哈希；用 compare_digest 避免时序泄露）。"""
        if not plain_key:
            return None
        target = hash_key(plain_key)
        for tenant in self._tenants.values():
            if not tenant.enabled:
                continue
            for stored in tenant.keys:
                if hmac.compare_digest(stored, target):
                    return tenant
        return None

    # ---------- 创建 ----------
    def create(self, tenant_id: str, *, name: str = "",
               namespaces: list[str] | None = None) -> tuple[Tenant, str]:
        """建租户并返回明文 key（只返回这一次，之后只存哈希）。"""
        tid = str(tenant_id).strip()
        if not tid:
            raise ValueError("tenant id 不能为空")
        if tid in self._tenants:
            raise ValueError(f"租户 {tid!r} 已存在")
        plain = "tk_" + secrets.token_urlsafe(24)
        tenant = Tenant(id=tid, name=name or tid,
                        namespaces=None if namespaces is None else tuple(namespaces),
                        created_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                        keys=(hash_key(plain),))
        self._tenants[tid] = tenant
        self.save()
        return tenant, plain
