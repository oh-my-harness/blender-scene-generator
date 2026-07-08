"""12 Blender TCP-bridge tools.

Each tool wraps a BlenderBridge.send() call behind the llm_harness_py
Tool interface (name, description, drive). Schemas are translated
verbatim from REDACTEDregistry.rs (SCHEMA_0..SCHEMA_11).

12 个 Blender TCP 桥接工具。
每个工具将 BlenderBridge.send() 调用封装在 llm_harness_py 的 Tool 接口
（name, description, drive）之后。Schema 从 REDACTEDregistry.rs
（SCHEMA_0..SCHEMA_11）逐字翻译。
"""

import json
from typing import Any

import llm_harness_py as lh
from blender_scene.bridge import BlenderBridge


# ---------------------------------------------------------------------------
# Schemas — translated verbatim from registry.rs SCHEMA_0..SCHEMA_11.
# Each is a JSON string (lh.create_tool expects a JSON string).
# ---------------------------------------------------------------------------

_SCHEMA_0 = json.dumps({
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": ["cube", "sphere", "cylinder", "plane", "cone", "torus"]},
        "name": {"type": "string"},
        "location": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
        "scale": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
        "rotation": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
    },
    "required": ["type"],
})

_SCHEMA_1 = json.dumps({
    "type": "object",
    "properties": {
        "target": {"type": "string"},
        "color": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 4},
        "roughness": {"type": "number"},
        "metallic": {"type": "number"},
    },
    "required": ["target"],
})

_SCHEMA_2 = json.dumps({
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": ["point", "sun", "area", "spot"]},
        "name": {"type": "string"},
        "location": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
        "energy": {"type": "number"},
        "color": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
    },
    "required": ["type"],
})

_SCHEMA_3 = json.dumps({
    "type": "object",
    "properties": {
        "location": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
        "rotation": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
    },
    "required": [],
})

_SCHEMA_4 = json.dumps({
    "type": "object",
    "properties": {
        "include_transforms": {
            "type": "boolean",
            "description": "If true, include world-space transforms in the result. Default: false.",
        },
    },
    "required": [],
})

_SCHEMA_5 = json.dumps({
    "type": "object",
    "properties": {
        "force": {
            "type": "boolean",
            "description": "Force refresh even if no changes detected. Default: true.",
        },
    },
    "required": [],
})

_SCHEMA_6 = json.dumps({
    "type": "object",
    "properties": {
        "name": {"type": "string"},
    },
    "required": ["name"],
})

_SCHEMA_7 = json.dumps({
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "location": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
        "scale": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
        "rotation": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
    },
    "required": ["name"],
})

_SCHEMA_8 = json.dumps({
    "type": "object",
    "properties": {
        "code": {"type": "string"},
    },
    "required": ["code"],
})

_SCHEMA_9 = json.dumps({
    "type": "object",
    "properties": {
        "target": {"type": "string", "description": "Name of the existing mesh object to modify."},
        "operation": {"type": "string", "enum": ["union", "difference", "intersect"]},
        "cutter": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["cube", "sphere", "cylinder", "plane", "cone", "torus"]},
                "location": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
                "scale": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
                "rotation": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
            },
            "required": ["type"],
        },
    },
    "required": ["target", "operation", "cutter"],
})

_SCHEMA_10 = json.dumps({
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "profile": {
            "type": "array",
            "items": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
            "minItems": 3,
            "description": "Closed 2D polygon points [[x,y], ...].",
        },
        "depth": {"type": "number", "description": "Extrusion length."},
        "axis": {"type": "string", "enum": ["X", "Y", "Z"], "description": "Axis along which depth extends. Default: Z."},
        "location": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
        "rotation": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
    },
    "required": ["profile"],
})

_SCHEMA_11 = json.dumps({
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "points": {
            "type": "array",
            "items": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
            "minItems": 2,
            "description": "Control points [[x,y,z], ...].",
        },
        "bevel_depth": {"type": "number", "description": "Tube radius. Default: 0.1."},
        "location": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
        "rotation": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
    },
    "required": ["points"],
})


# ---------------------------------------------------------------------------
# ToolSpecs — (name, description, action, schema_json, transform_empty).
# Order MUST match registry.rs TOOL_SPECS.
# ---------------------------------------------------------------------------

