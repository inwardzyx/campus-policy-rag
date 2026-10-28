# -*- coding: utf-8 -*-
"""run-state 的回归测试：**指纹必须能判"是不是同一个系统"**。

这套断言要钉住四件事，因为它们是回滚/配对能否成立的前提：

1. 同一个状态拍两次 → 指纹**相同**（否则指纹不可用）
2. 只改一个配置键 → 指纹**不同**，且 `diff_states` 能说出是哪个键
3. 语料指纹**与时间戳无关**（重新上传同一份文档不该改变指纹）
4. 逐题对比能分出"修好"和"弄坏"（只看总数会漏掉回归）

跑法（仓库根目录）：
    python -m pytest tests/test_runstate.py -v

★ 不需要 API key、不联网、不加载模型 —— 这个模块只读文件系统和 git。
"""
from __future__ import annotations

import copy
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kb import runstate  # noqa: E402


# ------------------------------------------------------------------
def test_指纹对同一状态稳定(tmp_path: Path):
    """同一状态拍两次，指纹必须一模一样。这是"可判定"的最低要求。"""
    env = {"RATE_PER_MIN": "20", "RATE_BURST": "10", "PATH": "/whatever"}
    a = runstate.capture(tmp_path, namespace="", env=env)
    b = runstate.capture(tmp_path, namespace="", env=env)
    assert a["fingerprint"] == b["fingerprint"]
    assert len(a["fingerprint"]) == 12


def test_只改一个配置键就换指纹(tmp_path: Path):
    """差一个 RATE_PER_MIN，就不是同一个系统 —— 这是最容易漏的一种漂移。"""
    slow = runstate.capture(tmp_path, env={"RATE_PER_MIN": "20"})
    fast = runstate.capture(tmp_path, env={"RATE_PER_MIN": "200"})
    assert slow["fingerprint"] != fast["fingerprint"]

    deltas = runstate.diff_states(slow, fast)
    assert any("RATE_PER_MIN" in d for d in deltas), deltas
    assert any("20" in d and "200" in d for d in deltas), deltas


def test_密钥不落明文(tmp_path: Path):
    """`.env` 里有真 key —— 快照里只允许出现指纹。"""
    (tmp_path / ".env").write_text("DEEPSEEK_API_KEY=sk-abcdef123456\nRATE_BURST=10\n",
                                   encoding="utf-8")
    st = runstate.capture(tmp_path, env={})
    entry = st["env"]["DEEPSEEK_API_KEY"]
    assert "value" not in entry, "密钥的值绝对不能进快照"
    assert entry["set"] is True and len(entry["sha8"]) == 8
    # 同一份 .env 里非密钥项仍要记原值（否则没法回滚配置）
    assert st["env"]["RATE_BURST"]["value"] == "10"


def test_env优先于dotenv(tmp_path: Path):
    """进程环境已存在值时，.env 不覆盖（与 service.py 的 load_dotenv 行为一致）。"""
    (tmp_path / ".env").write_text("RATE_PER_MIN=999\n", encoding="utf-8")
    st = runstate.capture(tmp_path, env={"RATE_PER_MIN": "20"})
    assert st["env"]["RATE_PER_MIN"] == {"value": "20", "source": "env"}

    st2 = runstate.capture(tmp_path, env={})
    assert st2["env"]["RATE_PER_MIN"] == {"value": "999", "source": "dotenv"}


# ------------------------------------------------------------------
def _write_manifest(tmp_path: Path, ns: str, records: list[dict]) -> None:
    d = tmp_path / "kb_data" / ns
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")


def test_语料指纹与时间戳无关(tmp_path: Path):
    """同一批文档、只是 updated_at 变了 ⇒ 语料**没变**。

    ★ 这条不是洁癖：manifest 里带 created_at/updated_at，若把它们算进哈希，
      重新上传一次同一份文档就会让指纹变化 ⇒ 所有历史 run 立刻"不可比"。
    """
    base = [{"doc_id": "d1", "content_hash": "aaa", "acl": "public",
             "status": "active", "effective_date": "2026-01-01"},
            {"doc_id": "d2", "content_hash": "bbb", "acl": "hr",
             "status": "active", "effective_date": "2026-02-01"}]
    _write_manifest(tmp_path, "ns", [dict(r, updated_at="2026-01-05") for r in base])
    h1 = runstate.corpus_state(tmp_path, "ns")["namespaces"]["ns"]["hash"]

    _write_manifest(tmp_path, "ns", [dict(r, updated_at="2026-09-09") for r in base])
    h2 = runstate.corpus_state(tmp_path, "ns")["namespaces"]["ns"]["hash"]

    assert h1 == h2


