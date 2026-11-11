# -*- coding: utf-8 -*-
"""eval/case_server.py —— 给 promptfoo 用的「单题评测」HTTP 口

它自己**不算分**：检索/拒答这一层直接调 `evalset/run_eval.py` 里的函数（同一个实现），
本文件只负责"把一道题跑一遍、把结果用 JSON 说清楚"。

★ 为什么不让 promptfoo 直接调 Python（`python:xxx.py` provider）：
  promptfoo 的 python provider 会 spawn 一个子进程、用**管道**通信。这台机器上
  DSH 的沙箱把 Node 子进程的管道（named pipe）挡掉了（EPERM）——
  而 http provider 只走本机 socket，不受影响。
★ 为什么判分逻辑放在服务端、不写进断言：
  Recall@5 的官方口径是 `rag.search()` 的 top-5（见 run_eval.py 顶部那条注释：
  "不拿 /chat 的 sources"）。断言里拿不到 `rag`，所以只能在进程内算。
  ⇒ 这里 import run_eval，**不抄第二份公式** —— 抄一份就等于造了第二把尺子。

启动（在仓库根目录）：
    python eval/case_server.py                        # 离线层（零 API 成本）
    $env:EVAL_LAYER='online' ; python eval/case_server.py   # 在线层（要 DEEPSEEK_API_KEY）

接口：
    GET  /health -> {"ready": bool, "chunks": int, "rejected": int, "layer": str}
    POST /case   <- {"qid": "h1", "question": "（可选，用于查 tests.yaml 是否过期）"}
                 -> 见下面 _payload()
"""
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# ★ 仓库根：本文件在 <repo>/eval/ 下；也允许用环境变量指向别处（本机调试用）
REPO_ROOT = Path(os.environ.get("POLICY_RAG_REPO") or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "evalset"))

import run_eval as R  # noqa: E402  ★ 判分口径的唯一来源

QUESTIONS = REPO_ROOT / "evalset" / "heldout" / "questions.json"
PORT = int(os.environ.get("EVAL_PORT", "8091"))
LAYER = os.environ.get("EVAL_LAYER", "offline").strip().lower()
if LAYER not in ("offline", "online"):
    raise SystemExit(f"EVAL_LAYER 只能是 offline / online，收到 {LAYER!r}")

_LOCK = threading.Lock()          # RAG 对象不是线程安全的；promptfoo 默认并发 4，这里排成队
_BY_ID: dict[str, dict] = {}
_READY = False


def _load_index() -> None:
    qs = R.load_questions(str(QUESTIONS))
    for q in qs:
        if q["id"] in _BY_ID:
            # 重名会让"配对比较"把两道题当成一道 —— 静默错，所以直接拒绝启动
            raise SystemExit(f"questions.json 里 id 重复：{q['id']}")
        _BY_ID[q["id"]] = q


def _chat(question: str, online: bool):
    """产品那条路：与 run_eval.knowledge_hit() 传的参数**逐字段一致**，只是这里要拿整份响应。"""
    req = R.svc.ChatRequest(question=question, use_rerank=online, use_rewrite=online)
    return R.svc.handle_chat(req)


def _field(resp, name, default=None):
    """handle_chat 成功时返回 pydantic 模型，未就绪分支返回 dict —— 两种都要能取。"""
    if isinstance(resp, dict):
        return resp.get(name, default)
    return getattr(resp, name, default)


