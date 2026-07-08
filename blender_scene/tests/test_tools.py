"""Tests for the 12 Blender TCP-bridge tools.

12 个 Blender TCP 桥接工具的测试。
"""
import json
import struct
import threading
import socket

import llm_harness_py as lh
from blender_scene.bridge import BlenderBridge
from blender_scene.tools import all_blender_tools, ALL_TOOL_NAMES, TOOL_SPECS


def test_all_tool_names_count():
    """ALL_TOOL_NAMES must contain exactly 12 entries."""
    assert len(ALL_TOOL_NAMES) == 12


def test_all_blender_tools_returns_list():
    """all_blender_tools returns 12 tools, each with name and description."""
    bridge = BlenderBridge()
    tools = all_blender_tools(bridge)
    assert len(tools) == 12
    for tool in tools:
        assert hasattr(tool, "name")
        assert hasattr(tool, "description")


def test_tool_names_match_expected():
    """Tool names must match the 12 specs from registry.rs."""
    expected = {
        "add_object", "set_material", "add_light", "set_camera",
        "get_scene_state", "viewport_refresh", "delete_object",
        "update_object", "execute_python", "boolean_modify",
        "extrude_shape", "add_curve",
    }
    assert set(ALL_TOOL_NAMES) == expected


def test_tool_descriptions_nonempty():
    """Every tool must have a non-empty description."""
    for spec in TOOL_SPECS:
        assert spec["description"], f"{spec['name']} has empty description"


def test_transform_empty_flags():
    """get_scene_state and viewport_refresh must have transform_empty=True."""
    by_name = {s["name"]: s for s in TOOL_SPECS}
    assert by_name["get_scene_state"]["transform_empty"] is True
    assert by_name["viewport_refresh"]["transform_empty"] is True
    # All others must be False
    for name in ALL_TOOL_NAMES:
        if name not in ("get_scene_state", "viewport_refresh"):
            assert by_name[name]["transform_empty"] is False, f"{name} should be False"


def test_schemas_are_valid_json():
    """Each schema must be a valid JSON string parseable into a dict with type=object."""
    for spec in TOOL_SPECS:
        schema = json.loads(spec["schema_json"])
        assert schema["type"] == "object"
        assert "properties" in schema
        assert "required" in schema


def test_add_object_schema_matches_rust():
    """SCHEMA_0 must match registry.rs exactly."""
    by_name = {s["name"]: s for s in TOOL_SPECS}
    schema = json.loads(by_name["add_object"]["schema_json"])
    props = schema["properties"]
    assert "type" in props
    assert props["type"]["type"] == "string"
    assert set(props["type"]["enum"]) == {"cube", "sphere", "cylinder", "plane", "cone", "torus"}
    assert props["location"]["minItems"] == 3
    assert props["location"]["maxItems"] == 3
    assert schema["required"] == ["type"]


def test_boolean_modify_schema_matches_rust():
    """SCHEMA_9 must match registry.rs exactly."""
    by_name = {s["name"]: s for s in TOOL_SPECS}
    schema = json.loads(by_name["boolean_modify"]["schema_json"])
    props = schema["properties"]
    assert set(schema["required"]) == {"target", "operation", "cutter"}
    assert set(props["operation"]["enum"]) == {"union", "difference", "intersect"}
    assert props["cutter"]["properties"]["type"]["enum"] == [
        "cube", "sphere", "cylinder", "plane", "cone", "torus"
    ]
    assert props["cutter"]["required"] == ["type"]


def test_extrude_shape_schema_matches_rust():
    """SCHEMA_10 must match registry.rs exactly."""
    by_name = {s["name"]: s for s in TOOL_SPECS}
    schema = json.loads(by_name["extrude_shape"]["schema_json"])
    props = schema["properties"]
    assert schema["required"] == ["profile"]
    assert props["profile"]["minItems"] == 3
    assert props["axis"]["enum"] == ["X", "Y", "Z"]


def test_add_curve_schema_matches_rust():
    """SCHEMA_11 must match registry.rs exactly."""
    by_name = {s["name"]: s for s in TOOL_SPECS}
    schema = json.loads(by_name["add_curve"]["schema_json"])
    props = schema["properties"]
    assert schema["required"] == ["points"]
    assert props["points"]["minItems"] == 2


# --- Integration: tool callback actually calls the bridge ---

def _start_mock_server(host, port, handler):
    """Start a minimal mock Blender TCP server."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host, port))
    server.listen(1)

    def serve():
        conn, _ = server.accept()
        len_buf = conn.recv(4)
        if len(len_buf) < 4:
            conn.close()
            return
        msg_len = struct.unpack(">I", len_buf)[0]
        payload = b""
        while len(payload) < msg_len:
            chunk = conn.recv(msg_len - len(payload))
            if not chunk:
                break
            payload += chunk
        request = json.loads(payload)
        response = handler(request)
        resp_payload = json.dumps(response).encode()
        conn.sendall(struct.pack(">I", len(resp_payload)) + resp_payload)
        conn.close()

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    return server, t


def test_tool_callback_calls_bridge():
    """The add_object tool callback must send action+params to the bridge and return a ToolResult dict."""
    host, port = "127.0.0.1", 29901

    def handler(request):
        assert request["action"] == "add_object"
        assert request["params"]["type"] == "cube"
        return {"ok": True, "data": {"created": "cube_001"}}

    server, thread = _start_mock_server(host, port, handler)
    bridge = BlenderBridge(host, port)
    tools = all_blender_tools(bridge)
    add_obj_tool = next(t for t in tools if t.name == "add_object")

    # drive() is synchronous: lh.create_tool runs the async callback
    # via asyncio.run() on a spawn_blocking thread internally.
    result = add_obj_tool.drive({"type": "cube", "name": "my_cube"})
    server.close()
    thread.join(timeout=2)

    assert result["terminate"] is False
    assert result["details"] == {"created": "cube_001"}
    # content must be a list with a text block
    text = result["content"][0]["text"]
    parsed = json.loads(text)
    assert parsed["action"] == "add_object"
    assert parsed["result"] == {"created": "cube_001"}


def test_transform_empty_tool_sends_empty_params():
    """get_scene_state must send {} params to the bridge (transform_empty workaround)."""
    host, port = "127.0.0.1", 29902

    captured = {}

    def handler(request):
        captured["action"] = request["action"]
        captured["params"] = request["params"]
        return {"ok": True, "data": {"objects": []}}

    server, thread = _start_mock_server(host, port, handler)
    bridge = BlenderBridge(host, port)
    tools = all_blender_tools(bridge)
    get_state_tool = next(t for t in tools if t.name == "get_scene_state")

    # drive() is synchronous: lh.create_tool runs the async callback
    # via asyncio.run() on a spawn_blocking thread internally.
    get_state_tool.drive({"include_transforms": True})
    server.close()
    thread.join(timeout=2)

    assert captured["action"] == "get_scene_state"
    assert captured["params"] == {}