def test_语料内容变了指纹要变(tmp_path: Path):
    rec = {"doc_id": "d1", "content_hash": "aaa", "acl": "public",
           "status": "active", "effective_date": "2026-01-01"}
    _write_manifest(tmp_path, "ns", [rec])
    h1 = runstate.corpus_state(tmp_path, "ns")["namespaces"]["ns"]["hash"]

    _write_manifest(tmp_path, "ns", [dict(rec, content_hash="CHANGED")])
    h2 = runstate.corpus_state(tmp_path, "ns")["namespaces"]["ns"]["hash"]

    assert h1 != h2
    deltas = runstate.diff_states(
        {"corpus": {"namespaces": {"ns": {"docs": 1, "hash": h1}}}},
        {"corpus": {"namespaces": {"ns": {"docs": 1, "hash": h2}}}})
    assert any("语料 ns" in d for d in deltas), deltas


# ------------------------------------------------------------------
def test_逐题对比能抓出回归(tmp_path: Path):
    """总数从 2/4 变 3/4 看着是涨了，但其中一题其实是**被弄坏**的。

    ★ 判据必须是 `bucket`（和门禁共用 `loopgate.is_ok`），不是 `passed` 字段
      —— 报告里根本没有 `passed`，旧实现于是把每道题都跳过，"什么都没比"还会
      长得像"没有变化"。
    """
    def row(cid: str, bucket: str, typ: str = "answer") -> dict:
        return {"id": cid, "type": typ, "bucket": bucket}

    a = {"rows": [row("q1", "ok"), row("q2", "miss"),
                  row("q3", "miss"), row("q4", "ok")]}
    b = {"rows": [row("q1", "ok"), row("q2", "ok"),
                  row("q3", "ok"), row("q4", "miss")]}
    cmp = runstate.compare_reports(a, b)
    assert cmp["fixed"] == ["q2", "q3"]
    assert cmp["broke"] == ["q4"]
    assert cmp["unchanged"] == 1
    # 桶的变化要能解释"为什么算修好/弄坏"
    assert cmp["bucket_changes"]["q4"] == ["ok", "miss"]


def test_拒答题必须刚好_refuse_ok():
    """拒答题答上来了（bucket=answer_not_found 之类）不算"做对"。"""
    def row(bucket: str, typ: str) -> dict:
        return {"id": "q1", "type": typ, "bucket": bucket}

    a = {"rows": [row("refuse_ok", "refuse")]}
    b = {"rows": [row("ok", "refuse")]}          # 桶变成 ok，但它是拒答题
    cmp = runstate.compare_reports(a, b)
    assert cmp["broke"] == ["q1"], "拒答题从 refuse_ok 变成 ok 必须算回归"


def test_题目集合不同要单独标出(tmp_path: Path):
    """题集换了就不能当成"系统变化" —— 这条防的是"换套更简单的题然后宣称变好"。"""
    cmp = runstate.compare_reports({"rows": [{"id": "q1", "passed": True}]},
                                   {"rows": [{"id": "q9", "passed": True}]})
    assert cmp["only_a"] == ["q1"] and cmp["only_b"] == ["q9"]
    assert cmp["n_common"] == 0


# ------------------------------------------------------------------
def test_record落盘三件套(tmp_path: Path):
    """run 目录必须自包含：state.json 一定要有；有脏改动时 worktree.diff 也要有。"""
    r = runstate.record(tmp_path, namespace="", note="单测")
    run_dir = Path(r["run_dir"])
    if not run_dir.is_absolute():
        run_dir = tmp_path / "evalset" / "runs" / r["run_id"]
    assert (run_dir / "state.json").exists()

    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["schema"] == runstate.STATE_SCHEMA
    assert state["fingerprint"] == r["state_fingerprint"]
    assert r["run_id"].endswith(state["fingerprint"][:8])


# ------------------------------------------------------------------
def test_未跟踪文件内容进指纹(tmp_path: Path):
    """**新写的文件改了，指纹必须变。**

    ★ 这条是 P1 的核心：`git diff` 只覆盖已跟踪文件，而本项目常态是"新模块还没 add"。
      修之前：改 `kb/runstate.py` 这类"还没 add"的新模块内容，`diff_sha256` 一动不动 ⇒ 门禁把"代码变了"
      当成"什么都没改"。
    """
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "kb").mkdir()
    mod = tmp_path / "kb" / "new_mod.py"

    mod.write_text("VALUE = 1\n", encoding="utf-8")
    s1 = runstate.capture(tmp_path, env={})
    assert s1["git"]["untracked_count"] == 1
    assert s1["git"]["untracked_sha256"], "未跟踪文件必须产生聚合哈希"

    mod.write_text("VALUE = 2\n", encoding="utf-8")      # 只改内容、文件名不变
    s2 = runstate.capture(tmp_path, env={})
    assert s2["git"]["untracked_sha256"] != s1["git"]["untracked_sha256"]
    assert s2["fingerprint"] != s1["fingerprint"], "未跟踪文件内容变了，指纹必须变"


