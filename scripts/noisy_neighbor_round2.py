# -*- coding: utf-8 -*-
"""noisy neighbor 第二轮工具：起服务 → 校验监听者 → 四阶段测量 → 清场；外加 /health 对照探针。

## 为什么不用第一轮的 `scripts/noisy_neighbor.py`

第一轮那个脚本只测"B 的延迟"，两个问题在第二轮才暴露出来（都写在
`experiments/noisy-neighbor.md` 第九节）：

1. **它会覆盖正式产物**（固定输出路径）—— 已踩过（HANDOFF 坑 12）；
2. **它分不出"CPU 被吃"和"进程被排队"**：噪音期 B 慢 5 倍，但 CPU 只到 57%。
   ⇒ 这里加了一路 `/health` 对照探针：轻接口也慢 ⇒ 是整个进程在排队；只有 /search 慢
   ⇒ 才是 CPU / 检索路径的问题。

## 三个子命令

```powershell
# ① 一条臂 = 起服务 + 校验监听者身份 + 四阶段测量 + 清场（防"打到旧进程上"）
python scripts/noisy_neighbor_round2.py arm --name R1 --port 8060 --workers 1 `
  --env ADMIT_MODE=early --env GLOBAL_CONC=4 --env CONC_WAIT_MS=250 --env EMBED_THREADS=3

# ② 只测一个已经在跑的服务
python scripts/noisy_neighbor_round2.py measure --port 8060 --mode split --out x.json

# ③ /health 对照探针（噪音期里轻接口慢不慢）
python scripts/noisy_neighbor_round2.py diag --port 8060 --retry-sleep 0.2
```

## 为什么 `arm` 要核对"监听者 pid 的创建时间"

uvicorn 的 `--workers` 父子结构 + Windows 上杀不干净，会让上一轮的进程继续占着端口：
新服务 bind 失败（日志里只有一行 `WinError 10048`），而测量**打到旧进程上照样出数**。
第二轮就因此作废了两条数据（见报告第九节的"作废"清单）。这条校验是为了让那种事**报错**，
而不是安静地产生一组漂亮的假数据。
"""
from __future__ import annotations

import argparse
import http.client
import json
import multiprocessing as mp
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO / "experiments" / "noisy-neighbor-round2"
QUESTION = "宿舍里使用违规电器会挨什么处分？"
BODY = json.dumps({"question": QUESTION}).encode("utf-8")
HDR = {"Content-Type": "application/json", "X-Auth-Roles": "public"}


# ----------------------------------------------------------------------
# 客户端侧：一个 loader / 一段四阶段测量
# ----------------------------------------------------------------------
def one(conn, client_id: str) -> tuple[int, float]:
    t0 = time.perf_counter()
    try:
        h = dict(HDR)
        h["X-Client-Id"] = client_id
        conn.request("POST", "/search", BODY, h)
        r = conn.getresponse()
        r.read()
        return r.status, (time.perf_counter() - t0) * 1000
    except Exception:
        return 0, (time.perf_counter() - t0) * 1000


def _loop(port: int, client_id: str, sink: list, stop, retry_sleep: float, path: str = "/search") -> None:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=120)
    while not stop.is_set():
        if path == "/health":
            t0 = time.perf_counter()
            try:
                conn.request("GET", path)
                r = conn.getresponse()
                r.read()
                code = r.status
            except Exception:
                code = 0
                try:
                    conn.close()
                except Exception:
                    pass
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=120)
            sink.append({"client": client_id, "at": time.time(), "code": code,
                         "ms": round((time.perf_counter() - t0) * 1000, 1)})
            time.sleep(0.01)
            continue
        code, ms = one(conn, client_id)
        sink.append({"client": client_id, "at": time.time(), "ms": round(ms, 1), "code": code})
        if code == 0:
            try:
                conn.close()
            except Exception:
                pass
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=120)
        elif code == 429 and retry_sleep > 0:
            stop.wait(retry_sleep)          # ★ 守规矩的客户端：尊重 Retry-After
            continue
        time.sleep(0.01)
    try:
        conn.close()
    except Exception:
        pass


def _a_proc(port: int, nthreads: int, stop_ev, path: str, retry_sleep: float) -> None:
    """子进程里的噪音源：数据直接落盘（不走队列，避免把进程间通信算进延迟）。"""
    rows: list = []
    never = threading.Event()
    ts = [threading.Thread(target=_loop, args=(port, "tenant-a", rows, never, retry_sleep), daemon=True)
          for _ in range(nthreads)]
    for t in ts:
        t.start()
    while not stop_ev.is_set():
        time.sleep(0.2)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, int(round((p / 100) * (len(xs) - 1)))))
    return round(xs[k], 1)


