# -*- coding: utf-8 -*-
"""run-state：给"一次评测跑"拍一份完整的系统状态快照，让回滚有靶子。

## 为什么需要它（不是"顺手加个日志"）

项目里 versioning 已经有两层，缺的是第三层：

| 层 | 回答什么问题 | 在哪 |
|---|---|---|
| 文档版本 | **语料**当时是第几版 | `kb/versioning.py` |
| 报告 schema | **报告**的字段什么意思 | `kb/evalkit/report.py` 的 `SCHEMA_VERSION` |
| **系统版本** | **代码/prompt/配置/语料**当时是第几版 | ← 本模块 |

没有第三层，`report-scenario-campus.json` 里的 "拒答 8/36" 是**孤儿数字**：
它不知道自己是在哪个 commit、哪份 .env、哪一版语料上跑出来的。于是：

- **回滚**退不到"那次跑的时候"（`git log` 只记代码，不记 .env 和 kb_data）
- **配对比**不成立（两次跑的可能不是同一个系统，"变好"可能只是语料变了）
- **归因**是假的（指标动了，不知道是改了 prompt 还是改了限流）

## 三个设计取舍（都是知情的，不是遗漏）

① **不 import service.py 读常量。**
   直觉做法是 `from service import VEC_REJECT_THRESHOLD` 把阈值记下来 —— 但 import
   会拉起 FastAPI app、embedding 栈，还可能触发模型下载。**快照必须便宜**，
   否则没人会在每次跑评测前记得拍。
   ⇒ 改为记 **git sha + 工作区 diff 的哈希 + diff 全文**：
     代码里的常量一旦变，diff 就变；比逐个枚举常量更全，也不会漏掉新加的常量。

② **必须记 `.env`，因为它是 git 看不见的那一半。**
   `.env` 在 `.gitignore` 里。把 `RATE_PER_MIN=200` 写进 .env 跑评测、跑完忘了改回来，
   `git status` 依然显示 clean —— 这是本项目最容易发生的一次"静默状态漂移"。
   ⇒ 快照里专门记一份 .env 的**有效值**（密钥只记指纹，不落明文）。

③ **指纹只覆盖"会改变结果的东西"。**
   `created_at`、`run_id`、墙钟耗时都不进指纹。否则同一状态拍两次会得到两个指纹，
   指纹就失去意义（"相同"必须是可判定的）。

## 边界（写清，免得当成它能管更多）

- 它记的是**本机工作区**的状态。远端 CI 跑的结果不在这里。
- 语料指纹取的是 `manifest.json` 里每个文档的 `content_hash`，**不是** manifest 文件本身的字节
  —— manifest 带 `created_at`/`updated_at`，重新上传同一份文档会改字节但不改语义。
- 它**不会**自动改你的工作区。回滚计划只生成、不执行（见 `scripts/rollback.py`）。
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

#: 状态快照的 schema 版本。
#:
#: ★ 为什么要升版本（`run-state/1` → `run-state/2`，2026-10-28）：
#:   这一版把 `docs/inbox` 也纳入了语料哈希。**旧快照没有这个字段** ⇒ 它缺的
#:   恰好是"被拒的那批脏副本"。如果沿用同一个版本号，`run-state/1` 的旧 run 会
#:   被当成"语料没变"而继续参与比较 —— 那正是最危险的一种假象。
#:   ⇒ 换版本号，让旧 run **自动判不可比**（`state_match` 直接 reject），
#:     代价是"要重跑一次基线"，收益是"不会拿缺了一半的证据下结论"。
#:   ⚠️ 版本号进指纹 ⇒ 升版后**所有指纹都会变**，这是有意的：它们确实不是同一个东西了。
STATE_SCHEMA = "run-state/3"

#: 会改变评测结果的配置键（前缀/全名）。故意用白名单而不是"记全部环境变量"：
#: 全量里混着 `PATH`、`TEMP`、一堆与结果无关的变量，每次都变 ⇒ 指纹永远不相等。
ENV_PATTERNS: tuple[str, ...] = (
    r"^RATE_", r"^DAILY_", r"^GLOBAL_DAILY_", r"^LIMIT_",
    r"^TRUST_PROXY", r"^ALLOW_CLIENT_ID", r"^REDIS_URL$",
    r"^CACHE_", r"^CONTEXT_", r"^BUDGET",
    r"^VEC_", r"^DUP_", r"^MIN_LEN$", r"^TOP_K$", r"^RERANK",
    r"^CHAT_", r"^LLM_", r"^EMBED", r"^HF_ENDPOINT$",
    r"^LANGSMITH_TRACING$", r"^FASTEMBED_CACHE_PATH$",
    r"^LANGCHAIN_", r"^INDEX_", r"^KB_",
    r"^DOCLING_", r"^MINERU_", r"^PDF_",
    # ★ 密钥类也要进白名单：只记"设没设 + sha8"，不落明文（见 _SECRET_RE）
    r"^DEEPSEEK_API_KEY$",
)
_ENV_RE = re.compile("|".join(ENV_PATTERNS))

#: ★ 显式登记表：**前缀表盖不住**的那些孤键，必须在这里留一行。
#:
#: 为什么需要它（2026-10-28，起因是 `EXPAND_LEXICON` / `INDEX_TEXT_MODE` 两个旋钮
#: 一直没进白名单）：它们是**闭环的干预变量**。漏登记的后果不是"少记一条日志"，
#: 而是"候选换了、指纹没变" ⇒ 门禁把一个真实的干预看成"什么都没改"，
#: 或者反过来把语料漂移看成干预效果。两个方向都会让结论错。
#:
#: 登记机制不是靠人记：`tests/test_runstate.py::test_所有环境旋钮都已登记` 会扫
#: 源码里的 `os.environ.get(...)` / `num("...")` / `setdefault(...)`，
#: 发现既没匹配前缀、又不在本表、也不在 `ENV_IGNORED` 里的键 ⇒ 测试红。
ENV_REGISTRY: dict[str, str] = {
    # ---- 检索行为（换它就是换基线）----
    "INDEX_TEXT_MODE": "索引文本拼法 plain/text/doc_clause_text —— 换它就是换检索基线",
    "EXPAND_LEXICON": "查询扩展词候选文件 —— **闭环的干预变量**",
    "KB_DATA_ROOT": "语料根目录 —— 指向不同库等于换了整个语料",
    "FASTEMBED_CACHE_PATH": "embedding 模型缓存位置 —— 换了可能换到另一版模型",
    # ---- 模型与成本 ----
    "LLM_TIMEOUT_SEC": "模型超时 —— 太小会把『慢』记成『错』，直接改分数",
    "CACHE_ENABLED": "缓存开关 —— 开着会改掉重复请求的延迟与 token",
    "KB_ADMIN_TOKEN": "上传接口管理口令 —— 决定『能不能写库』（只记指纹）",
    "INTERNAL_TOKEN": "内部接口口令（`/internal/*`，给 Java 业务层用）—— 没配则整组不可用（只记指纹）",
    # ---- 追踪 ----
    # ★ 注意 `LANGSMITH_TRACING` / `LANGCHAIN_TRACING_V2` **不在这里**：它们是派生键
    #   （`service.py` 的 `_guard_tracing_env()` 会按有无 key 强制改写），记它们只会
    #   制造假差异。见 `ENV_DERIVED`。
    "LANGCHAIN_API_KEY": "追踪服务密钥（只记指纹）",
    # ---- PDF 解析后端：**直接决定入库文本**，换后端等于换语料 ----
    "PDF_BACKEND": "PDF 解析后端选择（docling/mineru/…）",
    "DOCLING_CMD": "docling 调用命令（docker / 本地二进制）",
    "DOCLING_IMAGE": "docling 镜像 tag —— 换版本会改解析结果",
    "DOCLING_TIMEOUT": "docling 超时 —— 太小会把长文档解析成空",
    "DOCLING_CACHE_VOLUME": "docling 模型缓存卷",
    "MINERU_CMD": "mineru 调用命令",
    "MINERU_ARGS": "mineru 参数模板",
    "MINERU_TIMEOUT": "mineru 超时",
    # ---- 入库清洗 ----
    "RAG_QUIET_CLEAN": "入库时是否静默清洗（会改切出来的块）",
}

#: 明确**与评测结果无关**的运行时键：出现在源码里也不必登记（端口/主机/编码/测试开关）。
ENV_IGNORED: frozenset[str] = frozenset({
    "HOST", "PORT", "PYTHONIOENCODING", "PYTHONUTF8",
    "REDIS_URL", "TEST_REDIS_URL", "REQUIRE_REDIS",
    "RUN_KEEP",          # 本模块自己的保留策略开关，不改结果
    "LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING",
})

#: **派生键**：我们自己的代码在 import 时写进 `os.environ`，不是用户的配置。
#:
#: ★ 记它们会制造假差异 —— 实测抓到过：同一个系统两次拍快照，
#:   `LANGCHAIN_TRACING_V2` 一次"未设"、一次 `false`，唯一原因是其中一次进程
#:   先 import 了 `service.py`（`_guard_tracing_env()` 会把它强制改成 false）。
#:   门禁于是报"状态出现未声明的差异" ⇒ 一次真实的配对实验被判成不可比。
#: 判据：**它变不变不影响答案** ⇒ 不进状态。
ENV_DERIVED: frozenset[str] = frozenset({"LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING"})


#: 这些键**只记"设没设"和指纹**，绝不落明文（`.env` 里就有真 key）
_SECRET_RE = re.compile(r"KEY|TOKEN|SECRET|PASSWORD", re.IGNORECASE)


# ==================================================================
# 基础工具
# ==================================================================
def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _sha_text(s: str) -> str:
    return _sha(s.encode("utf-8"))


def _canon(obj: Any) -> str:
    """规范化 JSON：键排序、不留空格。指纹必须只由内容决定，不能被序列化细节影响。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _git(root: Path, *args: str) -> str | None:
    """跑一条 git。失败一律返回 None —— 快照不该因为"这不是个 git 仓库"而挂掉。"""
    try:
        r = subprocess.run(["git", *args], cwd=str(root), capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


# ==================================================================
# 四块状态
# ==================================================================
#: 未跟踪文件最多读这么多字节算哈希：再大就只哈希"前 1MB + 总字节数"。
#: ★ 为什么要有上限：`git ls-files --others` 会把没 ignore 掉的日志/数据文件都列进来，
#:   一台机器上出现一个 500MB 的 .log 不该让"拍快照"变成一次全盘 IO。
_MAX_HASH_BYTES = 1 << 20

#: run 目录自己**永远不算未跟踪文件**。
#: ★★ 这条是硬性的：`state.json` 本身就在 `evalset/runs/` 下，如果把它算进去，
#:    每跑一次就多出几个"未跟踪文件" ⇒ 指纹依赖跑了多少次 ⇒ 同一个系统的指纹
#:    会一路变下去。实测抓到过：一次 run 的 untracked 列表里塞了 19 个
#:    `evalset/runs/.../state.json`，**自己把自己写进了自己的指纹**。
_RUNS_REL = "evalset/runs/"


def _untracked_state(root: Path) -> dict[str, Any]:
    """未跟踪文件（`??`）的**内容**哈希。

    ★ 为什么必须单独做（2026-10-28 补，P1）：
      `git diff` 只覆盖**已跟踪**的文件。新建的文件（例如刚写的模块）完全不在其中，
      于是"改了它"和"什么都没改"会得到同一个 `diff_sha256`。
      实测过：`kb/runstate.py` 连续被改三次，指纹在那段时间只随其它文件变化——
      **新文件的改动是隐形的**。而本项目的常态恰恰是"新模块还没 add"。
    ★ 文件名也进哈希：改名/删文件同样是"系统变了"。
    ★ 但 `evalset/runs/` 下的东西要排除（见 `_RUNS_REL` 的注释：那是产物，不是输入）。
    """
    listing = (_git(root, "ls-files", "--others", "--exclude-standard") or "")
    files: list[dict[str, Any]] = []
    for rel in listing.splitlines():
        rel = rel.strip()
        if not rel:
            continue
        shown = rel.replace("\\", "/")
        if shown.startswith(_RUNS_REL) or shown == _RUNS_REL.rstrip("/"):
            continue                       # 产物目录：不计入指纹（否则指纹自指）
        p = root / rel
        try:
            size = p.stat().st_size
            with p.open("rb") as fh:
                head = fh.read(_MAX_HASH_BYTES)
            digest = _sha(head) if size <= _MAX_HASH_BYTES else _sha(head + b"|truncated")
            files.append({"path": shown, "sha256": digest, "bytes": size})
        except OSError:
            files.append({"path": shown, "sha256": "unreadable", "bytes": -1})
    files.sort(key=lambda f: f["path"])
    agg = _sha_text(_canon([[f["path"], f["sha256"]] for f in files]))[:16] if files else ""
    return {"files": files, "count": len(files), "sha256": agg}


def git_state(root: Path) -> dict[str, Any]:
    """代码状态：sha + 分支 + 是否脏 + 工作区 diff（含暂存区）。

    ★ 为什么要 diff 全文而不只是"脏/不脏"：
      本项目现在的现实是**长期带未提交改动跑评测**（见 git status）。
      只记 `dirty=True` 的话，回滚时只知道"当时改过东西"，但**改的是什么已经没了**
      —— 所以 diff 原文要落盘，才能 `git apply` 回来。
    """
    sha = (_git(root, "rev-parse", "HEAD") or "").strip()
    branch = (_git(root, "rev-parse", "--abbrev-ref", "HEAD") or "").strip()
    porcelain = (_git(root, "status", "--porcelain") or "")
    files = [ln[3:].strip() for ln in porcelain.splitlines() if ln.strip()]

    # 未暂存 + 已暂存，两段都要：只看 `git diff` 会漏掉 `git add` 过的改动
    unstaged = _git(root, "diff", "--binary") or ""
    staged = _git(root, "diff", "--cached", "--binary") or ""
    diff = staged + unstaged
    untracked = _untracked_state(root)

    return {
        "sha": sha,
        "branch": branch,
        "dirty": bool(diff.strip()) or untracked["count"] > 0,
        "dirty_files": files[:50],
        "dirty_file_count": len(files),
        "diff_sha256": _sha_text(diff) if diff.strip() else "",
        "diff_bytes": len(diff.encode("utf-8")),
        # ---- 未跟踪文件的**内容**（`git diff` 看不见的那一半）----
        "untracked": untracked["files"][:60],
        "untracked_count": untracked["count"],
        "untracked_sha256": untracked["sha256"],
    }


def _read_dotenv(root: Path) -> dict[str, str]:
    """只读 `.env` 的键值，不加载、不执行、不打印。

    ★ 不用 dotenv 包：这里要把"文件里写了什么"和"进程里生效了什么"分开记，
      而 `load_dotenv()` 会直接改进程环境，正好抹掉这个区别。
    """
    p = root / ".env"
    if not p.exists():
        return {}
    out: dict[str, str] = {}
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _env_effective(entry: Any) -> Any:
    """参与比较/指纹的配置内容：**不含 `source`**。

    ★ 依据：同一个 key 值从进程环境还是 `.env` 读到，是**来源**，不是系统差异。
      旧实现把整个 entry 拿去比 ⇒ 同值不同源被判"配置变了"、指纹也不同（假拒）。
    """
    if not isinstance(entry, Mapping):
        return entry
    return {k: v for k, v in entry.items() if k != "source"}


def env_state(root: Path, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """配置状态：白名单里的键 + 它们从哪来（进程环境 / .env）+ 密钥指纹。

    优先级与 service.py 的 `load_dotenv()` 一致：**进程环境已存在时 .env 不覆盖**
    （python-dotenv 的默认行为是 `override=False`）。
    """
    env = dict(os.environ if env is None else env)
    dotenv = _read_dotenv(root)

    # ★ 命中条件 = 前缀表 **或** 显式登记表；再减去"派生键"（我们自己的代码写进去的）。
    #   后者兜住 `EXPAND_LEXICON` 这类孤键（它不匹配任何前缀，漏了就是"干预变了、指纹没变"）；
    #   前者排除 `LANGCHAIN_TRACING_V2` 这种由 import 副作用产生的假差异。
    keys = sorted({k for k in list(env) + list(dotenv)
                   if (_ENV_RE.search(k) or k in ENV_REGISTRY) and k not in ENV_DERIVED})
    out: dict[str, Any] = {}
    for k in keys:
        raw = env.get(k)
        source = "env"
        if raw is None:
            raw, source = dotenv.get(k), "dotenv"
        if raw is None:
            continue
        if _SECRET_RE.search(k):
            out[k] = {"set": bool(raw), "sha8": _sha_text(raw)[:8], "source": source}
        else:
            out[k] = {"value": raw, "source": source}
    return out


def corpus_state(root: Path, namespace: str = "") -> dict[str, Any]:
    """语料状态：每个命名空间的文档内容哈希集合。

    ★ 取的是 `manifest.json` 里各文档的 `content_hash`，不是文件字节（见模块 docstring 边界）。
    ★★ **已知边界（2026-11-11 盲审点名，这里明写）**：语料走的是「manifest 信任制」——
      绕过上传 API 直接改 `kb_data/*/files/` 里的文件，`content_hash` 不会变，
      ⇒ 指纹**不会变**，本函数也发现不了。要查这种改动只能用文件对账
      （`sha256(文件字节)` vs manifest 里的 `content_hash`），那是一条**尚未实现**的判据；
      当前用法规避它：`kb_data/` 只由上传 API 或 docker compose 初始化写入。
    ★ 命名空间为空时，把 `kb_data/` 下所有命名空间都记上 —— 因为"跑评测时用的是哪个 ns"
      这件事本身也常常记错，全记下来能当场对质。
    """
    base = root / "kb_data"
    if not base.exists():
        return {"namespaces": {}, "namespace_count": 0}

    targets = [namespace] if namespace else sorted(
        d.name for d in base.iterdir() if d.is_dir() and (d / "manifest.json").exists())

    out: dict[str, Any] = {}
    for ns in targets:
        mp = base / ns / "manifest.json"
        if not mp.exists():
            out[ns] = {"exists": False}
            continue
        try:
            records = json.loads(mp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            out[ns] = {"exists": True, "error": f"{type(e).__name__}: {e}"}
            continue
        if not isinstance(records, list):
            out[ns] = {"exists": True, "error": "manifest 不是 list"}
            continue

        # 按 doc_id 排序后取 (doc_id, content_hash) 对：顺序无关、时间戳无关
        pairs = sorted(
            (str(r.get("doc_id") or r.get("file") or "?"),
             str(r.get("content_hash") or ""),
             str(r.get("acl") or "public"),
             str(r.get("status") or "active"),
             str(r.get("effective_date") or ""))
            for r in records if isinstance(r, dict))
        alive = [p for p in pairs if p[1]]
        out[ns] = {
            "exists": True,
            "docs": len(records),
            "docs_with_hash": len(alive),
            "with_acl": sum(1 for p in alive if p[2] not in ("", "public")),
            "hash": _sha_text(_canon(pairs))[:16],
        }
    return {"namespaces": out, "namespace_count": len(out)}


#: 默认语料目录。★ 为什么 kb_data 之外还要记它：`kb_data/<ns>/manifest.json` 只有
#: **上传过**的语料才有；而 `--transport inproc`、`eval_loop run --corpus docs/kb`、
#: 以及"服务启动时直接读 docs/ 的旧路径"都**不经过 manifest**。
#: 只记 kb_data 的话，那几条路径下"改了 docs 里的制度文件"在指纹上是隐形的。
#:
#: ★★ 为什么必须有 `docs/inbox`（2026-10-28 补，评审 ②）：
#:   `service.py:816` 的启动顺序是 **kb 先、inbox 后** —— inbox 里那批"待审副本"
#:   与"明知是脏的标本"**真的会进库、真的会参与把关判定**（263 块里有被拒的那部分）。
#:   只哈希 `docs/kb` 的话，往 inbox 扔一个近似重复的改写副本，指纹一动不动，
#:   而实际入库结果会变（多一条被拦的、甚至挤掉正确的那一版）。
#:   ⇒ 语料状态必须覆盖"实际被读的目录"，而不是"看起来干净的那个目录"。
DOC_DIRS: tuple[str, ...] = ("docs/kb", "docs/inbox")


def docs_state(root: Path, dirs: Sequence[str] = DOC_DIRS) -> dict[str, Any]:
    """把 `docs/` 下的语料按**逐文件内容哈希**记下来（路径 + 内容，不只是数量）。

    ★ 只记文件数是不够的：改一个字、编号不变，文件数也不变。
      而"制度改了一条"恰恰是本项目最该被发现的语料变化。
    """
    out: dict[str, Any] = {}
    for rel in dirs:
        d = root / rel
        if not d.exists():
            out[rel] = {"exists": False, "files": 0, "hash": ""}
            continue
        items: list[dict[str, Any]] = []
        for p in sorted(d.rglob("*.md")):
            try:
                items.append({"path": p.relative_to(root).as_posix(),
                              "sha256": _sha(p.read_bytes()),
                              "bytes": p.stat().st_size})
            except OSError:
                items.append({"path": p.relative_to(root).as_posix(), "sha256": "unreadable",
                              "bytes": -1})
        agg = _sha_text(_canon([[i["path"], i["sha256"]] for i in items]))[:16] if items else ""
        out[rel] = {"exists": True, "files": len(items), "hash": agg, "detail": items}
    return {"dirs": out, "dir_count": len(out)}


def runtime_state() -> dict[str, Any]:
    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "machine": platform.platform(),
        "executable": sys.executable,
    }


# ==================================================================
# 指纹 + 采集
# ==================================================================
def fingerprint(state: Mapping[str, Any]) -> str:
    """状态指纹（12 位）。只由"会改变结果的东西"决定，与时间/路径无关。

    ★ 故意**不含** `run_id` / `created_at` / `wall_clock_ms` / `note`。
      判据：同一个状态拍两次，指纹必须相同 —— 否则它没法用来判"是不是同一个系统"。
    """
    g = state.get("git") or {}
    core = {
        "schema": STATE_SCHEMA,
        "git_sha": g.get("sha") or "",
        "diff_sha": g.get("diff_sha256") or "",
        "untracked_sha": g.get("untracked_sha256") or "",   # 未跟踪文件的内容
        "env": {k: _env_effective(v) for k, v in (state.get("env") or {}).items()},
        "corpus": (state.get("corpus") or {}).get("namespaces") or {},
        "docs": _docs_hashes(state),
        "python": (state.get("runtime") or {}).get("python") or "",
    }
    return _sha_text(_canon(core))[:12]


def _docs_hashes(state: Mapping[str, Any]) -> dict[str, str]:
    """只取每个 docs 目录的聚合哈希 —— 明细留在 state 里，指纹只要"变没变"。"""
    dirs = (state.get("docs") or {}).get("dirs") or {}
    return {k: str(v.get("hash") or "") for k, v in dirs.items()}


def capture(root: str | Path, *, namespace: str = "", note: str = "",
            env: Mapping[str, str] | None = None,
            docs_dir: str = "") -> dict[str, Any]:
    """拍一次快照（不落盘）。返回的 dict 里自带 `fingerprint`。"""
    root = Path(root)
    state: dict[str, Any] = {
        "schema": STATE_SCHEMA,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "note": note,
        "root": str(root),
        "git": git_state(root),
        "env": env_state(root, env),
        "corpus": corpus_state(root, namespace),
        "docs": docs_state(root),
        "runtime": runtime_state(),
        "corpus_docs_dir": docs_dir,
    }
    state["fingerprint"] = fingerprint(state)
    return state


def new_run_id(state: Mapping[str, Any]) -> str:
    """`20261028-101500-a1b2c3d4`：时间可排序，指纹可对人眼。"""
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{state['fingerprint'][:8]}"


def runs_root(root: str | Path) -> Path:
    return Path(root) / "evalset" / "runs"


#: run 目录名的严格形状。**只有匹配它的目录才允许被清理** ——
#: 清理逻辑一旦"按前缀删目录"，就有机会碰到别人的东西。这条正则就是那道锁。
_RUN_ID_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{8}$")

#: 保留最近多少次。默认 20 次（一次带 diff 的 run 大概几十 KB；20 次够回看一周）。
RUN_KEEP = int((os.environ.get("RUN_KEEP") or "20").strip() or 20)

_GITIGNORE = (
    "# run 目录是**机器本地产物**：含 worktree.diff（可能是别人没提交的改动）与绝对路径，\n"
    "# 不该进仓库。要留证据就把 report.json 拷到 evalset/ 下（那份是给人看的）。\n"
    "*\n"
    "!.gitignore\n"
)


def _ensure_gitignore(root: Path) -> None:
    """保证 `evalset/runs/.gitignore` 存在。**不自动 git add**，只是让目录不再污染 `git status`。"""
    base = runs_root(root)
    base.mkdir(parents=True, exist_ok=True)
    gi = base / ".gitignore"
    if not gi.exists() or gi.read_text(encoding="utf-8") != _GITIGNORE:
        gi.write_text(_GITIGNORE, encoding="utf-8")


def prune_runs(root: str | Path, keep: int = RUN_KEEP) -> list[str]:
    """只保留最近 `keep` 次 run，返回被删掉的 run_id。

    ★ 三条安全边界：① 只认 `_RUN_ID_RE` 匹配的目录；② keep<=0 时**不删**（当成"关闭清理"）；
      ③ 用 `ignore_errors=True`，删不掉就留着 —— 清理失败不该让评测跑失败。
    """
    if keep <= 0:
        return []
    base = runs_root(root)
    if not base.exists():
        return []
    dirs = sorted((d for d in base.iterdir() if d.is_dir() and _RUN_ID_RE.match(d.name)),
                  key=lambda d: d.name, reverse=True)
    removed: list[str] = []
    for d in dirs[keep:]:
        shutil.rmtree(d, ignore_errors=True)
        removed.append(d.name)
    return removed


def record(root: str | Path, *, namespace: str = "", note: str = "",
           docs_dir: str = "", env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """拍快照 + 落盘到 `evalset/runs/<run_id>/`。返回给报告 meta 用的几个字段。

    目录里会放三样东西（自包含，拷走就能复盘）：
      - `state.json`    状态全文（含指纹）
      - `worktree.diff` 工作区 diff 原文（**这是"未提交改动也能回滚"的关键**）
      - `report.json`   跑完由 `attach_report()` 补进来
    """
    root = Path(root)
    # ★ 顺序：先确保 .gitignore，再拍快照。
    #   反过来的话，快照会把 runs/ 下已有的产物算进"未跟踪文件" ⇒ 指纹随跑的次数变化。
    _ensure_gitignore(root)
    state = capture(root, namespace=namespace, note=note, env=env, docs_dir=docs_dir)
    run_id = new_run_id(state)
    run_dir = runs_root(root) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    (run_dir / "state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    diff = (_git(root, "diff", "--cached", "--binary") or "") + \
           (_git(root, "diff", "--binary") or "")
    if diff.strip():
        (run_dir / "worktree.diff").write_text(diff, encoding="utf-8")

    pruned = prune_runs(root, RUN_KEEP)

    return {
        "run_id": run_id,
        "run_dir": str(run_dir.relative_to(root)) if str(run_dir).startswith(str(root)) else str(run_dir),
        "state_fingerprint": state["fingerprint"],
        # ★ 把 schema 也交出去：报告 meta 里带上它，门禁就能在**不打开 state.json** 时
        #   先判"这报告是不是旧格式"（旧格式的快照天生缺字段，比较没有意义）。
        "state_schema": state["schema"],
        "pruned": pruned,
        "keep": RUN_KEEP,
    }


def attach_report(root: str | Path, run_id: str, report_path: str | Path) -> Path | None:
    """把报告复制进 run 目录 —— 让"状态"和"它的结果"待在同一个文件夹里。"""
    src = Path(report_path)
    if not src.exists():
        return None
    dst = runs_root(root) / run_id / "report.json"
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return dst


# ==================================================================
# 读取 + 对比
# ==================================================================
def load(root: str | Path, run_id: str) -> dict[str, Any]:
    p = runs_root(root) / run_id / "state.json"
    return json.loads(p.read_text(encoding="utf-8"))


def load_report(root: str | Path, run_id: str) -> dict[str, Any] | None:
    p = runs_root(root) / run_id / "report.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def list_runs(root: str | Path) -> list[dict[str, Any]]:
    """按时间倒序。每项只取"一眼要看的"几个字段，不打全文。"""
    base = runs_root(root)
    if not base.exists():
        return []
    out = []
    for d in base.iterdir():
        sp = d / "state.json"
        if not d.is_dir() or not sp.exists():
            continue
        try:
            s = json.loads(sp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        g = s.get("git") or {}
        out.append({
            "run_id": d.name,
            "created_at": s.get("created_at", ""),
            "note": s.get("note", ""),
            "fingerprint": s.get("fingerprint", ""),
            "sha": g.get("sha", "")[:8],
            "dirty": g.get("dirty", False),
            "has_report": (d / "report.json").exists(),
        })
    return sorted(out, key=lambda r: r["run_id"], reverse=True)


def diff_state_items(old: Mapping[str, Any], new: Mapping[str, Any]) -> list[dict[str, Any]]:
    """两份状态的差异，**结构化的**：`[{"kind","key","before","after"}]`。空列表 = 同一个系统。

    ★ 为什么不用"人话字符串"当判据（2026-10-28 改，评审 ③）：
      门禁原来靠 `any(k in d for k in allow)` —— 在**给人看的**那行文字里做子串匹配。
      于是判据的稳定性依赖文案：改一句措辞、加个括号、把"未设"改成"（未设）"，
      都可能让一次应当拒绝的差异**误放行**（或反过来把合法差异误拒）。
      ⇒ 结构化：`kind` 说"哪一块"，`key` 说"具体哪个键/命名空间/目录"。
        门禁按 `(kind, key)` 精确比对，人话只用来打印。
    """
    items: list[dict[str, Any]] = []

    def add(kind: str, key: str, before: Any, after: Any) -> None:
        items.append({"kind": kind, "key": key, "before": before, "after": after})

    # ---- 第一块：schema 本身 ----
    # ★ 单独成一项：版本不同意味着**字段含义不同**，此时逐字段比较是没有意义的
    #   （旧快照缺的那几块会被读成"无 → 有"，看起来像"语料变了"，其实是"当时没记"）。
    if (old.get("schema") or "") != (new.get("schema") or ""):
        add("schema", "schema", old.get("schema"), new.get("schema"))

    # ---- 第二块：代码 ----
    og, ng = old.get("git") or {}, new.get("git") or {}
    if og.get("sha") != ng.get("sha"):
        add("git", "sha", og.get("sha"), ng.get("sha"))
    if (og.get("diff_sha256") or "") != (ng.get("diff_sha256") or ""):
        add("git", "diff", og.get("diff_sha256"), ng.get("diff_sha256"))
    if (og.get("untracked_sha256") or "") != (ng.get("untracked_sha256") or ""):
        add("git", "untracked", og.get("untracked_sha256"), ng.get("untracked_sha256"))

    # ---- 第三块：配置（每个键一项，key 就是环境变量名）----
    oe, ne = old.get("env") or {}, new.get("env") or {}
    for k in sorted(set(oe) | set(ne)):
        if _env_effective(oe.get(k)) != _env_effective(ne.get(k)):
            add("env", k, oe.get(k), ne.get(k))

    # ---- 第四块：语料（kb_data 命名空间）----
    oc = (old.get("corpus") or {}).get("namespaces") or {}
    nc = (new.get("corpus") or {}).get("namespaces") or {}
    for ns in sorted(set(oc) | set(nc)):
        if oc.get(ns) != nc.get(ns):
            add("corpus", ns, oc.get(ns), nc.get(ns))

    # ---- 第五块：语料目录（docs/kb、docs/inbox 这种直接读的路径）----
    od, nd = _docs_hashes(old), _docs_hashes(new)
    for d in sorted(set(od) | set(nd)):
        if od.get(d) != nd.get(d):
            add("docs", d, od.get(d), nd.get(d))

    return items


def render_state_item(it: Mapping[str, Any]) -> str:
    """把一条结构化差异渲染成人话（**只用于打印/日志，不用于判定**）。"""
    kind, key = str(it.get("kind")), str(it.get("key"))
    before, after = it.get("before"), it.get("after")

    if kind == "schema":
        return f"状态 schema 不同：{before or '?'} → {after or '?'}（字段含义变了，不可直比）"
    if kind == "git":
        if key == "sha":
            return f"代码 commit：{str(before or '?')[:8]} → {str(after or '?')[:8]}"
        if key == "diff":
            return "工作区改动变了（`git diff` 覆盖的那一半）" 
        if key == "untracked":
            return "未跟踪文件内容变了（`git diff` 看不见的那一半）"
        return f"代码 {key} 变了"
    if kind == "env":
        fmt = lambda v: (v.get("value") if isinstance(v, Mapping) and "value" in v
                         else ("<secret>" if isinstance(v, Mapping) else "未设"))
        src = lambda v: (f"（{v.get('source')}）" if isinstance(v, Mapping) and v.get("source") else "")
        if before is None:
            return f"配置 {key}：未设 → {fmt(after)}{src(after)}"
        if after is None:
            return f"配置 {key}：{fmt(before)} → 未设"
        return f"配置 {key}：{fmt(before)} → {fmt(after)}{src(after)}"
    if kind == "corpus":
        if not isinstance(before, Mapping):
            return f"语料 {key}：新增（{after.get('docs', 0) if isinstance(after, Mapping) else '?'} 篇）"
        if not isinstance(after, Mapping):
            return f"语料 {key}：消失"
        return (f"语料 {key}：{before.get('docs', 0)} 篇/{before.get('hash', '-')} → "
                f"{after.get('docs', 0)} 篇/{after.get('hash', '-')}")
    if kind == "docs":
        return f"语料目录 {key}：{before or '（无）'} → {after or '（无）'}"
    return f"{kind}/{key}: {before} → {after}"


def diff_states(old: Mapping[str, Any], new: Mapping[str, Any]) -> list[str]:
    """两份状态的人话差异（**展示用**）。判定请用 `diff_state_items()`。"""
    return [render_state_item(it) for it in diff_state_items(old, new)]


def compare_reports(a: Mapping[str, Any], b: Mapping[str, Any]) -> dict[str, Any]:
    """逐题对比两份报告（配对实验的核心：比同一道题，不比两个总数）。

    ★ 判"做对"用的是 `kb.loopgate.is_ok`（以报告里的 `bucket` 为准），
      **不是** `row["passed"]` —— 那个字段根本不存在，旧实现于是把所有题都跳过了
      （`unchanged=0`、`fixed=[]`，看起来"没有变化"，实际是"什么都没比"）。
      口径只允许有一份，和门禁共用同一个函数。
    ★ 为什么必须逐题：总数 +2 可能是"修好 3 题、弄坏 1 题"。
      只看总数，那 1 题回归会永远藏着 —— 而它可能正是最重要的边界题。
    """
    from kb.loopgate import is_ok          # 延迟导入：避免 kb 包初始化期的循环依赖

    ra = {str(r.get("id")): r for r in (a.get("rows") or [])}
    rb = {str(r.get("id")): r for r in (b.get("rows") or [])}
    common = sorted(set(ra) & set(rb))

    fixed, broke, same = [], [], 0
    bucket_changes: dict[str, list[str]] = {}
    for cid in common:
        x, y = ra[cid], rb[cid]
        ok_x, ok_y = is_ok(x), is_ok(y)
        if not ok_x and ok_y:
            fixed.append(cid)
        elif ok_x and not ok_y:
            broke.append(cid)
        else:
            same += 1
        bx, by = str(x.get("bucket") or ""), str(y.get("bucket") or "")
        if bx != by:
            bucket_changes[cid] = [bx, by]

    return {
        "n_a": len(ra), "n_b": len(rb), "n_common": len(common),
        "only_a": sorted(set(ra) - set(rb)), "only_b": sorted(set(rb) - set(ra)),
        "fixed": fixed, "broke": broke, "unchanged": same,
        # 桶的变化：解释"为什么这题算修好/弄坏"，比单看布尔值有用
        "bucket_changes": bucket_changes,
        "agg_a": a.get("aggregate"), "agg_b": b.get("aggregate"),
    }


def _paths_equal(a: Any, b: Any, root: Any = None) -> bool:
    """路径比较：接受"相对/绝对/反斜杠"混写；相对路径按 state 的 root 解析。"""
    if not a or not b:
        return False

    def norm(p: Any) -> str:
        s = str(p).replace("\\", "/").strip()
        try:
            pp = Path(s)
            if not pp.is_absolute() and root:
                pp = Path(str(root)) / pp
            return os.path.normcase(str(pp.resolve()))
        except (OSError, ValueError):
            return os.path.normcase(s)

    return norm(a) == norm(b)


def state_match(before: Mapping[str, Any], after: Mapping[str, Any], *,
                allow_env: Sequence[str] = (),
                expect_env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """两份状态是不是"只差我声明要改的那一处"。**门禁的前置判据。**

    ★ 为什么不能简单地"要求指纹相等"：
      闭环的 after 那次**故意**设了 `EXPAND_LEXICON=候选`，指纹本来就该不同。
      要求相等等于"任何候选都判 reject"。
    ★ 也不能只看"指纹不同就放行"：
      那样代码改了、语料漂了，都会被算成"候选的功劳"（这正是本项目踩过的：
      +1 的增益小于基线自身的波动，最后只能诚实地结论"没有证据"）。
    ⇒ 正确判据是**差集必须落在已声明的旋钮里**（`allow_env`），其余一律 reject。

    返回 `{"ok", "reasons", "deltas", "items", "unexpected", "unexpected_items",
           "allow_env", "expect_env", "before_fp", "after_fp"}`。
    """
    items = diff_state_items(before, after)
    allow = list(allow_env)
    expect = dict(expect_env or {})

    # ★ 判据本体：按 (kind, key) **精确**比对，不做字符串匹配（2026-10-28 改，评审 ③）。
    #   旧写法 `any(k in d for k in allow)` 是在**给人看的**那行文字里找子串 ——
    #   改一句文案（"未设"→"（未设）"、加个括号）就可能误放行或误拒。
    #   现在只有 `kind == "env"` 且 `key` 正好命中的才豁免；其它 kind 一律不可声明豁免。
    unexpected_items = [it for it in items
                        if not (it["kind"] == "env" and it["key"] in allow)]

    reasons: list[str] = []
    if not (before.get("fingerprint") and after.get("fingerprint")):
        reasons.append("报告缺状态指纹（run-state 之前跑的）⇒ 无法证明两次跑的是同一个系统")

    # ★ schema 不同单独判：字段含义变了，逐字段比较没有意义
    #   （旧快照缺的那几块会被读成"无 → 有"，看起来像"语料变了"，其实是"当时没记"）。
    schema_items = [it for it in items if it["kind"] == "schema"]
    if schema_items:
        reasons.append(render_state_item(schema_items[0])
                       + " ⇒ 不可比：升级快照格式后必须重跑一次基线")

    hard = [it for it in unexpected_items if it["kind"] != "schema"]
    if hard:
        shown = "；".join(render_state_item(it) for it in hard[:6])
        more = f"（另有 {len(hard) - 6} 处）" if len(hard) > 6 else ""
        reasons.append(f"状态出现未声明的差异：{shown}{more}")

    if not items and allow:
        # 声明了干预但状态一模一样 ⇒ 干预没生效（最典型：EXPAND_LEXICON 没被读到）
        reasons.append(f"两次状态完全相同 ⇒ 声明的干预（{'/'.join(allow)}）没有生效")

    # ★ 2026-10-28：只校验"变了"不够 —— 必须校验"变成了候选文件"。
    #   否则填一个不相干的词表也能被算成"候选生效"，增益会记到候选头上。
    root = after.get("root") or before.get("root")
    env_after = after.get("env") or {}
    for k, want in expect.items():
        got = (env_after.get(k) or {}).get("value")
        if not _paths_equal(got, want, root):
            reasons.append(f"声明的干预 {k} 没指向候选文件：期望 {want}，实际 {got!r}")

    return {
        "ok": not reasons,
        "reasons": reasons,
        # ---- 给人看 ----
        "deltas": [render_state_item(it) for it in items],
        "unexpected": [render_state_item(it) for it in unexpected_items],
        # ---- 给机器判（判据本体，报告里也留一份便于复盘）----
        "items": items,
        "unexpected_items": unexpected_items,
        "allow_env": allow,
        "expect_env": expect,
        "before_fp": before.get("fingerprint"),
        "after_fp": after.get("fingerprint"),
    }
