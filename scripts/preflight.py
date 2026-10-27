#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""preflight.py —— 私有化部署前的自检（**能在坏情况下报红**）。

## 为什么需要它
私有化部署最常见的失败不是"起不来"，而是"**起来了但每条 /chat 都 500**"：
- 没配 `DEEPSEEK_API_KEY` ⇒ 走生成的那一路全炸（模型懒加载，启动时看不出来）；
- `kb_data` 不可写 ⇒ 上传/审计/租户表静默失败；
- 多租户开了但没有租户表 / 没有 admin token ⇒ 面板打不开、或谁都能进；
- 审计开着但路径不可写 ⇒ "合规留痕"变成空头承诺。

这些都能在 `docker compose up` **之前**用 3 秒查出来，而不用等用户来报错。

## 用法
```powershell
D:\\Python-project\\.venv\\Scripts\\python.exe scripts/preflight.py            # 人读
... scripts/preflight.py --json                                               # 机器读
... scripts/preflight.py --strict                                             # 有 warn 也返回非 0
```
退出码：有 fail = 1；`--strict` 时 w​arn 也算 1；全 ok = 0。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Callable, Mapping

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Result:
    name: str
    status: str          # ok | warn | fail
    detail: str


def _writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".preflight-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def check_api_key(env: Mapping[str, str], root: Path) -> Result:
    key = (env.get("DEEPSEEK_API_KEY") or "").strip()
    if not key:
        return Result("deepseek_key", "fail",
                      "没有 DEEPSEEK_API_KEY：服务能起来，但 /chat 会全 500（模型懒加载，启动时不报）")
    if key.lower() in ("cli_xxx", "xxx", "sk-xxx") or len(key) < 20:
        return Result("deepseek_key", "warn", "key 看起来像占位符，确认一下是不是抄错了")
    return Result("deepseek_key", "ok", "已配置")


def check_kb_data(env: Mapping[str, str], root: Path) -> Result:
    """语料/审计/租户表都落在 kb_data：不可写等于"问了就丢"。"""
    path = Path(env.get("KB_DATA_DIR") or (root / "kb_data"))
    ok = _writable(path)
    return Result("kb_data_writable", "ok" if ok else "fail",
                  f"{path} " + ("可写" if ok else "不可写：上传/审计/租户表都会失败"))


def check_audit(env: Mapping[str, str], root: Path) -> Result:
    enabled = (env.get("AUDIT_ENABLED") or "1").strip().lower() not in ("0", "false", "off", "no")
    if not enabled:
        return Result("audit", "warn", "AUDIT_ENABLED=0：没有留痕，合规场景不要这么部署")
    path = Path(env.get("AUDIT_LOG_PATH") or (root / "kb_data" / "audit.jsonl"))
    ok = _writable(path.parent)
    return Result("audit", "ok" if ok else "fail",
                  f"{path} " + ("目录可写" if ok else "目录不可写：审计写不进去"))


def check_tenancy(env: Mapping[str, str], root: Path) -> Result:
    multi = (env.get("MULTI_TENANT") or "").strip().lower() in ("1", "true", "yes", "on")
    if not multi:
        return Result("tenancy", "ok", "单租户模式（MULTI_TENANT 未开）")
    tenants = Path(env.get("TENANTS_PATH") or (root / "kb_data" / "tenants.json"))
    if not tenants.is_file():
        return Result("tenancy", "fail",
                      f"开了多租户但租户表不存在：{tenants}（先建租户，否则所有 /chat 401）")
    token = (env.get("KB_ADMIN_TOKEN") or "").strip()
    if not token:
        return Result("tenancy", "warn",
                      "没配 KB_ADMIN_TOKEN：/admin/* 只允许本机回环，容器外面打不开控制台")
    return Result("tenancy", "ok", f"多租户已开，租户表 {tenants.name} 存在")


def check_tracing(env: Mapping[str, str], root: Path) -> Result:
    """追踪会把 prompt 发到第三方：开着就要让人知道。

    ★ 为什么算 warn 而不是 ok：它不影响服务能不能用，但影响"数据出不出本机"。
    """
    keys = ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2")
    on = any((env.get(k) or "").strip().lower() in ("1", "true", "yes", "on") for k in keys)
    if not on:
        return Result("tracing", "ok", "追踪关闭（prompt 不出本机）")
    if not (env.get("LANGCHAIN_API_KEY") or "").strip():
        return Result("tracing", "warn",
                      "追踪开着但没有 LANGCHAIN_API_KEY：服务会强制关掉它（否则 401 重试 + 数据外发）")
    return Result("tracing", "warn",
                  "追踪已开：prompt/答案会发往 api.smith.langchain.com（第三方），确认这是你要的")


def check_model_cache(env: Mapping[str, str], root: Path) -> Result:
    """embedding 模型首次要下载（约 91MB）：没缓存且没网就会卡在 loading。"""
    cache = Path(env.get("MODEL_CACHE_DIR") or (Path.home() / ".cache" / "fastembed"))
    if cache.is_dir() and any(cache.iterdir()):
        return Result("model_cache", "ok", f"{cache} 已有缓存")
    return Result("model_cache", "warn",
                  f"{cache} 为空：首次启动要下载 embedding 模型，离线部署请先预热")


CHECKS: list[Callable[[Mapping[str, str], Path], Result]] = [
    check_api_key,
    check_kb_data,
    check_audit,
    check_tenancy,
    check_model_cache,
    check_tracing,
]


def run(env: Mapping[str, str] | None = None, root: Path | None = None) -> list[Result]:
    env = dict(os.environ) if env is None else env
    root = Path(root or ROOT)
    return [check(env, root) for check in CHECKS]


def load_env_file(path: Path) -> dict[str, str]:
    """读 `.env` 但不覆盖已有环境变量（环境变量优先，和 service.py 的约定一致）。

    ★ 为什么必须读：本地跑自检时 key 通常只写在 `.env` 里；不读就会报"没有 key"，
      而这条假红会教人**忽略自检**——那比没有自检更坏。
    """
    if not path.is_file():
        return {}
    try:
        from dotenv import dotenv_values
    except ImportError:
        return {}
    return {k: v for k, v in dotenv_values(path).items() if v is not None}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="私有化部署前自检")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true", help="warn 也算失败")
    ap.add_argument("--env-file", default=str(ROOT / ".env"),
                    help="从哪个 .env 读缺省值（环境变量仍优先；容器里通常不需要）")
    args = ap.parse_args(argv)

    env = {**load_env_file(Path(args.env_file)), **os.environ}
    results = run(env)
    fails = [r for r in results if r.status == "fail"]
    warns = [r for r in results if r.status == "warn"]
    if args.json:
        print(json.dumps({"results": [asdict(r) for r in results],
                          "fails": len(fails), "warns": len(warns)}, ensure_ascii=False, indent=2))
    else:
        mark = {"ok": "✓", "warn": "!", "fail": "✗"}
        for r in results:
            print(f"{mark[r.status]} {r.name}: {r.detail}")
        print(f"\n{len(results)} 项检查：{len(results) - len(fails) - len(warns)} ok / "
              f"{len(warns)} warn / {len(fails)} fail")
    if fails:
        return 1
    return 1 if (args.strict and warns) else 0


if __name__ == "__main__":
    raise SystemExit(main())
