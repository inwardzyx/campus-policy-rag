# -*- coding: utf-8 -*-
"""auth.py —— 把 SSO 身份注入成 ACL 角色（不信任客户端自填的 acl）

支持两种企业常见形态：

1. `AUTH_MODE=proxy`（推荐，生产）
   由网关/oauth2-proxy 做 OIDC 登录，向下游注入 `X-Auth-Roles: hr,finance`。
   应用只在 `TRUST_PROXY_HEADERS=1` 时信任这个头 —— 否则任何人都能伪造角色。

2. `AUTH_MODE=jwt`（本地 demo / 无网关）
   应用自己验 `Authorization: Bearer <HS256 JWT>`，从 `roles` claim 取角色。
   ★ 只实现了 HS256（stdlib）；生产用 RS256/OIDC 时，请让网关验签后注入角色，
     或在部署环境装 PyJWT[crypto] 并替换 verify 函数。

`AUTH_MODE=none`（默认）保持旧行为：不校验身份，acl 仍可由请求体显式传（仅 demo）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Mapping


class AuthError(Exception):
    def __init__(self, detail: str, status: int = 401) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status = status


@dataclass(frozen=True)
class Identity:
    roles: list[str]
    subject: str | None = None


@dataclass(frozen=True)
class AuthConfig:
    mode: str = "none"              # none | proxy | jwt
    jwt_secret: str = ""
    issuer: str = ""
    audience: str = ""
    roles_claim: str = "roles"
    proxy_header: str = "X-Auth-Roles"
    trust_proxy: bool = False
    leeway: int = 30

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "AuthConfig":
        mode = (env.get("AUTH_MODE") or "none").strip().lower()
        if mode not in ("none", "proxy", "jwt"):
            mode = "none"            # 拼错时退回 none，但启动日志会警告（见 service.py）
        return cls(
            mode=mode,
            jwt_secret=env.get("AUTH_JWT_SECRET", ""),
            issuer=env.get("AUTH_ISSUER", ""),
            audience=env.get("AUTH_AUDIENCE", ""),
            roles_claim=env.get("AUTH_ROLES_CLAIM", "roles"),
            proxy_header=env.get("AUTH_PROXY_HEADER", "X-Auth-Roles"),
            trust_proxy=(env.get("TRUST_PROXY_HEADERS", "0") == "1"),
            leeway=int(env.get("AUTH_LEEWAY", "30") or 30),
        )


def _b64url_decode(text: str) -> bytes:
    text += "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text.encode("ascii"))


def _header(headers: Mapping[str, str], name: str) -> str:
    return headers.get(name) or headers.get(name.lower()) or ""


def verify_hs256(token: str, secret: str, *, issuer: str = "", audience: str = "",
                 leeway: int = 30) -> dict:
    """验 HS256 JWT，返回 claims；失败抛 AuthError。"""
    if not secret:
        raise AuthError("AUTH_MODE=jwt 但没配 AUTH_JWT_SECRET", 503)
    parts = token.split(".")
    if len(parts) != 3:
        raise AuthError("JWT 格式不对（应为 header.payload.signature）")
    try:
        header = json.loads(_b64url_decode(parts[0]))
        claims = json.loads(_b64url_decode(parts[1]))
        sig = _b64url_decode(parts[2])
    except Exception as exc:
        raise AuthError(f"JWT 解码失败：{exc}") from exc
    if header.get("alg") != "HS256":
        raise AuthError("只支持 HS256（其它算法请由网关验签）")
    expected = hmac.new(secret.encode("utf-8"),
                        f"{parts[0]}.{parts[1]}".encode("ascii"),
                        hashlib.sha256).digest()
    if not hmac.compare_digest(expected, sig):
        raise AuthError("JWT 签名不对")

    now = time.time()
    exp = claims.get("exp")
    if exp is not None and now > float(exp) + leeway:
        raise AuthError("JWT 已过期")
    nbf = claims.get("nbf")
    if nbf is not None and now + leeway < float(nbf):
        raise AuthError("JWT 尚未生效")
    if issuer and claims.get("iss") != issuer:
        raise AuthError("JWT issuer 不匹配")
    if audience:
        aud = claims.get("aud")
        if isinstance(aud, list):
            if audience not in aud:
                raise AuthError("JWT audience 不匹配")
        elif aud != audience:
            raise AuthError("JWT audience 不匹配")
    return claims


def roles_from_claims(claims: dict, claim_name: str = "roles") -> list[str]:
    """roles claim 支持 list 或字符串（逗号/空格分隔）。"""
    raw = claims.get(claim_name)
    if raw is None:
        return []
    if isinstance(raw, str):
        parts = raw.replace(",", " ").split()
    elif isinstance(raw, (list, tuple, set)):
        parts = [str(x) for x in raw]
    else:
        parts = [str(raw)]
    return sorted({p.strip() for p in parts if p.strip()})


def authenticate(headers: Mapping[str, str], config: AuthConfig) -> Identity | None:
    """返回 Identity；mode=none 返回 None；认证失败抛 AuthError。"""
    if config.mode == "none":
        return None

    if config.mode == "proxy":
        if not config.trust_proxy:
            raise AuthError("AUTH_MODE=proxy 但 TRUST_PROXY_HEADERS!=1（拒绝信任可伪造的头）", 503)
        raw = _header(headers, config.proxy_header)
        if not raw:
            raise AuthError("缺少网关注入的角色头")
        roles = sorted({x.strip() for x in raw.replace(",", " ").split() if x.strip()})
        return Identity(roles=roles, subject=_header(headers, "X-Auth-Subject") or None)

    if config.mode == "jwt":
        auth = _header(headers, "Authorization")
        if not auth.startswith("Bearer "):
            raise AuthError("缺少 Bearer token")
        claims = verify_hs256(auth[7:].strip(), config.jwt_secret,
                              issuer=config.issuer, audience=config.audience,
                              leeway=config.leeway)
        return Identity(roles=roles_from_claims(claims, config.roles_claim),
                        subject=claims.get("sub"))

    raise AuthError("未知 AUTH_MODE", 503)
