"""BlenderBridge — TCP connection to the Blender addon.

Frame format: 4-byte big-endian length prefix + JSON payload.
Request:  {"action": "...", "params": {...}}
Response: {"ok": true, "data": {...}} or {"ok": false, "error": "..."}

桥梁模块：与 Blender 插件之间的 TCP 连接。
帧格式：4 字节大端长度前缀 + JSON 负载。

Uses a synchronous socket + threading.Lock instead of asyncio, because
the runtime executes Python tool callbacks via spawn_blocking + asyncio.run,
which creates a new event loop per call. asyncio objects (Lock, StreamReader,
StreamWriter) cannot be shared across event loops. A sync socket + threading.Lock
is loop-agnostic and safe to call from any thread.
"""

import json
import socket
import struct
import threading
from typing import Any


# 64 MB — 与 Rust 端 MAX_FRAME 保持一致
MAX_FRAME = 64 * 1024 * 1024


class BlenderBridge:
    """Persistent TCP connection to the Blender addon.

    与 Blender 插件的持久 TCP 连接。
    使用同步 socket + threading.Lock，不依赖 asyncio。
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 9876):
        self._host = host
        self._port = port
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()

    def connect(self) -> None:
        """连接到 Blender 插件的 TCP 服务器 / Connect to the addon's TCP server."""
        self._sock = socket.create_connection((self._host, self._port))

    def send(self, action: str, params: Any) -> Any:
        """Send a JSON command and wait for the JSON response.

        发送 JSON 命令并等待 JSON 响应。
        帧格式：4 字节大端长度前缀 + JSON 负载。
        锁跨写入和读取持有，使请求与响应不会被并发调用者拆分。
        """
        with self._lock:
            if self._sock is None:
                self._sock = socket.create_connection((self._host, self._port))
            request = json.dumps({"action": action, "params": params}).encode()
            len_prefix = struct.pack(">I", len(request))
            self._sock.sendall(len_prefix + request)
            len_buf = self._recv_exactly(4)
            resp_len = struct.unpack(">I", len_buf)[0]
            if resp_len > MAX_FRAME:
                raise RuntimeError(f"frame too large: {resp_len} bytes")
            resp_buf = self._recv_exactly(resp_len)
            resp = json.loads(resp_buf)
        if resp.get("ok", False):
            return resp.get("data", None)
        else:
            error = resp.get("error", "unknown error")
            raise RuntimeError(f"blender error: {error}")

    def _recv_exactly(self, n: int) -> bytes:
        """Read exactly n bytes from the socket."""
        data = b""
        while len(data) < n:
            chunk = self._sock.recv(n - len(data))
            if not chunk:
                raise ConnectionError("connection closed by Blender addon")
            data += chunk
        return data

    async def send_async(self, action: str, params: Any) -> Any:
        """Async wrapper for send. Runs the sync call in a thread.

        异步包装器：在线程中执行同步调用。
        """
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.send, action, params)

    def close(self) -> None:
        """关闭连接 / Close the connection."""
        with self._lock:
            if self._sock is not None:
                self._sock.close()
                self._sock = None
