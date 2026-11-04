# -*- coding: utf-8 -*-
"""`/internal/*` 的判据：给 Java 业务层用的三个入口。

## 为什么单独一组测试（而不是并进 test_kb_api）

这一组引入了**一个新的边界**，它有三条必须被钉死的性质，任何一条破了都不会立刻报错：

1. **fail-closed**：没配 `INTERNAL_TOKEN` 时必须 503，不能"没配就放行"——
   否则一次部署遗漏 = 公网裸奔。
2. **跳过的是「闸」，不是「账」**：`/internal/*` 不限流（Java 是唯一上游，
   按来源 IP 认人会把所有终端用户挤进同一个桶），但 `LIMITER.record()` 必须照旧执行，
   否则 `/limits` 与 `/metrics` 就看不见这批真实流量 —— 那是把"可证明的 0"写反。
3. **不新造鉴权语义**：角色仍然来自 `_request_roles()`（`X-Auth-Roles` / JWT）。
   下一个人顺手加一个 `X-ACL` 就会变成两套并行语义，所以这里直接断言"角色来自哪个头"。

★ 第 2 条配了**反向用例**（`test_public_chat_still_hits_the_gate`）：
   只证明"内部不过闸"是不够的 —— 把 `skip_limit` 写成对全局生效，那条用例会红。
   只有一对一起绿，"这个开关是有范围的"才算被证明。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import service as svc
from auth import AuthConfig
from kb.store import KnowledgeStore


class _LimitRecorder:
    """替身限流器：内部接口**不该**调 `check()`，一调就让测试失败。"""

    def __init__(self):
        self.checks = 0
        self.records = 0
        self.limits = svc.LIMITER.limits

    def check(self, who):                       # pragma: no cover - 走到这里就是错
        self.checks += 1
        raise AssertionError("内部接口不应调用限流闸（配额归 Java 侧的 Redis）")

    def record(self, who, usage):
        self.records += 1


class _CountingLimiter(_LimitRecorder):
    """公开接口的替身：`check()` 必须被调用，且放行。"""

    class _OK:
        allowed = True

    def check(self, who):
        self.checks += 1
        return self._OK()


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("INTERNAL_TOKEN", "t0ken")
    # 内部接口要求"角色必须有来源"，所以默认给 proxy 模式（见 _internal_guard 的注释）。
    monkeypatch.setattr(svc, "AUTH_CONFIG", AuthConfig(mode="proxy", trust_proxy=True))
    return TestClient(svc.app)


@pytest.fixture()
def audit_calls(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(svc.audit_mod, "record_response",
                        lambda req, payload, **kw: calls.append(kw))
    return calls


@pytest.fixture()
def kb_store(tmp_path, monkeypatch):
    store = KnowledgeStore(tmp_path)
    monkeypatch.setattr(svc.rag, "store", store)
    monkeypatch.setattr(svc.rag, "ready", True)
    monkeypatch.setattr(svc.rag, "reload_enterprise", lambda: None)
    monkeypatch.setattr(svc.rag, "chunks", [])
    return store


def _fake_chat(seen: dict | None = None):
    def _f(req, led, roles=None):
        if seen is not None:
            seen["roles"] = roles
        return svc.ChatResponse(answer="ok", sources=[], took_ms=1,
                                rerank_used=False, knowledge_hit=False)
    return _f


# ---------------------------------------------------------------- 1. fail-closed
def test_unconfigured_internal_token_is_503_not_bypass(monkeypatch):
    """没配 INTERNAL_TOKEN ⇒ 503。**不能**因为是"自己人"就放行。"""
    monkeypatch.delenv("INTERNAL_TOKEN", raising=False)
    r = TestClient(svc.app).post("/internal/chat", json={"question": "q"},
                                 headers={"X-Internal-Token": "anything"})
    assert r.status_code == 503, r.text
    assert "INTERNAL_TOKEN" in r.text


def test_wrong_token_is_401(client):
    r = client.post("/internal/chat", json={"question": "q"},
                    headers={"X-Internal-Token": "nope"})
    assert r.status_code == 401, r.text


def test_auth_mode_none_is_refused_even_with_valid_token(client, monkeypatch):
    """`AUTH_MODE=none` + 正确 token 也必须 **503**。

    ★ 这条挡的是盲审 P-2 那个洞：none 模式下没有角色来源，`handle_chat` 会退回去读
      **请求体里的 acl** ⇒ 经由 Java 暴露到公网就是"任何人 `acl:["hr"]` 拿全量"。
      token 对只证明"是 Java 在调"，不证明"用户被认证过" —— 两件事不能混。
    """
    monkeypatch.setattr(svc, "AUTH_CONFIG", AuthConfig(mode="none"))
    r = client.post("/internal/chat", json={"question": "q", "acl": ["hr"]},
                    headers={"X-Internal-Token": "t0ken"})
    assert r.status_code == 503, r.text
    assert "AUTH_MODE" in r.text


# ------------------------------------------------- 2. 过闸 vs 记账（一对一反向用例）
def test_internal_chat_skips_the_gate_but_still_records(client, audit_calls, monkeypatch):
    lim = _LimitRecorder()
    monkeypatch.setattr(svc, "LIMITER", lim)
    monkeypatch.setattr(svc, "handle_chat", _fake_chat())

    r = client.post("/internal/chat", json={"question": "q"},
                    headers={"X-Internal-Token": "t0ken", "X-Auth-Roles": "public"})
    assert r.status_code == 200, r.text
    assert lim.checks == 0, "内部接口不该过限流闸"
    assert lim.records == 1, "跳过的是闸，不是账 —— /limits 必须看得见这批流量"
    assert audit_calls and audit_calls[0]["route"] == "/internal/chat"


def test_public_chat_still_hits_the_gate(client, audit_calls, monkeypatch):
    """反向用例：`skip_limit` 必须**只**对内部入口生效。

    把开关写成全局生效时，这条会红 —— 只有这一对一起绿，"开关有范围"才算被证明。
    """
    lim = _CountingLimiter()
    monkeypatch.setattr(svc, "LIMITER", lim)
    monkeypatch.setattr(svc, "handle_chat", _fake_chat())

    r = client.post("/chat", json={"question": "q"},
                    headers={"X-Auth-Roles": "public"})
    assert r.status_code == 200, r.text
    assert lim.checks == 1, "公开 /chat 必须照旧过闸"


# ------------------------------------------------------------ 3. 角色仍走 v1 的通道
def test_roles_come_from_the_existing_proxy_header(client, monkeypatch):
    """角色来自 `X-Auth-Roles`（v1 已有），不是新造的 `X-ACL`。"""
    monkeypatch.setattr(svc, "AUTH_CONFIG", AuthConfig(mode="proxy", trust_proxy=True))
    monkeypatch.setattr(svc, "LIMITER", _LimitRecorder())
    seen: dict = {}
    monkeypatch.setattr(svc, "handle_chat", _fake_chat(seen))

    r = client.post("/internal/chat", json={"question": "q"},
                    headers={"X-Internal-Token": "t0ken", "X-Auth-Roles": "hr,public"})
    assert r.status_code == 200, r.text
    assert seen["roles"] == ["hr", "public"], "角色必须原样透传到 handle_chat"


# ------------------------------------------------------------------ 4. 内部上传/删除
def test_internal_upload_and_delete(client, kb_store):
    md = "---\ndoc: FAQ\nversion: 2026-01-01\n---\n## Q1\n七天无理由退货。\n"

    r = client.post("/internal/kb/demo/documents?filename=faq.md",
                    content=md.encode("utf-8"),
                    headers={"X-Internal-Token": "nope"})
    assert r.status_code == 401, "上传同样要过内部闸"

    r = client.post("/internal/kb/demo/documents?filename=faq.md&acl=hr",
                    content=md.encode("utf-8"),
                    headers={"X-Internal-Token": "t0ken"})
    assert r.status_code == 200, r.text
    doc_id = r.json()["document"]["doc_id"]

    r = client.delete(f"/internal/kb/demo/documents/{doc_id}",
                      headers={"X-Internal-Token": "t0ken"})
    assert r.status_code == 200 and r.json()["deleted"] is True

    r = client.delete(f"/internal/kb/demo/documents/{doc_id}",
                      headers={"X-Internal-Token": "nope"})
    assert r.status_code == 401


def test_internal_upload_reuses_the_public_error_mapping(client, kb_store):
    """与公开端点共用 `_kb_store_upload` ⇒ 错误码不许漂移（415/422 是同一套）。"""
    r = client.post("/internal/kb/demo/documents?filename=x.exe", content=b"MZ",
                    headers={"X-Internal-Token": "t0ken"})
    assert r.status_code == 415
    r = client.post("/internal/kb/demo/documents?filename=x.docx", content=b"not a zip",
                    headers={"X-Internal-Token": "t0ken"})
    assert r.status_code == 422