def test_docs默认语料进指纹(tmp_path: Path):
    """`docs/kb` 的内容必须进状态 —— 它是不经 `kb_data/manifest` 的那条路径。"""
    d = tmp_path / "docs" / "kb"
    d.mkdir(parents=True)
    (d / "制度.md").write_text("# 第一条\n原始内容\n", encoding="utf-8")

    s1 = runstate.capture(tmp_path, env={})
    assert s1["docs"]["dirs"]["docs/kb"]["files"] == 1
    assert s1["docs"]["dirs"]["docs/kb"]["hash"]

    (d / "制度.md").write_text("# 第一条\n改过的内容\n", encoding="utf-8")
    s2 = runstate.capture(tmp_path, env={})
    assert s2["docs"]["dirs"]["docs/kb"]["hash"] != s1["docs"]["dirs"]["docs/kb"]["hash"]
    assert s2["fingerprint"] != s1["fingerprint"]
    assert any("docs/kb" in d for d in runstate.diff_states(s1, s2))


def test_docs_inbox也进指纹(tmp_path: Path):
    """**`docs/inbox` 必须一起哈希。**

    ★ 为什么（评审 ②）：`service.py:816` 的启动顺序是 kb 先、inbox 后 ——
      inbox 里那批"待审副本 / 明知是脏的标本"**真的会进库、真的参与把关判定**。
      只哈希 `docs/kb` 的话，往 inbox 扔一个近似重复的改写副本，指纹一动不动，
      而实际入库结果会变（多一条被拦的，甚至挤掉正确的那一版）。
    """
    for sub in ("kb", "inbox"):
        d = tmp_path / "docs" / sub
        d.mkdir(parents=True)
        (d / f"{sub}.md").write_text(f"# {sub}\n内容\n", encoding="utf-8")

    s1 = runstate.capture(tmp_path, env={})
    assert s1["docs"]["dirs"]["docs/inbox"]["files"] == 1

    (tmp_path / "docs" / "inbox" / "新副本.md").write_text("# 改写版\n近似重复\n", encoding="utf-8")
    s2 = runstate.capture(tmp_path, env={})
    assert s2["docs"]["dirs"]["docs/inbox"]["hash"] != s1["docs"]["dirs"]["docs/inbox"]["hash"]
    assert s2["fingerprint"] != s1["fingerprint"], "inbox 变了，指纹必须变"
    assert any("docs/inbox" in d for d in runstate.diff_states(s1, s2))


# ------------------------------------------------------------------
def test_状态判据_差异必须落在声明范围内():
    """门禁的前置判据：差集只能落在 `allow_env` 里。"""
    base = {"fingerprint": "aaa", "git": {"sha": "s1", "diff_sha256": ""},
            "env": {"EXPAND_LEXICON": {"value": "", "source": "env"}},
            "corpus": {"namespaces": {}}, "docs": {"dirs": {}}}

    # ① 只差 EXPAND_LEXICON ⇒ 通过
    only_knob = {**base, "fingerprint": "bbb",
                 "env": {"EXPAND_LEXICON": {"value": "c.json", "source": "env"}}}
    r = runstate.state_match(base, only_knob, allow_env=("EXPAND_LEXICON",))
    assert r["ok"], r

    # ② 同时差了语料 ⇒ 拒
    drifted = {**only_knob,
               "corpus": {"namespaces": {"campus": {"docs": 3, "hash": "h"}}}}
    r = runstate.state_match(base, drifted, allow_env=("EXPAND_LEXICON",))
    assert not r["ok"]
    assert any("未声明的差异" in x for x in r["reasons"]), r["reasons"]

    # ③ 声明了干预但状态一模一样 ⇒ 候选没生效
    r = runstate.state_match(base, dict(base), allow_env=("EXPAND_LEXICON",))
    assert not r["ok"]
    assert any("没有生效" in x for x in r["reasons"]), r["reasons"]

    # ④ 缺指纹 ⇒ 拒（老报告不能冒充配对实验）
    r = runstate.state_match({"git": {}}, {"git": {}}, allow_env=())
    assert not r["ok"]
    assert any("缺状态指纹" in x for x in r["reasons"]), r["reasons"]


