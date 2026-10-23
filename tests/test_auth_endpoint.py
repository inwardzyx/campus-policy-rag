# -*- coding: utf-8 -*-
"""SSO → /search 的端到端接线测试（不加载模型）。"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

import service as svc
from auth import AuthConfig
from test_auth import make_jwt


@pytest.fixture()
def jwt_client(monkeypatch):
    seen = {}

    def fake_search(q, k=5, namespace=None, acl=None):
        seen["acl"] = acl
        return [({"doc": "制度", "clause": "第三条", "text": "内容", "acl": acl or ["public"]}, 0.9)]

    monkeypatch.setattr(svc.rag, "ready", True)
    monkeypatch.setattr(svc.rag, "search", fake_search)
    monkeypatch.setattr(svc, "AUTH_CONFIG", AuthConfig(mode="jwt", jwt_secret="s3cret"))
    return TestClient(svc.app), seen


def test_missing_token_is_401(jwt_client):
    client, _ = jwt_client
    assert client.post("/search", json={"question": "报销时限"}).status_code == 401
    assert client.post("/chat", json={"question": "报销时限"}).status_code == 401
    assert client.post("/chat/stream", json={"question": "报销时限"}).status_code == 401


def test_token_roles_override_body_acl(jwt_client):
    client, seen = jwt_client
    token = make_jwt({"sub": "u1", "roles": ["hr"], "exp": time.time() + 60})
    r = client.post("/search", json={"question": "报销时限", "acl": ["finance"]},
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    assert seen["acl"] == ["hr"]          # SSO 角色覆盖客户端自填 acl
