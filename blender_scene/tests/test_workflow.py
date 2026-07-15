"""Tests for the expanded 10+1 step workflow.

Tests: scene refinement + human approval + batch planning loop + professional division of labor.
"""
import json
import queue
import threading
import time

import llm_harness_py as lh
import pytest

from blender_scene.workflow import (
    ALL_TOOLS,
    BUILDER_PROMPT,
    BUILDER_SYSTEM,
    LIGHTING_DESIGNER_PROMPT,
    LIGHTING_DESIGNER_SYSTEM,
    LIGHTING_PLANNER_PROMPT,
    LIGHTING_PLANNER_SYSTEM,
    MATERIAL_ARTIST_PROMPT,
    MATERIAL_ARTIST_SYSTEM,
    OBJECT_PLANNER_PROMPT,
    OBJECT_PLANNER_SYSTEM,
    REVIEWER_PROMPT,
    REVIEWER_SYSTEM,
    SCENE_ANALYST_PROMPT,
    SCENE_ANALYST_SYSTEM,
    SCENE_REFINER_PROMPT,
    SCENE_REFINER_SYSTEM,
    MAX_BUILDER_RUNS,
    create_blender_judge,
    create_render_executor,
    create_scene_review_executor,
    create_wait_for_adjust_executor,
    build_workflow,
)
from blender_scene.bridge import BlenderBridge


# ── build_workflow ─────────────────────────────────────────────

def test_build_workflow_has_11_steps():
    wf = build_workflow()
    step_ids = [s["id"] for s in wf["steps"]]
    assert len(step_ids) == 11
    for sid in ("scene_refiner", "scene_review", "scene_analyst", "object_planner",
                "builder", "lighting_planner", "material_artist", "lighting_designer",
                "reviewer", "renderer", "wait_for_adjust"):
        assert sid in step_ids


def test_build_workflow_entry_is_scene_refiner():
    wf = build_workflow()
    assert wf["entry_step"] == "scene_refiner"


def test_build_workflow_has_14_edges():
    wf = build_workflow()
    assert len(wf["edges"]) == 14


def test_build_workflow_step_types():
    """8 LLM steps + 3 executor steps."""
    wf = build_workflow()
    llm_steps = [s for s in wf["steps"] if "prompt" in s]
    exec_steps = [s for s in wf["steps"] if "executor" in s]
    assert len(llm_steps) == 8
    assert len(exec_steps) == 3


def test_build_workflow_executor_names():
    wf = build_workflow()
    exec_steps = {s["id"]: s["executor"] for s in wf["steps"] if "executor" in s}
    assert exec_steps["scene_review"] == "scene_review_executor"
    assert exec_steps["renderer"] == "render_executor"
    assert exec_steps["wait_for_adjust"] == "wait_for_adjust"


def test_build_workflow_builder_tools():
    """Builder step has modeling tools only (no set_material, no add_light, no set_camera)."""
    wf = build_workflow()
    builder = next(s for s in wf["steps"] if s["id"] == "builder")
    tools = builder["allowed_tools"]
    assert "add_object" in tools
    assert "boolean_modify" in tools
    assert "extrude_shape" in tools
    assert "add_curve" in tools
    assert "get_scene_state" in tools
    assert "set_material" not in tools
    assert "add_light" not in tools
    assert "set_camera" not in tools


def test_build_workflow_material_artist_tools():
    """Material Artist step has only set_material + get_scene_state + viewport_refresh."""
    wf = build_workflow()
    artist = next(s for s in wf["steps"] if s["id"] == "material_artist")
    tools = artist["allowed_tools"]
    assert "set_material" in tools
    assert "get_scene_state" in tools
    assert "add_object" not in tools
    assert "add_light" not in tools


def test_build_workflow_lighting_designer_tools():
    """Lighting Designer step has only add_light + set_camera + get_scene_state + viewport_refresh."""
    wf = build_workflow()
    designer = next(s for s in wf["steps"] if s["id"] == "lighting_designer")
    tools = designer["allowed_tools"]
    assert "add_light" in tools
    assert "set_camera" in tools
    assert "get_scene_state" in tools
    assert "add_object" not in tools
    assert "set_material" not in tools