def test_判据按kind与key精确比对_不看文案(tmp_path: Path):
    """**判据不能依赖人话文案。**

    ★ 评审 ③ 指出的脆点：旧实现是 `any(k in d for k in allow)` —— 在给人看的
      那行文字里找子串。这条用例构造的正是它的反例：让某个**跟配置无关**的差异
      文字里恰好出现被允许的键名（这里让 docs 的 hash 就写成 `EXPAND_LEXICON`），
      旧实现会把它当成合法豁免而**放行**；新实现按 `kind/key` 判，必须拒绝。
    """
    base = {"schema": runstate.STATE_SCHEMA, "fingerprint": "aa",
            "git": {}, "env": {}, "corpus": {"namespaces": {}},
            "docs": {"dirs": {"docs/kb": {"hash": "h1"}}}}
    after = copy.deepcopy(base)
    after["fingerprint"] = "bb"
    after["docs"]["dirs"]["docs/kb"]["hash"] = "EXPAND_LEXICON"   # 故意等于被允许的键名

    r = runstate.state_match(base, after, allow_env=("EXPAND_LEXICON",))
    assert not r["ok"], "只有 env/EXPAND_LEXICON 可以豁免，docs 变化必须拒"
    assert [it["kind"] for it in r["unexpected_items"]] == ["docs"]
    # 结构化字段齐备：机器消费的就是这两个
    assert r["unexpected_items"][0]["key"] == "docs/kb"
    assert "before" in r["unexpected_items"][0]


def test_env差异是结构化的(tmp_path: Path):
    """配置差异必须给出 `(kind="env", key=变量名)`，而不是一句拼好的中文。"""
    a = {"schema": runstate.STATE_SCHEMA, "fingerprint": "aa", "git": {},
         "env": {"RATE_PER_MIN": {"value": "20", "source": "env"}},
         "corpus": {"namespaces": {}}, "docs": {}}
    b = copy.deepcopy(a)
    b["fingerprint"] = "bb"
    b["env"]["RATE_PER_MIN"] = {"value": "200", "source": "env"}

    items = runstate.diff_state_items(a, b)
    assert items == [{"kind": "env", "key": "RATE_PER_MIN",
                      "before": {"value": "20", "source": "env"},
                      "after": {"value": "200", "source": "env"}}]
    # 人话只用于展示，且必须能渲染出来
    assert "RATE_PER_MIN" in runstate.render_state_item(items[0])


def test_schema不同直接判不可比():
    """旧 schema 的快照缺字段 ⇒ 必须 reject，而不是把它读成"无 → 有"。"""
    a = {"schema": "run-state/1", "fingerprint": "aa", "git": {}, "env": {},
         "corpus": {"namespaces": {}}}
    b = {"schema": runstate.STATE_SCHEMA, "fingerprint": "bb", "git": {}, "env": {},
         "corpus": {"namespaces": {}}, "docs": {"dirs": {"docs/inbox": {"hash": "x"}}}}

    r = runstate.state_match(a, b, allow_env=())
    assert not r["ok"]
    assert any("schema" in x for x in r["reasons"]), r["reasons"]
    assert any(it["kind"] == "schema" for it in r["unexpected_items"])


# ------------------------------------------------------------------
def test_保留策略只删匹配形状的目录(tmp_path: Path):
    """清理只认 `YYYYMMDD-HHMMSS-<8hex>`；别的东西一个都不许碰。"""
    base = runstate.runs_root(tmp_path)
    base.mkdir(parents=True)
    keep_me = base / "not-a-run"
    keep_me.mkdir()
    (keep_me / "important.txt").write_text("别删我", encoding="utf-8")

    for i in range(5):
        d = base / f"2026010{i + 1}-101500-abcdef{i:02d}"
        d.mkdir()
        (d / "state.json").write_text("{}", encoding="utf-8")

    removed = runstate.prune_runs(tmp_path, keep=2)
    assert len(removed) == 3
    left = sorted(p.name for p in base.iterdir() if p.is_dir())
    assert len([n for n in left if runstate._RUN_ID_RE.match(n)]) == 2
    assert keep_me.exists(), "形状不匹配的目录绝不能被清理逻辑碰到"


def test_保留策略_keep为0时关闭清理(tmp_path: Path):
    base = runstate.runs_root(tmp_path)
    base.mkdir(parents=True)
    (base / "20260101-101500-abcdef01").mkdir()
    assert runstate.prune_runs(tmp_path, keep=0) == []
    assert len(list(base.iterdir())) == 1