def _payload(case: dict) -> dict:
    """跑一道题，返回给人看和给断言看都够用的一份 JSON。"""
    qid, q = case["qid"], _BY_ID[case["qid"]]
    online = LAYER == "online"
    R.USE_REWRITE = online      # 与 run_eval.main() 里那句同语义（离线层不改写）

    drift = bool(case.get("question")) and case["question"] != q["question"]

    out = {
        "qid": qid,
        "type": q["type"],
        "layer": LAYER,
        "drift": drift,
        "question": q["question"],
        "boot": {"chunks": len(R.svc.rag.chunks), "rejected": len(R.svc.rag.rejected)},
    }

    # ① 检索层：gold 条款有没有进粗捞 top-5（只有应答题有 gold）
    if q["type"] == "answer":
        cands = R.svc.rag.search(R.retrieval_query(q), k=5)
        out["gold_hit"] = R.recall_gold(q, k=5)
        gold = tuple(q.get("gold") or [])
        out["gold_rank"] = next(
            (i for i, (c, _) in enumerate(cands, start=1)
             if (c["doc"], c["clause"]) == gold), None)
        out["cands"] = [{"doc": c["doc"], "clause": c["clause"], "score": round(float(s), 4)}
                        for c, s in cands]

    # ② 判据层：knowledge_hit（= 是否拒答）—— 产品自己算的，不是这里算的
    t0 = time.time()
    resp = _chat(q["question"], online)
    out["knowledge_hit"] = bool(_field(resp, "knowledge_hit", True))
    out["answer"] = str(_field(resp, "answer", "") or "")
    out["took_ms"] = int(_field(resp, "took_ms", 0) or 0)
    out["wall_ms"] = int((time.time() - t0) * 1000)
    out["usage"] = _field(resp, "usage")
    srcs = _field(resp, "sources", []) or []
    out["sources"] = [{"doc": s.get("doc") if isinstance(s, dict) else getattr(s, "doc", ""),
                       "score": (s.get("score") if isinstance(s, dict) else getattr(s, "score", None))}
                      for s in srcs]
    return out


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"      # 让 promptfoo 复用连接，105 题不用 105 次握手

    def _send(self, code: int, obj: dict) -> None:
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path.split("?")[0] != "/health":
            self._send(404, {"error": "not found"})
            return
        self._send(200, {"ready": _READY, "chunks": len(R.svc.rag.chunks),
                         "rejected": len(R.svc.rag.rejected), "layer": LAYER})

    def do_POST(self):
        if self.path.split("?")[0] != "/case":
            self._send(404, {"error": "not found"})
            return
        n = int(self.headers.get("Content-Length") or 0)
        try:
            case = json.loads(self.rfile.read(n) or b"{}")
            if case.get("qid") not in _BY_ID:
                self._send(400, {"error": f"未知 qid: {case.get('qid')!r}"})
                return
            with _LOCK:
                payload = _payload(case)
        except Exception as e:                      # noqa: BLE001
            # 抛出去只会变成 500，promptfoo 那头看到的是一行 "Error" —— 把原因带上
            self._send(500, {"error": f"{type(e).__name__}: {e}"})
            return
        # 进度：跑 105 题时这行是"还活着"的唯一证据（promptfoo 的进度条被 --no-progress-bar 关掉了）
        print(f"[case] {payload['qid']} {payload['type']:6s} hit={payload.get('knowledge_hit')} "
              f"gold={payload.get('gold_hit')} {payload['wall_ms']}ms", flush=True)
        self._send(200, payload)

    def log_message(self, *a):
        pass


def main() -> None:
    global _READY
    _load_index()
    print(f"[boot] 题目 {len(_BY_ID)} 道，层={LAYER}，正在加载模型 + 建库……", flush=True)
    t0 = time.time()
    R.svc.rag.startup()
    if LAYER == "offline":
        # 与 run_eval.py 离线层同款：注入人偶模型，保证一个生成调用都不发（也就不需要 key）
        R.svc.rag._llm = R._OfflineLLM()
    _READY = True
    print(f"[boot] 就绪 {time.time() - t0:.1f}s：入库 {len(R.svc.rag.chunks)} 块 / "
          f"拦下 {len(R.svc.rag.rejected)} 块，监听 127.0.0.1:{PORT}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), _Handler).serve_forever()


if __name__ == "__main__":
    main()
