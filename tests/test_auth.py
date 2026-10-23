# -*- coding: utf-8 -*-
"""SSO 角色注入测试：JWT 验签/过期/aud、代理头信任、mode=none 兼容。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import pytest

from auth import AuthConfig, AuthError, authenticate, roles_from_claims


def _b64(obj) -> str:
    raw = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def make_jwt(claims: dict, secret: str = "s3cret") -> str:
    header = _b64({"alg": "HS256", "typ": "JWT"})
    payload = _b64(claims)
    sig = hmac.new(secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
    return f"{header}.{payload}." + base64.urlsafe_b64encode(sig).decode().rstrip("=")


def test_jwt_roles_are_injected():
    token = make_jwt({"sub": "u1", "roles": ["hr", "finance"], "exp": time.time() + 60})
    cfg = AuthConfig(mode="jwt", jwt_secret="s3cret")
    ident = authenticate({"Authorization": f"Bearer {token}"}, cfg)
    assert ident is not None
    assert ident.roles == ["finance", "hr"]
    assert ident.subject == "u1"


def test_jwt_rejects_bad_signature_and_expiry():
    token = make_jwt({"roles": ["hr"], "exp": time.time() + 60}, secret="other")
    with pytest.raises(AuthError):
        authenticate({"Authorization": f"Bearer {token}"},
                     AuthConfig(mode="jwt", jwt_secret="s3cret"))

    expired = make_jwt({"roles": ["hr"], "exp": time.time() - 100})
    with pytest.raises(AuthError):
        authenticate({"Authorization": f"Bearer {expired}"},
                     AuthConfig(mode="jwt", jwt_secret="s3cret", leeway=0))


def test_jwt_checks_issuer_and_audience():
    token = make_jwt({"roles": ["hr"], "iss": "idp", "aud": "kb", "exp": time.time() + 60})
    cfg = AuthConfig(mode="jwt", jwt_secret="s3cret", issuer="idp", audience="kb")
    assert authenticate({"Authorization": f"Bearer {token}"}, cfg).roles == ["hr"]
    with pytest.raises(AuthError):
        authenticate({"Authorization": f"Bearer {token}"},
                     AuthConfig(mode="jwt", jwt_secret="s3cret", issuer="other"))


def test_proxy_requires_explicit_trust():
    cfg = AuthConfig(mode="proxy", trust_proxy=False)
    with pytest.raises(AuthError) as exc:
        authenticate({"X-Auth-Roles": "hr"}, cfg)
    assert exc.value.status == 503

    cfg = AuthConfig(mode="proxy", trust_proxy=True)
    ident = authenticate({"X-Auth-Roles": "hr, finance", "X-Auth-Subject": "u1"}, cfg)
    assert ident.roles == ["finance", "hr"]


def test_none_mode_keeps_old_behavior():
    assert authenticate({"X-Auth-Roles": "hr"}, AuthConfig(mode="none")) is None


def test_roles_claim_accepts_string_and_list():
    assert roles_from_claims({"roles": "hr, finance"}) == ["finance", "hr"]
    assert roles_from_claims({"groups": ["a", "b"]}, "groups") == ["a", "b"]
    assert roles_from_claims({}) == []
