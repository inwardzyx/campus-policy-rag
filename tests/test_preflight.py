# -*- coding: utf-8 -*-
"""私有化部署自检（`scripts/preflight.py`）的判据。

## 盯的四条
  ① 没配 `DEEPSEEK_API_KEY` ⇒ **fail**（服务能起、/chat 全 500，这是部署现场最常见的坑）；
  ② `kb_data` 不可写 ⇒ fail（语料/审计/租户表全落这里）；
  ③ 开了多租户却没租户表 ⇒ fail；没配 admin token ⇒ warn（容器外打不开控制台）；
  ④ 退出码：有 fail = 1；`--strict` 时 warn 也算 1（否则"检查通过"是假的）。

★ 变异体 `scripts/mutate_preflight.py`：把 API key 检查从清单里删掉 / 让 fail 也返回 0。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import preflight                                  # noqa: E402


def _env(**kw):
    base = {"DEEPSEEK_API_KEY": "sk-" + "a" * 30, "AUDIT_ENABLED": "1"}
    base.update(kw)
    return base


def test_missing_key_is_a_failure(tmp_path) -> None:
    results = preflight.run(_env(DEEPSEEK_API_KEY=""), tmp_path)
    key = next(r for r in results if r.name == "deepseek_key")
    assert key.status == "fail"
    assert "500" in key.detail


def test_placeholder_key_warns(tmp_path) -> None:
    results = preflight.run(_env(DEEPSEEK_API_KEY="cli_xxx"), tmp_path)
    assert next(r for r in results if r.name == "deepseek_key").status == "warn"


def test_kb_data_unwritable_fails(tmp_path) -> None:
    blocker = tmp_path / "kb_data"
    blocker.write_text("我是文件，不是目录", encoding="utf-8")   # 让 mkdir 失败
    results = preflight.run(_env(KB_DATA_DIR=str(blocker)), tmp_path)
    assert next(r for r in results if r.name == "kb_data_writable").status == "fail"


def test_multi_tenant_without_registry_fails(tmp_path) -> None:
    env = _env(MULTI_TENANT="1", TENANTS_PATH=str(tmp_path / "缺失.json"))
    results = preflight.run(env, tmp_path)
    assert next(r for r in results if r.name == "tenancy").status == "fail"


def test_multi_tenant_with_registry_but_no_admin_token_warns(tmp_path) -> None:
    (tmp_path / "tenants.json").write_text('{"tenants": []}', encoding="utf-8")
    env = _env(MULTI_TENANT="1", TENANTS_PATH=str(tmp_path / "tenants.json"),
               KB_ADMIN_TOKEN="")
    assert next(r for r in preflight.run(env, tmp_path) if r.name == "tenancy").status == "warn"


def test_cli_exit_codes(tmp_path) -> None:
    """真实 CLI：fail → 1；全 ok 且非 strict → 0；全 ok 但 --strict 有 warn → 1。"""
    def run_cli(env, *extra):
        return subprocess.run([sys.executable, str(ROOT / "scripts" / "preflight.py"), *extra],
                              cwd=str(ROOT), capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              env={**os.environ, **env, "PYTHONIOENCODING": "utf-8"})

    ok_env = {"DEEPSEEK_API_KEY": "sk-" + "b" * 30, "AUDIT_ENABLED": "1",
              "MULTI_TENANT": "0", "MODEL_CACHE_DIR": str(tmp_path / "cache")}
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / "model.bin").write_text("x", encoding="utf-8")

    r_ok = run_cli(ok_env)
    assert r_ok.returncode == 0, r_ok.stdout + r_ok.stderr

    r_fail = run_cli({**ok_env, "DEEPSEEK_API_KEY": ""})
    assert r_fail.returncode == 1
    assert "deepseek_key" in r_fail.stdout

    r_strict = run_cli({**ok_env, "AUDIT_ENABLED": "0"}, "--strict")
    assert r_strict.returncode == 1, "strict 模式下 warn 必须让退出码非 0"


def test_json_output_is_parseable() -> None:
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "preflight.py"), "--json"],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    payload = json.loads(r.stdout)
    assert set(payload) == {"results", "fails", "warns"}
    assert all({"name", "status", "detail"} <= set(x) for x in payload["results"])
