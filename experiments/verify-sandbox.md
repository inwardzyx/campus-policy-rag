# 代码执行沙箱：四种越界实测（第五份产物）

> ★ 本文件由 `scripts/verify_sandbox.py` 生成，**不要手改**（下次生成会覆盖）。
> 生成时间：2026-11-16 14:39:13　用时 7 秒
> 镜像：`python:3.12-slim`（id `sha256:a6e34c598f24`）

## 一句话结论

六个探针全部符合期望：**正常计算能算、四种越界全被拦住**；另有一条结构证据（`/` 挂载为 ro）。

## 限制是怎么下的（同一条命令，五个探针共用）

```text
docker run --rm -i --network=none --read-only --memory=128m --cpus=0.5 --pids-limit=64 -u 65534 --tmpfs /tmp:size=16m python:3.12-slim python -
```

| 限制 | 拦的是什么 |
|---|---|
| `--network=none` | 外发数据、下载脚本 |
| `--read-only` + `--tmpfs /tmp:size=16m` | 改系统文件；只能写 16MB 临时区 |
| `--memory=128m` | 一次分配 400MB 会 OOM |
| `--cpus=0.5` / `--pids-limit=64` | CPU 打满、fork 炸弹 |
| `-u 65534` | 以 nobody 运行，不是 root |

## 六个探针（5 个行为探针 + 1 条结构证据）

| 探针 | 期望 | 实测 | 通过 | 为什么测它 |
|---|---|---|---|---|
| 正常计算 | `ok` | `ok`（248 ms） | ✅ | 能算数：这条链路的用处（查条款 → 算数字） |
| 超时 | `timeout` | `timeout`（5018 ms） | ✅ | 死循环必须被杀（不挂住 agent 循环） |
| 内存 | `oom` | `oom`（673 ms） | ✅ | 超 128MB 必须失败（不拖垮宿主机） |
| 网络 | `network_blocked` | `network_blocked`（660 ms） | ✅ | 断网：外发数据 / 下载脚本都不行 |
| 写盘 | `write_blocked` | `write_blocked`（317 ms） | ✅ | 改不了系统文件（EACCES 或 EROFS，报告里保留原文） |
| 根挂载只读 | `ok` | `ok`（333 ms） | ✅ | 结构证据：`/` 的挂载选项里必须带 ro |

## 每个探针的代码与原始输出

### 正常计算（期望 `ok`，实测 `ok`）

代码：
```python
print(round((5000/21.75)*3, 2))
```

stdout（0）：
```text
689.66
```
stderr（截断到 300 字）：
```text
（空）
```

### 超时（期望 `timeout`，实测 `timeout`）

代码：
```python
while True:
    pass
```

stdout（None）：
```text
（空）
```
stderr（截断到 300 字）：
```text
（空）
```

### 内存（期望 `oom`，实测 `oom`）

代码：
```python
x = bytearray(400 * 1024 * 1024)
print(len(x))
```

stdout（137）：
```text
（空）
```
stderr（截断到 300 字）：
```text
（空）
```

### 网络（期望 `network_blocked`，实测 `network_blocked`）

代码：
```python
import urllib.request
urllib.request.urlopen('http://example.com', timeout=2).read()
```

stdout（1）：
```text
（空）
```
stderr（截断到 300 字）：
```text
Traceback (most recent call last):
  File "/usr/local/lib/python3.12/urllib/request.py", line 1357, in do_open
    h.request(req.get_method(), req.selector, req.data, headers,
  File "/usr/local/lib/python3.12/http/client.py", line 1386, in request
    self._send_request(method, url, body, headers, …
```

### 写盘（期望 `write_blocked`，实测 `write_blocked`）

代码：
```python
open('/etc/passwd', 'a').write('x')
```

stdout（1）：
```text
（空）
```
stderr（截断到 300 字）：
```text
Traceback (most recent call last):
  File "<stdin>", line 1, in <module>
PermissionError: [Errno 13] Permission denied: '/etc/passwd'
```

### 根挂载只读（期望 `ok`，实测 `ok`）

代码：
```python
print([l for l in open('/proc/mounts') if ' / ' in l][0].strip())
```

stdout（0）：
```text
overlay / overlay ro,relatime,lowerdir=/var/lib/desktop-containerd/daemon/io.containerd.snapshotter.v1.overlayfs/snapshots/590/fs:/var/lib/desktop-containerd/daemon/io.containerd.snapshotter.v1.overlayfs/snapshots/187/fs:/var/lib/desktop-containerd/daemon/io.containerd.snapshotter.v1.overlayfs/snaps…
```
stderr（截断到 300 字）：
```text
（空）
```

## 判定规则（`agent/sandbox.py::classify`）

| 判定 | 依据 |
|---|---|
| `timeout` | 进程超时（`subprocess.TimeoutExpired`） |
| `oom` | 退出码 137 / `MemoryError` / `Killed` |
| `network_blocked` | DNS 失败 / `Network is unreachable` / `Connection refused` |
| `write_blocked` | `Read-only file system` |
| `ok` | 退出码 0 |
| `error` | 其它（**不当成功**） |

