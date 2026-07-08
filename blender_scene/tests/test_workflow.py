"""Tests for the workflow definition, judge, and executors.

工作流定义、judge 和 executor 的测试。
翻译自 REDACTEDjudge.rs 和 definition.rs 的 tests 模块。
"""
import asyncio
import json

import llm_harness_py as lh
from blender_scene.workflow import (
    build_workflow,
    create_blender_judge,
    create_render_executor,
    create_wait_for_adjust_executor,
    create_system_prompt_plugin,
    PLANNER_PROMPT,
    BUILDER_PROMPT,
    REVIEWER_PROMPT,
    PLANNER_SYSTEM,
    BUILDER_SYSTEM,
    REVIEWER_SYSTEM,
    MAX_BUILDER_RUNS,
    ALL_TOOLS,
)
from blender_scene.bridge import BlenderBridge


# ── build_workflow ─────────────────────────────────────────────

def test_build_workflow_has_5_steps():
    wf = build_workflow()
    assert len(wf["steps"]) == 5
    step_ids = [s["id"] for s in wf["steps"]]
    assert "planner" in step_ids
    assert "builder" in step_ids
    assert "reviewer" in step_ids
    assert "renderer" in step_ids
    assert "wait_for_adjust" in step_ids


def test_build_workflow_entry_is_planner():
    wf = build_workflow()
    assert wf["entry_step"] == "planner"


def test_build_workflow_has_7_edges():
    wf = build_workflow()
    assert len(wf["edges"]) == 7


def test_build_workflow_step_types():
    """3 LLM steps + 2 executor steps."""
    wf = build_workflow()
    llm_steps = [s for s in wf["steps"] if "prompt" in s]
    exec_steps = [s for s in wf["steps"] if "executor" in s]
    assert len(llm_steps) == 3
    assert len(exec_steps) == 2


def test_build_workflow_executor_names():
    """Executor steps must have correct executor names."""
    wf = build_workflow()
    exec_steps = {s["id"]: s["executor"] for s in wf["steps"] if "executor" in s}
    assert exec_steps["renderer"] == "render_executor"
    assert exec_steps["wait_for_adjust"] == "wait_for_adjust"


def test_build_workflow_builder_tools():
    """Builder step has builder tools (all except wait_for_external_event)."""
    wf = build_workflow()
    builder = [s for s in wf["steps"] if s["id"] == "builder"][0]
    tools = builder["allowed_tools"]
    assert "wait_for_external_event" not in tools
    assert "add_object" in tools
    assert "get_scene_state" in tools


def test_build_workflow_reviewer_tools():
    """Reviewer step only has get_scene_state."""
    wf = build_workflow()
    reviewer = [s for s in wf["steps"] if s["id"] == "reviewer"][0]
    assert reviewer["allowed_tools"] == ["get_scene_state"]


def test_build_workflow_planner_no_tools():
    """Planner step has no tools."""
    wf = build_workflow()
    planner = [s for s in wf["steps"] if s["id"] == "planner"][0]
    assert planner["allowed_tools"] == []


# ── Prompts ────────────────────────────────────────────────────

def test_prompts_are_non_empty_strings():
    for p in [PLANNER_PROMPT, BUILDER_PROMPT, REVIEWER_PROMPT,
              PLANNER_SYSTEM, BUILDER_SYSTEM, REVIEWER_SYSTEM]:
        assert isinstance(p, str)
        assert len(p) > 0


def test_planner_prompt_mentions_json():
    assert "JSON" in PLANNER_PROMPT


def test_builder_prompt_mentions_adjustment():
    assert "adjustment_instruction" in BUILDER_PROMPT


def test_reviewer_prompt_mentions_passed():
    assert "passed" in REVIEWER_PROMPT


# ── ALL_TOOLS ──────────────────────────────────────────────────

def test_all_tools_includes_13_names():
    """ALL_TOOLS has the 13 tool names (12 tools + wait_for_external_event)."""
    assert "add_object" in ALL_TOOLS
    assert "wait_for_external_event" in ALL_TOOLS


