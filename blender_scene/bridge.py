"""BlenderBridge — TCP connection to the Blender addon.

Frame format: 4-byte big-endian length prefix + JSON payload.
Request:  {"action": "...", "params": {...}}
Response: {"ok": true, "data": {...}} or {"ok": false, "error": "..."}

桥梁模块：与 Blender 插件之间的持久 TCP 连接。
帧格式：4 字节大端长度前缀 + JSON 负载。
"""

import asyncio
import struct
import json
from typing import Any


# 64 MB — 与 Rust 端 MAX_FRAME 保持一致
MAX_FRAME = 64 * 1024 * 1024


class BlenderBridge:
    """Persistent TCP connection to the Blender addon.

    The underlying socket is guarded by an asyncio.Lock — the framing
    protocol interleaves write+read and concurrent sends would corrupt
    the stream. Serialization is a protocol invariant, not a convenience,
    and must live inside the bridge rather than at every call site.

    与 Blender 插件的持久 TCP 连接。
    底层 socket 由 asyncio.Lock 保护——帧协议将写入与读取交替进行，
    并发 send 会把一个请求与另一个调用者的响应配对，从而破坏流。
    因此串行化是协议不变量，必须放在桥内部，而非每个调用点。
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 9876):
        self._host = host
        self._port = port
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        """连接到 Blender 插件的 TCP 服务器 / Connect to the addon's TCP server."""
        self._reader, self._writer = await asyncio.open_connection(self._host, self._port)

    async def send(self, action: str, params: Any) -> Any:
        """Send a JSON command and wait for the JSON response.

        发送 JSON 命令并等待 JSON 响应。
        帧格式：4 字节大端长度前缀 + JSON 负载。
        锁跨写入和读取持有，使请求与响应不会被并发调用者拆分。
        """
        if self._writer is None or self._reader is None:
            await self.connect()
        request = json.dumps({"action": action, "params": params}).encode()
        len_prefix = struct.pack(">I", len(request))
        async with self._lock:
            assert self._writer is not None and self._reader is not None
            self._writer.write(len_prefix + request)
            await self._writer.drain()
            len_buf = await self._reader.readexactly(4)
            resp_len = struct.unpack(">I", len_buf)[0]
            if resp_len > MAX_FRAME:
                raise RuntimeError(f"frame too large: {resp_len} bytes")
            resp_buf = await self._reader.readexactly(resp_len)
            resp = json.loads(resp_buf)
        if resp.get("ok", False):
            return resp.get("data", None)
        else:
            error = resp.get("error", "unknown error")
            raise RuntimeError(f"blender error: {error}")

    async def close(self) -> None:
        """关闭连接 / Close the connection."""
        if self._writer is not None:
            self._writer.close()
            await self._writer.wait_closed()
            self._writer = None
            self._reader = None