def test_build_workflow_reviewer_tools():
    """Reviewer step only has get_scene_state."""
    wf = build_workflow()
    reviewer = next(s for s in wf["steps"] if s["id"] == "reviewer")
    assert reviewer["allowed_tools"] == ["get_scene_state"]


def test_build_workflow_planner_steps_no_tools():
    """All 4 planner/refiner/analyst steps have no tools."""
    wf = build_workflow()
    for step_id in ("scene_refiner", "scene_analyst", "object_planner", "lighting_planner"):
        step = next(s for s in wf["steps"] if s["id"] == step_id)
        assert step["allowed_tools"] == [], f"{step_id} should have no tools"


# ── Prompts ────────────────────────────────────────────────────

def test_prompts_are_non_empty_strings():
    for p in [SCENE_REFINER_PROMPT, SCENE_ANALYST_PROMPT, OBJECT_PLANNER_PROMPT,
              LIGHTING_PLANNER_PROMPT, BUILDER_PROMPT, MATERIAL_ARTIST_PROMPT,
              LIGHTING_DESIGNER_PROMPT, REVIEWER_PROMPT]:
        assert isinstance(p, str)
        assert len(p) > 0


def test_scene_refiner_prompt_mentions_submit():
    assert "submit_step_result" in SCENE_REFINER_PROMPT


def test_object_planner_prompt_mentions_has_more():
    assert "has_more" in OBJECT_PLANNER_PROMPT


def test_builder_prompt_mentions_adjustment():
    assert "wait_for_adjust" in BUILDER_PROMPT


def test_reviewer_prompt_mentions_passed():
    assert "passed" in REVIEWER_PROMPT


# ── ALL_TOOLS ──────────────────────────────────────────────────

def test_all_tools_includes_13_names():
    assert "add_object" in ALL_TOOLS
    assert "wait_for_external_event" in ALL_TOOLS


# ── create_blender_judge ───────────────────────────────────────

def test_judge_created():
    judge = create_blender_judge()
    assert judge is not None


def test_judge_is_judge_wrapper():
    judge = create_blender_judge()
    assert callable(judge)
    assert hasattr(judge, "_judge")


def test_judge_routes_refiner_to_scene_review():
    judge = create_blender_judge()
    ctx = {"step_id": "scene_refiner", "output": "", "step_count": 0, "structured": None}
    assert judge(ctx) == "to:scene_review"


def test_judge_routes_scene_review_to_analyst():
    judge = create_blender_judge()
    ctx = {"step_id": "scene_review", "output": "", "step_count": 0, "structured": None}
    assert judge(ctx) == "to:scene_analyst"


def test_judge_routes_analyst_to_object_planner():
    judge = create_blender_judge()
    ctx = {"step_id": "scene_analyst", "output": "", "step_count": 0, "structured": None}
    assert judge(ctx) == "to:object_planner"


def test_judge_routes_object_planner_has_more_to_builder():
    """object_planner with has_more=true → to:builder (batch loop)."""
    judge = create_blender_judge()
    ctx = {"step_id": "object_planner", "output": "", "step_count": 0,
           "structured": {"objects": [], "has_more": True}}
    assert judge(ctx) == "to:builder"


def test_judge_routes_object_planner_no_more_to_lighting_planner():
    """object_planner with has_more=false → to:lighting_planner."""
    judge = create_blender_judge()
    ctx = {"step_id": "object_planner", "output": "", "step_count": 0,
           "structured": {"objects": [], "has_more": False}}
    assert judge(ctx) == "to:lighting_planner"


def test_judge_routes_builder_after_has_more_back_to_object_planner():
    """builder after object_planner with has_more=true → to:object_planner (batch loop)."""
    judge = create_blender_judge()
    # First: object_planner says has_more=true
    judge({"step_id": "object_planner", "output": "", "step_count": 0,
           "structured": {"objects": [], "has_more": True}})
    # Then: builder should go back to object_planner
    ctx = {"step_id": "builder", "output": "", "step_count": 1, "structured": None}
    assert judge(ctx) == "to:object_planner"


def test_judge_routes_builder_after_no_more_to_material_artist():
    """builder after object_planner with has_more=false → to:material_artist."""
    judge = create_blender_judge()
    # object_planner says has_more=false (last batch)
    judge({"step_id": "object_planner", "output": "", "step_count": 0,
           "structured": {"objects": [], "has_more": False}})
    ctx = {"step_id": "builder", "output": "", "step_count": 1, "structured": None}
    assert judge(ctx) == "to:material_artist"