TOOL_SPECS = [
    {
        "name": "add_object",
        "description": "Create a 3D primitive object in the Blender scene.",
        "action": "add_object",
        "schema_json": _SCHEMA_0,
        "transform_empty": False,
    },
    {
        "name": "set_material",
        "description": "Set PBR material on an existing object.",
        "action": "set_material",
        "schema_json": _SCHEMA_1,
        "transform_empty": False,
    },
    {
        "name": "add_light",
        "description": "Add a light source to the scene.",
        "action": "add_light",
        "schema_json": _SCHEMA_2,
        "transform_empty": False,
    },
    {
        "name": "set_camera",
        "description": "Set or update the scene camera position and rotation.",
        "action": "set_camera",
        "schema_json": _SCHEMA_3,
        "transform_empty": False,
    },
    {
        "name": "get_scene_state",
        "description": "Query the current list of objects in the Blender scene.",
        "action": "get_scene_state",
        "schema_json": _SCHEMA_4,
        # glm-5.2 streaming bug workaround: dummy param in schema, empty params to bridge
        "transform_empty": True,
    },
    {
        "name": "viewport_refresh",
        "description": "Force-refresh the Blender viewport to show recent changes.",
        "action": "viewport_refresh",
        "schema_json": _SCHEMA_5,
        # glm-5.2 streaming bug workaround: dummy param in schema, empty params to bridge
        "transform_empty": True,
    },
    {
        "name": "delete_object",
        "description": "Delete an object from the scene by name.",
        "action": "delete_object",
        "schema_json": _SCHEMA_6,
        "transform_empty": False,
    },
    {
        "name": "update_object",
        "description": "Update an existing object's transform (location, scale, rotation).",
        "action": "update_object",
        "schema_json": _SCHEMA_7,
        "transform_empty": False,
    },
    {
        "name": "execute_python",
        "description": "Execute arbitrary bpy Python code in Blender. Use for operations not covered by structured tools.",
        "action": "execute_python",
        "schema_json": _SCHEMA_8,
        "transform_empty": False,
    },
    {
        "name": "boolean_modify",
        "description": "Apply a boolean operation (union, difference, intersect) between an existing target mesh and a transient cutter primitive. The cutter is deleted after the operation. Use to carve holes, cut shapes, or merge solids.",
        "action": "boolean_modify",
        "schema_json": _SCHEMA_9,
        "transform_empty": False,
    },
    {
        "name": "extrude_shape",
        "description": "Create a prismatic mesh by extruding a 2D profile polygon along an axis. Use for columns, beams, rails, and custom cross-sections not available as primitives.",
        "action": "extrude_shape",
        "schema_json": _SCHEMA_10,
        "transform_empty": False,
    },
    {
        "name": "add_curve",
        "description": "Create a bezier/poly curve with bevel depth (tube/pipe shape). Use for pipes, rails, cables, and organic linear structures.",
        "action": "add_curve",
        "schema_json": _SCHEMA_11,
        "transform_empty": False,
    },
]

ALL_TOOL_NAMES = [s["name"] for s in TOOL_SPECS]


# ---------------------------------------------------------------------------
# Tool wrapper — lh.create_tool returns a bare Tool with only .drive().
# We wrap it to also expose .name and .description (needed by tests and
# useful for debugging / workflow allowed_tools).
# ---------------------------------------------------------------------------

class BlenderTool:
    """Wraps an lh.Tool, exposing name/description alongside drive().

    封装 lh.Tool，额外暴露 name/description 属性。
    lh.create_tool 返回的 Tool 对象只有 drive() 方法，没有 name/description，
    因此用此包装类补充这些属性。
    """

    __slots__ = ("name", "description", "_tool", "drive")

    def __init__(self, name: str, description: str, tool: Any):
        self.name = name
        self.description = description
        self._tool = tool
        self.drive = tool.drive


def _make_callback(bridge: BlenderBridge, action: str, transform_empty: bool):
    """Build an async callback for lh.create_tool.

    为 lh.create_tool 构建异步回调。
    transform_empty=True 时，向 bridge 发送空 params {}（glm-5.2 workaround）。
    """

    async def callback(args: Any, ctx: Any) -> dict:
        params = {} if transform_empty else args
        data = await bridge.send(action, params)
        return {
            "content": [
                {"type": "text", "text": json.dumps({"action": action, "result": data})},
            ],
            "details": data,
            "terminate": False,
        }

    return callback


def all_blender_tools(bridge: BlenderBridge) -> list[BlenderTool]:
    """Build the full set of 12 Blender tools, all sharing one bridge.

    构建全部 12 个 Blender 工具，共享同一个 bridge。
    """
    tools = []
    for spec in TOOL_SPECS:
        cb = _make_callback(bridge, spec["action"], spec["transform_empty"])
        raw_tool = lh.create_tool(
            spec["name"],
            spec["description"],
            spec["schema_json"],
            cb,
        )
        tools.append(BlenderTool(spec["name"], spec["description"], raw_tool))
    return tools
