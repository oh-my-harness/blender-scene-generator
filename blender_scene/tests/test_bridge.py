import asyncio
import struct
import json
import threading
import socket
from blender_scene.bridge import BlenderBridge


def start_mock_server(host, port, handler):
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host, port))
    server.listen(1)

    def serve():
        conn, _ = server.accept()
        len_buf = conn.recv(4)
        if len(len_buf) < 4:
            conn.close(); return
        msg_len = struct.unpack(">I", len_buf)[0]
        payload = b""
        while len(payload) < msg_len:
            chunk = conn.recv(msg_len - len(payload))
            if not chunk: break
            payload += chunk
        request = json.loads(payload)
        response = handler(request)
        resp_payload = json.dumps(response).encode()
        conn.sendall(struct.pack(">I", len(resp_payload)) + resp_payload)
        conn.close()

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    return server, t


def test_bridge_send_receives_response():
    host, port = "127.0.0.1", 19999
    def handler(request):
        assert request["action"] == "add_object"
        assert request["params"]["type"] == "cube"
        return {"ok": True, "data": {"created": "cube_001"}}
    server, thread = start_mock_server(host, port, handler)
    async def run():
        bridge = BlenderBridge(host, port)
        return await bridge.send("add_object", {"type": "cube"})
    result = asyncio.run(run())
    assert result == {"created": "cube_001"}
    server.close(); thread.join(timeout=2)


def test_bridge_send_error_response():
    host, port = "127.0.0.1", 19998
    def handler(request):
        return {"ok": False, "error": "object not found"}
    server, thread = start_mock_server(host, port, handler)
    async def run():
        bridge = BlenderBridge(host, port)
        try:
            await bridge.send("delete_object", {"name": "nonexistent"})
            return None
        except Exception as e:
            return str(e)
    result = asyncio.run(run())
    assert "object not found" in result
    server.close(); thread.join(timeout=2)