def summarize(rows: list[dict], label: str) -> dict:
    lat = [r["ms"] for r in rows if r["code"] == 200]
    codes: dict = {}
    for r in rows:
        codes[str(r["code"])] = codes.get(str(r["code"]), 0) + 1
    return {"phase": label, "n": len(rows), "n_ok": len(lat),
            "p50": pct(lat, 50), "p95": pct(lat, 95), "p99": pct(lat, 99), "codes": codes}


def measure(port: int, *, a_conc: int, a_procs: int, b_conc: int, phase_secs: float,
            mode: str, a_retry_sleep: float, out: Path, tag: str) -> dict:
    """四阶段：B 预热 → ①B 单独 → ②A 加入（B 不停）→ ③A 停。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    b_rows: list[dict] = []
    b_stop = threading.Event()
    for _ in range(b_conc):
        threading.Thread(target=_loop, args=(port, "tenant-b", b_rows, b_stop, 0.0),
                         daemon=True).start()

    time.sleep(5)
    t0 = time.time()
    time.sleep(phase_secs)                       # ① 基线
    t1 = time.time()

    a_rows: list[dict] = []
    procs: list[mp.Process] = []
    stop_ev = mp.Event()
    a_stop = threading.Event()
    if mode == "split":
        per = max(1, a_conc // a_procs)
        for i in range(a_procs):
            p = mp.Process(target=_a_proc,
                           args=(port, per, stop_ev, str(out.with_name(f"_a{i}_{out.stem}.jsonl")),
                                 a_retry_sleep))
            p.start()
            procs.append(p)
    else:
        for _ in range(a_conc):
            threading.Thread(target=_loop, args=(port, "tenant-a", a_rows, a_stop, a_retry_sleep),
                             daemon=True).start()

    time.sleep(phase_secs)                       # ② 噪音
    t2 = time.time()

    if mode == "split":
        stop_ev.set()
        for p in procs:
            p.join(timeout=20)
        for i in range(a_procs):
            f = out.with_name(f"_a{i}_{out.stem}.jsonl")
            if f.exists():
                for line in f.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        a_rows.append(json.loads(line))
                f.unlink()
    else:
        a_stop.set()
        time.sleep(1)

    time.sleep(3)
    time.sleep(max(5.0, phase_secs - 3))
    t3 = time.time()
    b_stop.set()
    time.sleep(2)

    def pick(rows, lo, hi, cid):
        return [r for r in rows if lo <= r["at"] < hi and r["client"] == cid]

    phases = {
        "1_基线_B单独": summarize(pick(b_rows, t0, t1, "tenant-b"), "1_基线_B单独"),
        "2_噪音_A打满": summarize(pick(b_rows, t1, t2, "tenant-b"), "2_噪音_A打满"),
        "3_恢复_A停": summarize(pick(b_rows, t2, t3, "tenant-b"), "3_恢复_A停"),
        "A_自身": summarize(pick(a_rows, t1, t2, "tenant-a"), "A_自身"),
    }
    base = phases["1_基线_B单独"]["p95"]
    noisy = phases["2_噪音_A打满"]["p95"]
    degrade = ((noisy - base) / base * 100) if base else 0.0

    out.write_text(json.dumps({"tag": tag, "mode": mode,
                               "config": {"port": port, "a_conc": a_conc, "a_procs": a_procs,
                                          "b_conc": b_conc, "phase_secs": phase_secs,
                                          "a_retry_sleep": a_retry_sleep},
                               "phases": phases, "degradation_pct": round(degrade, 1)},
                              ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\n### {tag or out.stem}  [mode={mode}]")
    print(f"{'阶段':<16}{'n':>8}{'ok':>8}{'p50':>9}{'p95':>9}{'p99':>9}  状态码")
    for k in ("1_基线_B单独", "2_噪音_A打满", "3_恢复_A停", "A_自身"):
        s = phases[k]
        print(f"{k:<16}{s['n']:>8}{s['n_ok']:>8}{s['p50']:>9.1f}{s['p95']:>9.1f}"
              f"{s['p99']:>9.1f}  {s['codes']}")
    print(f"B 的 p95：{base}ms → {noisy}ms（{degrade:+.1f}%） | 写 {out}")
    return {"phases": phases, "degradation_pct": round(degrade, 1)}


# ----------------------------------------------------------------------
# 服务端侧：起实例 + 校验监听者 + 清场
# ----------------------------------------------------------------------
EXP_ENV = {
    "ALLOW_CLIENT_ID_HEADER": "1", "TRUST_PROXY_HEADERS": "1", "AUTH_MODE": "proxy",
    "RATE_BURST": "1000000", "RATE_PER_MIN": "1000000",
    "DAILY_REQ_QUOTA": "0", "DAILY_TOKEN_QUOTA": "0",
    "GLOBAL_DAILY_REQ_QUOTA": "0", "GLOBAL_DAILY_TOKEN_QUOTA": "0",
}


def _experiment_procs() -> list:
    try:
        import psutil
    except ImportError:
        return []
    me = os.getpid()
    out = []
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = (p.info["name"] or "").lower()
            cmd = " ".join(p.info["cmdline"] or [])
        except Exception:
            continue
        # ★ 必须限定进程名并排除自己：launcher/diagnostic 的命令行里也含这些字样
        if not name.startswith("python") or p.pid == me:
            continue
        if "service:app" in cmd and "-m uvicorn" in cmd:
            out.append(p)
    return out


def kill_experiment_procs(rounds: int = 6) -> int:
    n = 0
    for _ in range(rounds):
        ps = _experiment_procs()
        if not ps:
            return n
        for p in ps:
            try:
                p.kill()
                n += 1
            except Exception:
                pass
        time.sleep(2)
    return n


def _kill_tree(pid: int) -> None:
    """按进程树杀：`--workers` 的 worker 是子进程，只杀父会留孤儿占端口、吃 CPU。"""
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True, text=True, timeout=30)
    except Exception:
        try:
            import psutil
            psutil.Process(pid).kill()
        except Exception:
            pass


def _listener_pid(port: int) -> int | None:
    import psutil
    for conn in psutil.net_connections(kind="inet"):
        if conn.status == "LISTEN" and conn.laddr and conn.laddr.port == port:
            return conn.pid
    return None


def serve_and_measure(a) -> int:
    import psutil

    killed = kill_experiment_procs()
    time.sleep(2)
    idle = psutil.cpu_percent(interval=2)
    print(f"[{a.name}] 清场杀掉 {killed} 个；空闲 CPU = {idle:.1f}%")
    if idle > a.max_idle_cpu:
        print(f"[{a.name}] ✗ 空闲 CPU {idle:.1f}% > {a.max_idle_cpu:.0f}%：机器被别人占着，"
              f"测出来的差值不可信，先停")
        return 2

    env = dict(os.environ)
    env.update(EXP_ENV)
    for item in a.env:
        k, _, v = item.partition("=")
        env[k] = v

    out = Path(a.out) if a.out else DEFAULT_OUT / f"{a.name}.json"
    log = Path(a.log) if a.log else out.with_suffix(".log")
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "uvicorn", "service:app", "--host", "127.0.0.1",
           "--port", str(a.port), "--log-level", "warning"]
    if a.workers > 1:
        cmd += ["--workers", str(a.workers)]
    t_start = time.time()
    fh = open(log, "w", encoding="utf-8", errors="replace")
    server = subprocess.Popen(cmd, cwd=str(REPO), env=env, stdout=fh, stderr=subprocess.STDOUT)

    ready = False
    deadline = time.time() + a.boot_timeout
    while time.time() < deadline:
        if server.poll() is not None:
            print(f"[{a.name}] ✗ 服务进程退出（exit {server.returncode}），日志尾：")
            print("".join(open(log, encoding="utf-8", errors="replace").readlines()[-12:]))
            return 3
        try:
            c = http.client.HTTPConnection("127.0.0.1", a.port, timeout=5)
            c.request("GET", "/health")
            r = c.getresponse()
            r.read()
            c.close()
            if r.status == 200:
                ready = True
                break
        except Exception:
            pass
        time.sleep(3)
    if not ready:
        print(f"[{a.name}] ✗ {a.boot_timeout} 秒未就绪")
        _kill_tree(server.pid)
        return 4

    # ★ 核对监听者身份：pid 必须是本次启动之后创建的（否则打到了旧进程上）
    pid = _listener_pid(a.port)
    if pid is None:
        print(f"[{a.name}] ✗ 端口 {a.port} 上没有监听者")
        _kill_tree(server.pid)
        return 5
    try:
        ct = psutil.Process(pid).create_time()
    except Exception:
        ct = 0
    if ct < t_start - 5:
        print(f"[{a.name}] ✗ 监听者 pid {pid} 早于本次启动 —— 打到了旧进程上，拒绝测")
        _kill_tree(server.pid)
        return 6
    print(f"[{a.name}] 就绪 {time.time() - t_start:.0f}s；监听 pid={pid}（本次创建 ✓）")

    try:
        measure(a.port, a_conc=a.a_conc, a_procs=a.a_procs, b_conc=a.b_conc,
                phase_secs=a.phase_secs, mode=a.mode, a_retry_sleep=a.a_retry_sleep,
                out=out, tag=a.name)
    finally:
        _kill_tree(server.pid)
        fh.close()
        time.sleep(2)
        kill_experiment_procs()
    return 0


# ----------------------------------------------------------------------
# /health 对照探针
# ----------------------------------------------------------------------
def diag(a) -> int:
    health: list[dict] = []
    stop_h = threading.Event()
    threading.Thread(target=_loop, args=(a.port, "victim", health, stop_h, 0.0, "/health"),
                     daemon=True).start()
    time.sleep(10)                                  # 噪音前
    t1 = time.time()
    ps = [mp.Process(target=_a_proc, args=(a.port, a.threads, mp.Event(), os.devnull, a.retry_sleep))
          for _ in range(a.procs)]
    for p in ps:
        p.start()
    time.sleep(a.noise_secs)                        # 噪音中
    t2 = time.time()
    for p in ps:
        p.join(timeout=20)
    time.sleep(10)                                  # 噪音后
    t3 = time.time()
    stop_h.set()
    time.sleep(1)

    def band(lo, hi):
        xs = [r["ms"] for r in health if lo <= r["at"] < hi and r["code"] == 200]
        return {"n": len(xs), "p50": pct(xs, 50), "p95": pct(xs, 95)}

    b1, b2, b3 = band(0, t1), band(t1, t2), band(t2, t3 + 1)
    ratio = (b2["p50"] / b1["p50"]) if b1["p50"] else 0.0
    print(f"\n### {a.tag or 'diag'}（{a.procs}×{a.threads} 并发打 /search，同时量 /health）")
    print(f"{'阶段':<10}{'n':>6}{'/health p50':>14}{'/health p95':>14}")
    for label, b in (("噪音前", b1), ("噪音中", b2), ("噪音后", b3)):
        print(f"{label:<10}{b['n']:>6}{b['p50']:>14.1f}{b['p95']:>14.1f}")
    print(f"/health p50 在噪音期放大 {ratio:.1f}×")
    if a.out:
        Path(a.out).write_text(json.dumps(
            {"tag": a.tag, "procs": a.procs, "threads": a.threads,
             "noise_secs": a.noise_secs, "retry_sleep": a.retry_sleep,
             "before": b1, "during": b2, "after": b3,
             "health_amplification": round(ratio, 1)}, ensure_ascii=False, indent=1),
            encoding="utf-8")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="noisy neighbor 第二轮：多 worker / 全局预算 / 早拒 / 退避")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, *, with_serve: bool):
        p.add_argument("--port", type=int, default=8060)
        p.add_argument("--out", default="")
        if with_serve:
            p.add_argument("--name", required=True)
            p.add_argument("--workers", type=int, default=1)
            p.add_argument("--env", action="append", default=[], help="KEY=VAL，可多次")
            p.add_argument("--log", default="")
            p.add_argument("--boot-timeout", type=float, default=480)
            p.add_argument("--max-idle-cpu", type=float, default=75)
        p.add_argument("--a-conc", type=int, default=48)
        p.add_argument("--a-procs", type=int, default=4)
        p.add_argument("--b-conc", type=int, default=2)
        p.add_argument("--phase-secs", type=float, default=60)
        p.add_argument("--a-retry-sleep", type=float, default=0.0)
        p.add_argument("--mode", choices=("same", "split"), default="split")

    common(sub.add_parser("arm"), with_serve=True)
    common(sub.add_parser("measure"), with_serve=False)

    pd = sub.add_parser("diag")
    pd.add_argument("--port", type=int, default=8060)
    pd.add_argument("--procs", type=int, default=4)
    pd.add_argument("--threads", type=int, default=12)
    pd.add_argument("--noise-secs", type=float, default=30)
    pd.add_argument("--retry-sleep", type=float, default=0.0)
    pd.add_argument("--out", default="")
    pd.add_argument("--tag", default="")

    a = ap.parse_args()
    if a.cmd == "arm":
        return serve_and_measure(a)
    if a.cmd == "diag":
        return diag(a)
    out = Path(a.out) if a.out else DEFAULT_OUT / "measure.json"
    measure(a.port, a_conc=a.a_conc, a_procs=a.a_procs, b_conc=a.b_conc,
            phase_secs=a.phase_secs, mode=a.mode, a_retry_sleep=a.a_retry_sleep,
            out=out, tag="")
    return 0


if __name__ == "__main__":
    mp.freeze_support()
    raise SystemExit(main())