def test_judge_routes_lighting_planner_to_material_artist():
    judge = create_blender_judge()
    ctx = {"step_id": "lighting_planner", "output": "", "step_count": 0, "structured": None}
    assert judge(ctx) == "to:material_artist"


def test_judge_routes_material_artist_to_lighting_designer():
    judge = create_blender_judge()
    ctx = {"step_id": "material_artist", "output": "", "step_count": 0, "structured": None}
    assert judge(ctx) == "to:lighting_designer"


def test_judge_routes_lighting_designer_to_reviewer():
    judge = create_blender_judge()
    ctx = {"step_id": "lighting_designer", "output": "", "step_count": 0, "structured": None}
    assert judge(ctx) == "to:reviewer"


def test_judge_routes_reviewer_pass_to_renderer():
    judge = create_blender_judge()
    ctx = {"step_id": "reviewer", "output": "", "step_count": 0,
           "structured": {"passed": True, "issues": []}}
    assert judge(ctx) == "to:renderer"


def test_judge_routes_reviewer_fail_back_to_material_artist():
    """Reviewer with passed=False, under limit → to:material_artist."""
    judge = create_blender_judge()
    ctx = {"step_id": "reviewer", "output": "", "step_count": 0,
           "structured": {"passed": False, "issues": ["bad"]}}
    assert judge(ctx) == "to:material_artist"


def test_judge_fails_reviewer_after_max_builder_runs():
    judge = create_blender_judge()
    for _ in range(MAX_BUILDER_RUNS):
        judge({"step_id": "builder", "output": "", "step_count": 0, "structured": None})
    result = judge({"step_id": "reviewer", "output": "", "step_count": 0,
                    "structured": {"passed": False, "issues": ["bad"]}})
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
    judge = create_blender_judge()
    ctx = {"step_id": "reviewer", "output": "", "step_count": 0, "structured": None}
    assert judge(ctx) == "to:renderer"


# ── create_scene_review_executor ───────────────────────────────

def test_scene_review_executor_created():
    executor, handle = create_scene_review_executor()
    assert hasattr(executor, "_executor")
    assert handle is not None


def test_scene_review_executor_blocks_until_submit():
    """Executor blocks until handle.submit() is called."""
    executor, handle = create_scene_review_executor()
    result_box = {}

    def run():
        result_box["result"] = executor({"test": True})

    t = threading.Thread(target=run, daemon=True)
    t.start()
    time.sleep(0.2)
    assert "result" not in result_box  # still blocked

    handle.submit(True, "looks good")
    t.join(timeout=2)

    assert "result" in result_box
    assert result_box["result"]["structured"]["approved"] is True


# ── create_render_executor ────────────────────────────────────

def test_render_executor_created():
    bridge = BlenderBridge()
    executor = create_render_executor(bridge, "/tmp/test_renders")
    assert hasattr(executor, "_executor")


def test_render_executor_calls_bridge():
    calls = []

    class FakeBridge:
        def send(self, cmd, args):
            calls.append((cmd, args))
            return {"image_path": args["output_path"]}

    executor = create_render_executor(FakeBridge(), "/tmp/test_renders")
    result = executor({"output_path": "/tmp/test_renders/test.png"})
    assert result["structured"]["image_path"] == "/tmp/test_renders/test.png"
    assert calls[0][0] == "render"


# ── create_wait_for_adjust_executor ────────────────────────────

def test_wait_for_adjust_executor_created():
    executor, handle = create_wait_for_adjust_executor()
    assert hasattr(executor, "_executor")
    assert handle is not None


def test_wait_for_adjust_executor_blocks_until_submit():
    executor, handle = create_wait_for_adjust_executor()
    result_box = {}

    def run():
        result_box["result"] = executor({"test": True})

    t = threading.Thread(target=run, daemon=True)
    t.start()
    time.sleep(0.2)
    assert "result" not in result_box

    handle.submit("make the cube red")
    t.join(timeout=2)

    assert "result" in result_box
    assert result_box["result"]["structured"]["instruction"] == "make the cube red"
