# -*- coding: utf-8 -*-
"""连接器与增量同步（S1）的守门用例。

## 盯的四条
  ① **增量真的增量**：第二次同步 `unchanged=N`、`added=0`；改内容只 `updated` 那一篇；
  ② **权限变更下一次就生效**：改 manifest 的 acl → 同步后 chunk 的 acl 变了，
     且**不重建索引**也能被 ACL 过滤挡住（`_acl_allows` 用的是 chunk 上的 acl）；
  ③ **删除只删自己拥有的**：同一 namespace 里人工上传的文档不能被连接器一轮同步清掉；
  ④ **报错可操作**：飞书的 99991672 必须被翻译成"发版本 + 加入知识空间"的清单，
     而不是丢一个错误码。

★ 变异体 `scripts/mutate_connectors.py`：忽略 ACL 变化 / 清单删掉"发版本"那步，两条都必须红。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest                                   # noqa: E402

import service as svc                           # noqa: E402
from kb.connectors.core import sync_connector    # noqa: E402
from kb.connectors.fixture import FixtureConnector  # noqa: E402
from kb.feishu import FeishuError, explain_error    # noqa: E402
from kb.store import KnowledgeStore              # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def make_fixture(root: Path, docs: dict[str, dict]) -> Path:
    """造一个外部世界：manifest.json + docs/<key>.md。"""
    (root / "docs").mkdir(parents=True, exist_ok=True)
    entries = []
    for key, spec in docs.items():
        (root / "docs" / f"{key}.md").write_text(spec["text"], encoding="utf-8")
        entries.append({"key": key, "title": spec.get("title", key),
                        "acl": spec.get("acl", ["public"]),
                        "version": spec.get("version", "1"),
                        "effective_date": spec.get("effective_date", "")})
    (root / "manifest.json").write_text(json.dumps({"docs": entries}, ensure_ascii=False),
                                        encoding="utf-8")
    return root


def test_first_sync_adds_then_second_is_unchanged(tmp_path) -> None:
    fixture = make_fixture(tmp_path / "ext", {"a": {"text": "## 第三条\n三十日内提交。"},
                                              "b": {"text": "## 第四条\n不得外传。"}})
    store = KnowledgeStore(tmp_path / "kb")
    conn = FixtureConnector(fixture)

    first = sync_connector(conn, store, namespace="demo")
    assert (first.added, first.updated, first.unchanged) == (2, 0, 0)

    second = sync_connector(FixtureConnector(fixture), store, namespace="demo")
    assert (second.added, second.updated, second.unchanged) == (0, 0, 2)


def test_content_change_updates_only_that_doc(tmp_path) -> None:
    fixture = make_fixture(tmp_path / "ext", {"a": {"text": "## 第三条\n三十日内。"},
                                              "b": {"text": "## 第四条\n不得外传。"}})
    store = KnowledgeStore(tmp_path / "kb")
    sync_connector(FixtureConnector(fixture), store, namespace="demo")

    (fixture / "docs" / "a.md").write_text("## 第三条\n改为六十日内。", encoding="utf-8")
    rep = sync_connector(FixtureConnector(fixture), store, namespace="demo")
    assert (rep.updated, rep.unchanged) == (1, 1)
    assert any("六十日" in c["text"] for c in store.load_chunks("demo"))


def test_acl_change_takes_effect_without_reindex(tmp_path) -> None:
    """★ S1 的核心判据：改权限 ⇒ 下一次检索就被挡（靠 chunk 上的 acl，不靠重建索引）。"""
    fixture = make_fixture(tmp_path / "ext", {"a": {"text": "## 第三条\n薪酬保密。",
                                                    "acl": ["public"]}})
    store = KnowledgeStore(tmp_path / "kb")
    sync_connector(FixtureConnector(fixture), store, namespace="demo")
    assert store.load_chunks("demo")[0]["acl"] == ["public"]

    make_fixture(fixture, {"a": {"text": "## 第三条\n薪酬保密。", "acl": ["hr"]}})
    rep = sync_connector(FixtureConnector(fixture), store, namespace="demo")
    assert rep.updated == 1 and "acl" in rep.changed_reasons[0]

    chunk = store.load_chunks("demo")[0]
    assert chunk["acl"] == ["hr"]
    # 检索层的 ACL 判据（与线上同一份实现）：没有 hr 角色就看不到
    assert svc._acl_allows(chunk["acl"], None) is False
    assert svc._acl_allows(chunk["acl"], ["hr"]) is True


def test_delete_missing_only_removes_its_own_docs(tmp_path) -> None:
    fixture = make_fixture(tmp_path / "ext", {"a": {"text": "## 第三条\n三十日内。"}})
    store = KnowledgeStore(tmp_path / "kb")
    # 同一 namespace 里还有一份人工上传的语料：连接器不许把它删掉
    store.upload("demo", "人工制度.md", "## 第一条\n人工上传。".encode("utf-8"),
                 source="upload:人工制度.md")
    sync_connector(FixtureConnector(fixture), store, namespace="demo")
    assert len(store.load_chunks("demo")) == 2

    (fixture / "manifest.json").write_text(json.dumps({"docs": []}), encoding="utf-8")
    rep = sync_connector(FixtureConnector(fixture), store, namespace="demo")
    assert rep.deleted == 1
    docs = {c["doc"] for c in store.load_chunks("demo")}
    assert docs == {"人工制度"}, f"连接器删了它不拥有的文档：{docs}"


def test_dry_run_writes_nothing(tmp_path) -> None:
    fixture = make_fixture(tmp_path / "ext", {"a": {"text": "## 第三条\n三十日内。"}})
    store = KnowledgeStore(tmp_path / "kb")
    rep = sync_connector(FixtureConnector(fixture), store, namespace="demo", dry_run=True)
    assert rep.added == 1 and rep.dry_run is True
    assert store.load_chunks("demo") == [], "dry-run 竟然写了库"


# ---------------------------------------------------------------- ④ 报错可操作

def test_permission_error_explains_next_steps() -> None:
    text = explain_error(FeishuError(99991672,
                                     "Access denied. [wiki:wiki, docx:document:readonly]"))
    assert "docx:document:readonly" in text and "wiki:wiki" in text
    assert "发版" in text, "最常见的坑（加了权限不发版）必须写在报错旁边"
    assert "知识空间" in text, "scope ≠ 空间成员，这条也必须写"


def test_missing_token_error_mentions_credentials() -> None:
    assert "App ID" in explain_error(FeishuError("no_token", "响应里没有 tenant_access_token"))


# ---------------------------------------------------------------- CLI

def test_cli_fixture_sync(tmp_path) -> None:
    fixture = make_fixture(tmp_path / "ext", {"a": {"text": "## 第三条\n三十日内。"}})
    root = tmp_path / "kb"
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "connector_sync.py"),
                        "--connector", "fixture", "--source", str(fixture),
                        "--namespace", "demo", "--root", str(root)],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0, r.stdout + r.stderr
    payload = json.loads(r.stdout)
    assert payload["connector"] == "fixture" and payload["added"] == 1
    assert (root / "demo").exists()
