# -*- coding: utf-8 -*-
"""执行方式：HTTP / 录好的响应 / 直连管道。**runner 只依赖这个接口**。

## 为什么要三个
- `HttpTransport`：真实部署形态（含限流、鉴权、SSE 之外的一切）；
- `RecordedTransport`：离线复算。评测逻辑本身要能进单测、不花钱、结论可复查；
- `InProcTransport`：直接调 `handle_chat`（不经 HTTP/限流）。适合在 CI 里跑
  "带假模型的端到端"，也适合没有服务进程的环境。三者对 runner 完全等价。

## 统一契约
`ask(case) -> dict | None`：`None` 表示"这一题没拿到响应"（**不是空答案**），
由报告把它记成 `no_response`，而不是让指标悄悄少算一题。
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Mapping, Protocol


class Transport(Protocol):
    name: str

    def ask(self, case: Mapping[str, Any]) -> dict[str, Any] | None: ...


class RecordedTransport:
    """读一份 `{case_id: response}`（或 `{"responses": {...}}`）的录好响应。"""

    name = "recorded"

    def __init__(self, responses: Mapping[str, Any]):
        self._r = dict(responses.get("responses", responses)) \
            if isinstance(responses, Mapping) else {}
        self.missing: list[str] = []

    @classmethod
    def from_file(cls, path: str | Path) -> "RecordedTransport":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(data)

    def ask(self, case: Mapping[str, Any]) -> dict[str, Any] | None:
        resp = self._r.get(str(case.get("id")))
        if resp is None:
            self.missing.append(str(case.get("id")))
        return resp


class HttpTransport:
    """打真实服务。`bootstrap_docs` 可选：先把语料灌进 namespace（带 trace 才有指标）。"""

    name = "http"

    def __init__(self, base_url: str, *, namespace: str | None = None,
                 acl: str | None = None, event_time: str | None = None,
                 pace: float = 0.0, timeout: float = 180.0):
        self.base = base_url.rstrip("/")
        self.namespace = namespace
        self.acl = [a.strip() for a in acl.split(",") if a.strip()] if acl else None
        self.event_time = event_time
        self.pace = pace
        self.timeout = timeout

    def _call(self, method: str, path: str, body: bytes | None = None,
              headers: Mapping[str, str] | None = None) -> tuple[int, dict[str, Any]]:
        req = urllib.request.Request(self.base + path, data=body, method=method,
                                     headers=dict(headers or {}))
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read().decode("utf-8")
                return r.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as e:
            return e.code, {"error": e.read().decode("utf-8", "replace")}

    def bootstrap_docs(self, corpus: str | Path, *, namespace: str | None = None) -> list[str]:
        ns = namespace or self.namespace
        if not ns:
            raise ValueError("bootstrap_docs 需要 namespace")
        done = []
        for path in sorted(Path(corpus).glob("*.md")):
            q = urllib.parse.urlencode({"filename": path.name, "acl": "public"})
            code, body = self._call("POST", f"/kb/{ns}/documents?{q}",
                                    body=path.read_bytes(),
                                    headers={"Content-Type": "text/markdown; charset=utf-8"})
            if code not in (200, 201):
                raise RuntimeError(f"上传 {path.name} 失败 {code}: {str(body)[:200]}")
            done.append(path.name)
        return done

    def ask(self, case: Mapping[str, Any]) -> dict[str, Any] | None:
        payload: dict[str, Any] = {"question": case.get("question"), "top_k": 5}
        if self.namespace:
            payload["namespace"] = self.namespace
        if self.acl:
            payload["acl"] = self.acl
        if self.event_time:
            payload["event_time"] = self.event_time
        # ★ 会话级评测靠它把多轮串成一条轨迹；单题评测不带这个键，行为不变。
        if case.get("session_id"):
            payload["session_id"] = case.get("session_id")
        code, body = self._call("POST", "/chat",
                                body=json.dumps(payload).encode("utf-8"),
                                headers={"Content-Type": "application/json"})
        if self.pace:
            time.sleep(self.pace)
        if code != 200:
            return {"error": f"HTTP {code}", "knowledge_hit": False,
                    "detail": str(body)[:300]}
        return body


class InProcTransport:
    """直连 `service.handle_chat`（无 HTTP、无限流）。

    ★ 与 HTTP 那条**共用同一个管道**（`handle_chat` 的 docstring 就写了"直连调用"是它的
      合法用法），所以这里的结论与线上同源，不是另写一套。
    ★ 需要服务已 `rag.startup()`（模型加载）；测试里可以用假模型替换 `svc.rag`。
    """

    name = "inproc"

    def __init__(self, *, namespace: str | None = None, event_time: str | None = None):
        self.namespace = namespace
        self.event_time = event_time

    def ask(self, case: Mapping[str, Any]) -> dict[str, Any] | None:
        import service as svc                      # 延迟导入：只有这个模式需要它

        req = svc.ChatRequest(question=case.get("question"),
                              namespace=self.namespace,
                              event_time=self.event_time,
                              session_id=case.get("session_id"))
        resp = svc.handle_chat(req)
        return resp.model_dump() if hasattr(resp, "model_dump") else dict(resp)


def make_transport(mode: str, **kw: Any) -> Transport:
    if mode == "recorded":
        return RecordedTransport.from_file(kw["responses"])
    if mode == "inproc":
        return InProcTransport(namespace=kw.get("namespace"), event_time=kw.get("event_time"))
    if mode == "http":
        return HttpTransport(kw["base_url"], namespace=kw.get("namespace"),
                             acl=kw.get("acl"), event_time=kw.get("event_time"),
                             pace=kw.get("pace", 0.0))
    raise ValueError(f"未知 transport={mode!r}（可选 http/recorded/inproc）")