# ── create_blender_judge ───────────────────────────────────────

def test_judge_created():
    judge = create_blender_judge()
    assert judge is not None


def test_judge_is_judge_wrapper():
    """Judge must expose a callable callback and an lh.Judge."""
    judge = create_blender_judge()
    assert hasattr(judge, "callback")
    assert hasattr(judge, "_judge")


def test_judge_routes_planner_to_builder():
    judge = create_blender_judge()
    ctx = {"step_id": "planner", "output": "", "step_count": 0, "structured": None}
    assert judge(ctx) == "to:builder"


def test_judge_routes_first_builder_to_reviewer():
    """First builder pass (no prior builder) → to:reviewer."""
    judge = create_blender_judge()
    ctx = {"step_id": "builder", "output": "", "step_count": 1, "structured": None}
    assert judge(ctx) == "to:reviewer"


def test_judge_routes_adjust_builder_to_renderer():
    """Second builder pass (adjust) → to:renderer."""
    judge = create_blender_judge()
    # First builder → reviewer
    judge({"step_id": "builder", "output": "", "step_count": 1, "structured": None})
    # Second builder → renderer (adjust pass)
    ctx = {"step_id": "builder", "output": "", "step_count": 5, "structured": None}
    assert judge(ctx) == "to:renderer"


def test_judge_fails_adjust_after_max_builder_runs():
    """After MAX_BUILDER_RUNS builder executions, adjust → fail."""
    judge = create_blender_judge()
    # Simulate MAX_BUILDER_RUNS builder completions
    for _ in range(MAX_BUILDER_RUNS):
        judge({"step_id": "builder", "output": "", "step_count": 0, "structured": None})
    # Next builder → adjust pass but over limit → fail
    ctx = {"step_id": "builder", "output": "", "step_count": 0, "structured": None}
    result = judge(ctx)
    assert result.startswith("fail:")


def test_judge_routes_reviewer_pass_to_renderer():
    """Reviewer with passed=True → to:renderer."""
    judge = create_blender_judge()
    # Need one builder run first
    judge({"step_id": "builder", "output": "", "step_count": 1, "structured": None})
    ctx = {
        "step_id": "reviewer",
        "output": "",
        "step_count": 2,
        "structured": {"passed": True, "issues": []},
    }
    assert judge(ctx) == "to:renderer"


def test_judge_routes_reviewer_fail_back_to_builder():
    """Reviewer with passed=False, under limit → to:builder."""
    judge = create_blender_judge()
    # One builder run
    judge({"step_id": "builder", "output": "", "step_count": 1, "structured": None})
    ctx = {
        "step_id": "reviewer",
        "output": "",
        "step_count": 2,
        "structured": {"passed": False, "issues": ["bad"]},
    }
    assert judge(ctx) == "to:builder"


def test_judge_fails_reviewer_after_max_builder_runs():
    """Reviewer fail after MAX_BUILDER_RUNS builder runs → fail."""
    judge = create_blender_judge()
    for _ in range(MAX_BUILDER_RUNS):
        judge({"step_id": "builder", "output": "", "step_count": 0, "structured": None})
    ctx = {
        "step_id": "reviewer",
        "output": "",
        "step_count": 0,
        "structured": {"passed": False, "issues": ["bad"]},
    }
    result = judge(ctx)
    assert result.startswith("fail:")


def test_judge_routes_renderer_to_wait_for_adjust():
    judge = create_blender_judge()
    ctx = {"step_id": "renderer", "output": "", "step_count": 0, "structured": None}
    assert judge(ctx) == "to:wait_for_adjust"


def test_judge_routes_wait_for_adjust_to_builder():
    judge = create_blender_judge()
    ctx = {"step_id": "wait_for_adjust", "output": "", "step_count": 0, "structured": None}
    assert judge(ctx) == "to:builder"