def test_runs目录不计入自己的指纹(tmp_path: Path):
    """**产物不能变成输入。**

    ★ 抓到过的真 bug：`state.json` 自己就在 `evalset/runs/` 下，被 `git ls-files --others`
      列成"未跟踪文件" ⇒ 每跑一次就多几个文件 ⇒ 指纹随"跑过几次"变化。
      同一个系统连拍两次得到两个不同指纹，那指纹就废了。
    """
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")

    r1 = runstate.record(tmp_path, note="第一次")
    r2 = runstate.record(tmp_path, note="第二次")

    s2 = json.loads((runstate.runs_root(tmp_path) / r2["run_id"] / "state.json")
                    .read_text(encoding="utf-8"))
    paths = [f["path"] for f in (s2["git"].get("untracked") or [])]
    assert not [p for p in paths if p.startswith("evalset/runs/")], paths
    assert r1["state_fingerprint"] == r2["state_fingerprint"], \
        "同一系统连拍两次，指纹必须相同（否则指纹自指）"


# ------------------------------------------------------------------
_ENV_READ_RE = re.compile(
    r'os\.environ\.get\(\s*["\']([A-Za-z_][A-Za-z_0-9]*)'
    r'|os\.getenv\(\s*["\']([A-Za-z_][A-Za-z_0-9]*)'
    r'|os\.environ\.setdefault\(\s*["\']([A-Za-z_][A-Za-z_0-9]*)'
    r'|\bnum\(\s*["\']([A-Za-z_][A-Za-z_0-9]*)')


def _env_keys_in_source() -> dict[str, set[str]]:
    """扫描**服务运行路径**上的环境变量读取点（静态读，不 import service）。

    ★ 为什么静态扫而不是 import：`service.py` 顶层要 numpy/fastapi/fastembed，
      CI 的快 job 只装 pytest ⇒ import 会直接红（本项目已经踩过一次）。
    """
    targets = [ROOT / "service.py", ROOT / "limits.py", ROOT / "cache.py"]
    targets += sorted((ROOT / "kb").rglob("*.py"))
    found: dict[str, set[str]] = {}
    for p in targets:
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        for m in _ENV_READ_RE.finditer(text):
            key = next(g for g in m.groups() if g)
            if key != key.upper():           # 只认全大写（`ProgramFiles` 这种不是旋钮）
                continue
            found.setdefault(key, set()).add(p.name)
    return found


def test_所有环境旋钮都已登记():
    """**登记机制**：源码里读的环境变量，必须能被白名单覆盖。

    ★ 这条防的是"以后新增的旋钮忘了登记"：漏登记的后果不是少记日志，
      而是"它变了、指纹没变" —— 门禁会把环境漂移当成候选效果（或反过来）。
    修法只有两种：加进 `ENV_PATTERNS`（成族的键）或 `ENV_REGISTRY`（孤键）；
    确实与结果无关的，加进 `ENV_IGNORED` 并写清理由。
    """
    missing = {k: sorted(v) for k, v in _env_keys_in_source().items()
               if not runstate._ENV_RE.search(k)
               and k not in runstate.ENV_REGISTRY
               and k not in runstate.ENV_IGNORED}
    assert not missing, (
        "这些环境变量既没匹配前缀、也没在 ENV_REGISTRY 登记：\n"
        + "\n".join(f"  {k:24s} 读于 {', '.join(v)}" for k, v in sorted(missing.items())))


def test_登记表里的键至少能被采集到(tmp_path: Path):
    """登记表不能是摆设：写进 .env 的已登记键必须出现在 env_state 里。"""
    sample = "\n".join(f"{k}=x" for k in runstate.ENV_REGISTRY if "KEY" not in k)
    (tmp_path / ".env").write_text(sample + "\n", encoding="utf-8")
    st = runstate.capture(tmp_path, env={})
    for k in runstate.ENV_REGISTRY:
        if "KEY" not in k:
            assert k in st["env"], f"{k} 登记了却没被采集"


def test_派生键不进状态(tmp_path: Path):
    """**我们自己的代码写进环境的那几个键不能算状态。**

    ★ 实测的假差异：`service.py` 在没配 key 时把 `LANGCHAIN_TRACING_V2` 改成 `false`，
      于是"这次进程先 import 了 service"就会让同一个系统看起来"配置变了"。
      门禁会因此把一次真实的配对实验判成不可比。
    """
    st = runstate.capture(tmp_path, env={"LANGCHAIN_TRACING_V2": "false",
                                        "LANGSMITH_TRACING": "true",
                                        "RATE_PER_MIN": "20"})
    for k in runstate.ENV_DERIVED:
        assert k not in st["env"], f"{k} 是派生键，不该进状态"
    assert st["env"]["RATE_PER_MIN"]["value"] == "20"
