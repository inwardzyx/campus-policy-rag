# -*- coding: utf-8 -*-
"""多租户 S2-1：身份 → 命名空间隔离 → 审计可导出。

## 盯的四条
  ① 注册表：建租户只返回**一次**明文 key，落盘只存 sha256；重载后仍能按 key 找到租户；
  ② 隔离：`MULTI_TENANT=1` 时无 key 401、key 与 namespace 不匹配 403、匹配 200；
  ③ 审计：事件带 `tenant`，管理 API 能按租户过滤；
  ④ 管理接口**不泄露** key 哈希。

★ 默认关闭（`MULTI_TENANT` 不设）时一切走 default 租户 —— 既有部署行为不变。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest                                   # noqa: E402
from fastapi.testclient import TestClient       # noqa: E402

import service as svc                           # noqa: E402
import tenants as tenants_mod                   # noqa: E402
from kb import audit as audit_mod               # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def make_registry(tmp_path, *, enabled: bool):
    reg = tenants_mod.Registry(tmp_path / "tenants.json", enabled=enabled)
    tenant, key = reg.create("hr", name="人力资源部", namespaces=["hr", "hr/policy"])
    return reg, tenant, key


# ---------------------------------------------------------------- ① 注册表

def test_create_returns_key_once_and_stores_only_hash(tmp_path) -> None:
    reg, tenant, key = make_registry(tmp_path, enabled=True)
    raw = (tmp_path / "tenants.json").read_text(encoding="utf-8")
    assert key not in raw, "明文 key 不许落盘"
    assert tenant.keys and all(len(k) == 64 for k in tenant.keys)
    json.loads(raw)                              # 可解析


def test_reload_keeps_key_resolvable(tmp_path) -> None:
    reg, _, key = make_registry(tmp_path, enabled=True)
    again = tenants_mod.Registry(tmp_path / "tenants.json", enabled=True)
    again.load()
    assert again.resolve_key(key) is not None
    assert again.resolve_key("tk_wrong") is None


def test_duplicate_tenant_rejected(tmp_path) -> None:
    reg, _, _ = make_registry(tmp_path, enabled=True)
    with pytest.raises(ValueError):
        reg.create("hr")


def test_allows_matches_prefix_but_not_lookalike(tmp_path) -> None:
    _, tenant, _ = make_registry(tmp_path, enabled=True)
    assert tenant.allows("hr") is True
    assert tenant.allows("hr/policy") is True
    assert tenant.allows("hr/policy/x") is True, "子前缀应该允许"
    assert tenant.allows("hrx") is False, "只是字符串前缀相同不算"
    assert tenant.allows(None) is False


def test_default_tenant_is_unrestricted(tmp_path) -> None:
    reg = tenants_mod.Registry(tmp_path / "t.json", enabled=False)
    assert reg.default().allows("任意命名空间") is True


# ---------------------------------------------------------------- ①/② 服务层

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(svc.rag, "ready", True)
    monkeypatch.setattr(svc.rag, "rewrite_query", lambda q, prior_questions=None: q)
    monkeypatch.setattr(svc.rag, "search",
                        lambda q, k=5, namespace=None, acl=None: [
                            ({"doc": "d.md", "clause": "第 1 条",
                              "text": "正文" * 10, "text_full": "正文" * 10,
                              "acl": ["public"]}, 0.9)])
    monkeypatch.setattr(svc.rag, "rerank",
                        lambda q, cands: [(c, s, 9, "假精排") for c, s in cands])
    monkeypatch.setattr(svc.rag, "answer", lambda q, chunks: "答案[1]。")
    return TestClient(svc.app)


def _ask(client, namespace, key=None):
    headers = {"x-api-key": key} if key else {}
    return client.post("/chat", json={"question": "问题", "namespace": namespace},
                       headers=headers)


def test_multi_tenant_requires_key(client, tmp_path, monkeypatch) -> None:
    reg, _, key = make_registry(tmp_path, enabled=True)
    monkeypatch.setattr(svc, "TENANTS", reg)
    assert _ask(client, "hr").status_code == 401
    assert _ask(client, "hr", key="tk_bad").status_code == 401
    assert _ask(client, "hr", key=key).status_code == 200


def test_tenant_cannot_reach_foreign_namespace(client, tmp_path, monkeypatch) -> None:
    reg, _, key = make_registry(tmp_path, enabled=True)
    monkeypatch.setattr(svc, "TENANTS", reg)
    assert _ask(client, "finance", key=key).status_code == 403
    assert _ask(client, "hr/policy", key=key).status_code == 200


def test_single_tenant_mode_is_backward_compatible(client, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(svc, "TENANTS", tenants_mod.Registry(tmp_path / "none.json",
                                                             enabled=False))
    assert _ask(client, "任意空间").status_code == 200


# ---------------------------------------------------------------- ③ 审计

def test_audit_event_carries_tenant() -> None:
    req = svc.ChatRequest(question="问题", namespace="hr")
    event = audit_mod.build_event(req, {"knowledge_hit": True, "sources": [],
                                        "answer": "答"}, route="/chat", tenant="hr")
    assert event["tenant"] == "hr"
    assert "问题" not in json.dumps(event, ensure_ascii=False), "审计不该有原文"


# ---------------------------------------------------------------- ④ 管理 API

def test_admin_tenants_hides_key_hashes(client, tmp_path, monkeypatch) -> None:
    reg, tenant, key = make_registry(tmp_path, enabled=True)
    monkeypatch.setattr(svc, "TENANTS", reg)
    body = client.get("/admin/tenants").json()
    assert body["multi_tenant"] is True
    assert body["tenants"][0]["id"] == "hr"
    assert tenant.keys[0] not in json.dumps(body), "对外展示不许带 key 哈希"
    assert "keys" not in body["tenants"][0]


def test_admin_audit_filters_by_tenant(client, tmp_path, monkeypatch) -> None:
    log = tmp_path / "audit.jsonl"
    monkeypatch.setenv("AUDIT_LOG_PATH", str(log))
    lines = [
        {"tenant": "hr", "session_id": "s1", "decision": "answer"},
        {"tenant": "finance", "session_id": "s2", "decision": "refuse"},
        {"tenant": "hr", "session_id": "s2", "decision": "refuse"},
    ]
    log.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    monkeypatch.setattr(svc, "TENANTS", tenants_mod.Registry(tmp_path / "t.json",
                                                             enabled=False))

    body = client.get("/admin/audit", params={"tenant": "hr"}).json()
    assert body["n"] == 2 and all(r["tenant"] == "hr" for r in body["rows"])

    body2 = client.get("/admin/audit", params={"session_id": "s2"}).json()
    assert {r["tenant"] for r in body2["rows"]} == {"finance", "hr"}

# ---------------------------------------------------------------- S2-2 配额与写鉴权

def test_per_tenant_quota_does_not_leak_across_tenants(client, tmp_path, monkeypatch) -> None:
    """A 租户打满自己的配额，不能把 B 拖下水（否则只是"多个 namespace"）。"""
    import limits as limits_mod

    reg, _hr, key_hr = make_registry(tmp_path, enabled=True)
    _fin, key_fin = reg.create("finance", namespaces=["finance"])
    monkeypatch.setattr(svc, "TENANTS", reg)
    monkeypatch.setattr(svc, "LIMITER", limits_mod.build_limiter({
        "DAILY_REQ_QUOTA": "1", "DAILY_TOKEN_QUOTA": "1000000",
        "RATE_BURST": "100", "RATE_PER_MIN": "600",
    }))

    assert _ask(client, "hr", key_hr).status_code == 200
    assert _ask(client, "hr", key_hr).status_code == 429, "第二个请求应打满"

    # B 租户的额度是独立的
    assert _ask(client, "finance", key_fin).status_code == 200


def _upload(client, namespace, key=None, text="## 第 1 条\n正文。"):
    headers = {"x-api-key": key} if key else {}
    return client.post(f"/kb/{namespace}/documents",
                       params={"filename": "a.md", "acl": "public"},
                       content=text.encode("utf-8"), headers=headers)


def test_tenant_can_write_only_own_namespace(client, tmp_path, monkeypatch) -> None:
    from kb.store import KnowledgeStore

    reg, _hr, key_hr = make_registry(tmp_path, enabled=True)
    monkeypatch.setattr(svc, "TENANTS", reg)
    monkeypatch.setattr(svc.rag, "store", KnowledgeStore(tmp_path / "kb"))
    monkeypatch.setattr(svc.rag, "reload_enterprise", lambda: None)

    assert _upload(client, "hr", key_hr).status_code == 200
    assert _upload(client, "finance", key_hr).status_code == 403
    assert _upload(client, "hr").status_code == 401, "多租户模式没有 key 不许写"


def test_single_tenant_mode_keeps_loopback_write(client, tmp_path, monkeypatch) -> None:
    from kb.store import KnowledgeStore

    monkeypatch.setattr(svc, "TENANTS", tenants_mod.Registry(tmp_path / "none.json",
                                                             enabled=False))
    monkeypatch.setattr(svc.rag, "store", KnowledgeStore(tmp_path / "kb2"))
    monkeypatch.setattr(svc.rag, "reload_enterprise", lambda: None)
    assert _upload(client, "demo").status_code == 200, "单租户模式行为必须不变"

# ---------------------------------------------------------------- 开通租户 CLI

def _run_tenant_cli(tmp_path, *args):
    import subprocess

    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "create_tenant.py"),
         "--path", str(tmp_path / "tenants.json"), *args],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"})


def test_create_tenant_cli_prints_key_once(tmp_path) -> None:
    r = _run_tenant_cli(tmp_path, "--id", "hr", "--name", "人力资源部",
                        "--namespaces", "hr,hr/policy")
    assert r.returncode == 0, r.stdout + r.stderr
    keys = [ln for ln in r.stdout.splitlines() if ln.startswith("KEY=tk_")]
    assert len(keys) == 1, "明文 key 只能打印一次"

    raw = (tmp_path / "tenants.json").read_text(encoding="utf-8")
    assert keys[0][4:] not in raw, "明文 key 不许落盘"
    assert "hr" in raw


def test_create_tenant_cli_rejects_duplicate(tmp_path) -> None:
    assert _run_tenant_cli(tmp_path, "--id", "hr", "--namespaces", "hr").returncode == 0
    again = _run_tenant_cli(tmp_path, "--id", "hr", "--namespaces", "hr")
    assert again.returncode == 1
    assert "已存在" in again.stdout

# ---------------------------------------------------------------- 隧道/反代暴露

def test_admin_guard_blocks_tunnel_when_no_token(client, monkeypatch) -> None:
    """没配 KB_ADMIN_TOKEN 时，直连本机可以；**经过隧道/反代就不行**。

    ★ 为什么必须守：cloudflared 把请求转发到 127.0.0.1，服务端看到的来源就是回环，
      只按 host 判断等于公网无闸门（/admin/* 与上传/删除会全开）。
    """
    monkeypatch.delenv("KB_ADMIN_TOKEN", raising=False)
    assert client.get("/admin/tenants").status_code == 200, "直连本机应放行"
    assert client.get("/admin/tenants",
                      headers={"cf-connecting-ip": "1.2.3.4"}).status_code == 403
    assert client.get("/admin/tenants",
                      headers={"x-forwarded-for": "1.2.3.4"}).status_code == 403