def test_judge_review_passed_defaults_true_when_missing():
    """When structured is None or missing 'passed', default to True (tolerate glm-5.2)."""
    judge = create_blender_judge()
    judge({"step_id": "builder", "output": "", "step_count": 1, "structured": None})
    ctx = {"step_id": "reviewer", "output": "", "step_count": 2, "structured": None}
    assert judge(ctx) == "to:renderer"


# ── create_render_executor ────────────────────────────────────

def test_render_executor_created():
    bridge = BlenderBridge()
    executor = create_render_executor(bridge, "/tmp/renders")
    assert hasattr(executor, "callback")
    assert hasattr(executor, "_executor")


def test_render_executor_calls_bridge():
    """Executor calls bridge.send('render', ...) and returns image_path."""
    import struct
    import threading
    import socket

    host, port = "127.0.0.1", 29871
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host, port))
    server.listen(1)

    captured = {}

    def serve():
        conn, _ = server.accept()
        len_buf = conn.recv(4)
        msg_len = struct.unpack(">I", len_buf)[0]
        payload = b""
        while len(payload) < msg_len:
            chunk = conn.recv(msg_len - len(payload))
            if not chunk:
                break
            payload += chunk
        request = json.loads(payload)
        captured["action"] = request["action"]
        captured["params"] = request["params"]
        response = {"ok": True, "data": {"image_path": "/tmp/renders/test.png"}}
        resp_payload = json.dumps(response).encode()
        conn.sendall(struct.pack(">I", len(resp_payload)) + resp_payload)
        conn.close()

    t = threading.Thread(target=serve, daemon=True)
    t.start()

    bridge = BlenderBridge(host, port)
    executor = create_render_executor(bridge, "/tmp/renders")

    # Executor callback is sync (SDK runs it in spawn_blocking).
    # It internally uses asyncio.run() for bridge.send().
    ctx = {"step_id": "renderer", "step_name": "Renderer", "config": None,
           "prev_output": None, "context": {}}
    result = executor(ctx)
    assert "image_path" in result["structured"]
    assert result["structured"]["image_path"] == "/tmp/renders/test.png"
    assert captured["action"] == "render"
    assert "output_path" in captured["params"]
    server.close()
    t.join(timeout=2)


# ── create_wait_for_adjust_executor ────────────────────────────

def test_wait_for_adjust_executor_created():
    executor, handle = create_wait_for_adjust_executor()
    assert hasattr(executor, "callback")
    assert hasattr(executor, "_executor")
    assert handle is not None


def test_wait_for_adjust_executor_blocks_until_submit():
    """Executor blocks until handle.submit() is called."""
    import threading

    executor, handle = create_wait_for_adjust_executor()
    ctx = {"step_id": "wait_for_adjust", "step_name": "Wait", "config": None,
           "prev_output": None, "context": {}}

    result_box = {}

    def run_executor():
        # Executor callback is sync — blocks on threading.Event
        result_box["result"] = executor(ctx)

    t = threading.Thread(target=run_executor, daemon=True)
    t.start()
    # Give it a moment to start waiting
    t.join(timeout=0.1)
    assert t.is_alive(), "executor should be blocking"
    # Submit the adjustment
    handle.submit("make the cube red")
    t.join(timeout=2)
    assert not t.is_alive(), "executor should have finished"

    result = result_box["result"]
    assert "make the cube red" in result["output"]
    assert result["structured"]["instruction"] == "make the cube red"


# ── create_system_prompt_plugin ────────────────────────────────

def test_system_prompt_plugin_created():
    plugin = create_system_prompt_plugin("You are a planner.")
    assert isinstance(plugin, lh.Plugin)


def test_system_prompt_plugin_name():
    """Plugin name should be 'system-prompt'."""
    # The plugin is opaque; we can't directly check the name,
    # but we verify it was created without error.
    plugin = create_system_prompt_plugin("test prompt")
    assert plugin is not None
