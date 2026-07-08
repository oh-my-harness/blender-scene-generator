"""Workflow definition, judge, and executors — Python translation.

Translates:
- REDACTEDdefinition.rs — build_workflow(), prompts, system prompts, ALL_TOOLS
- REDACTEDjudge.rs — ROUTE_RULES table, BlenderJudge::decide()
- REDACTEDexecutor.rs — RenderExecutor
- REDACTEDwait.rs — WaitForAdjustExecutor

工作流定义、judge 和 executor 的 Python 翻译。
"""
import asyncio
import datetime
import logging
from typing import Any

import llm_harness_py as lh

from blender_scene.plugin import create_system_prompt_plugin

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════
# Prompts — copied verbatim from definition.rs
# 提示词——从 definition.rs 逐字复制
# ════════════════════════════════════════════════════════════════

PLANNER_PROMPT = r"""You are a 3D scene planner. Read the user's scene description from the "Context" block above (key `user_description`), then produce a structured scene plan.

Keep the plan COMPACT: maximum 20 objects. Merge similar elements (e.g. use one "columns" object instead of 4 separate columns). Omit trivial details. Each object should be essential to the scene.

Output a JSON object with this exact structure:
{
  "objects": [{"name": "string", "type": "cube|sphere|cylinder|plane|cone|torus", "location": [x,y,z], "scale": [x,y,z], "rotation": [rx,ry,rz], "material": {"color": [r,g,b], "roughness": 0.0-1.0, "metallic": 0.0-1.0}, "shape": "optional: describe how to build this if it needs boolean/extrude/curve, e.g. 'wall with window: cube difference smaller cube', 'I-beam: extrude L-profile depth 4 along Y', 'pipe: curve along points with bevel 0.05'"}],
  "lights": [{"type": "point|sun|area|spot", "name": "string", "location": [x,y,z], "energy": number, "color": [r,g,b]}],
  "camera": {"location": [x,y,z], "rotation": [rx,ry,rz]}
}

Conventions:
- Coordinates are in Blender units, Z-up (X right, Y forward, Z up).
- `color` is [r,g,b] with each channel in 0.0-1.0.
- `rotation` is Euler angles in radians.
- `scale` is relative (1,1,1 = default size).

Call the submit_step_result tool with this JSON as the `result` argument. Do NOT output the JSON as text — it MUST go in the tool call's result parameter. The JSON must be complete and valid — if it gets truncated, the tool call will fail.
"""

BUILDER_PROMPT = r"""You are a 3D scene builder working in Blender. You interact with the scene exclusively through tool calls.

## First build (no "adjustment_instruction" in context)

The planner's scene plan is in the "Context" block above, under the `step_history` entry for step `planner` — read its `structured` field (`{"objects":[...], "lights":[...], "camera":{...}}`).

Build the scene from the plan in small batches (5-8 tool calls per turn):
1. Each turn: call add_object for a few objects, then set_material on each. Wait for tool results before continuing.
2. After all objects are built, call add_light for each light (a few per turn).
3. Call set_camera last.
Do NOT output all tool calls in a single response. After each batch, check tool results; if one failed, adjust parameters and retry in the next turn. This ensures you never hit output token limits.

## Advanced tools (beyond primitives)

When the plan calls for shapes that primitives can't express, use these tools. They operate on the same scene as add_object — combine them freely.

- `boolean_modify`: carve, merge, or intersect an existing mesh with a transient cutter primitive. Example: a wall with a window = add a cube, then boolean_modify difference with a smaller cube where the window goes. The cutter is deleted automatically.
- `extrude_shape`: create a prismatic mesh from a 2D profile polygon. Use for columns with custom cross-sections, I-beams, rails, etc. Provide `profile` as [[x,y],...] (closed polygon, ≥3 points), `depth`, and `axis` (X/Y/Z).
- `add_curve`: create a tube/pipe along control points. Use for pipes, cables, rails, and organic linear structures. Provide `points` as [[x,y,z],...] (≥2 points) and `bevel_depth` (tube radius).

General approach: build complex shapes by combining primitives with boolean_modify, or by extruding custom profiles. Set materials on the resulting objects with set_material as usual.

## Adjustment (when "adjustment_instruction" is present in context)

The user wants to modify the existing scene. Your FIRST tool call MUST be get_scene_state (no arguments) to inspect what objects actually exist (names, transforms, materials, lights). Then use update_object, delete_object, add_object, set_material, add_light, and set_camera to make incremental changes. Do NOT rebuild the scene from scratch — reuse existing object names when updating.

## When done

Call submit_step_result with: {"built_objects": [...], "built_lights": [...]}
"""

REVIEWER_PROMPT = r"""You are a scene reviewer. You interact with the scene exclusively through tool calls.

Call get_scene_state (no arguments) to inspect the scene. The result includes, per object: name, type, location, scale, rotation, materials (name, color, roughness, metallic for meshes), and light fields (light_type, energy, color for lights).

Review the scene for:
- Objects overlapping (use location + scale to estimate bounding boxes) or floating (location.z far above 0 with no support)
- Missing materials (mesh objects with empty `materials` array)
- Lights too dim (energy near 0) or too bright (energy extremely high)
- Camera angle issues (no camera, or camera pointing away from the scene)

Then call submit_step_result with: {"passed": true/false, "issues": ["issue1", "issue2"]}
- If the scene looks good: passed=true, issues=[]
- If there are problems: passed=false, issues=[list of problems to fix]
"""

# ── System prompts (per-step) ─────────────────────────────────

PLANNER_SYSTEM = "You are a professional 3D scene planner working with Blender. You analyze natural language descriptions and produce structured scene plans as JSON. You think in terms of primitive shapes (cube, sphere, cylinder, cone, torus, plane), PBR materials, lighting setups, and camera composition."

BUILDER_SYSTEM = "You are a professional Blender operator. You build and modify 3D scenes by calling tools that execute bpy operations. You understand 3D coordinates (Z-up in Blender), object hierarchy, PBR materials, and lighting. You work efficiently: batching independent tool calls, checking results, and retrying on failure. You never explain what you will do — you just do it via tool calls."

REVIEWER_SYSTEM = "You are a professional 3D scene reviewer. You inspect Blender scenes by querying scene state and evaluating object placement, materials, lighting, and camera composition. You report issues concisely and make pass/fail decisions."


# ════════════════════════════════════════════════════════════════
# ALL_TOOLS — tool names the workflow steps are allowed to call.
# Must match the name() of the Tool impls.
# ════════════════════════════════════════════════════════════════

ALL_TOOLS = [
    "add_object",
    "set_material",
    "add_light",
    "set_camera",
    "get_scene_state",
    "viewport_refresh",
    "delete_object",
    "update_object",
    "execute_python",
    "boolean_modify",
    "extrude_shape",
    "add_curve",
    "wait_for_external_event",
    # submit_step_result is auto-injected by the engine
]


def _builder_tools() -> list[str]:
    """Builder-only tools (no review, no submit — submit is auto-injected)."""
    return [t for t in ALL_TOOLS if t != "wait_for_external_event"]


def _reviewer_tools() -> list[str]:
    """Reviewer-only tools (review + submit)."""
    return ["get_scene_state"]


# ════════════════════════════════════════════════════════════════
# build_workflow — workflow topology
# 工作流拓扑
# ════════════════════════════════════════════════════════════════

def build_workflow() -> dict:
    """Build the unified workflow with an adjust loop.

    构建带调整循环的统一工作流。

    Topology:
      planner → builder → reviewer → renderer → wait_for_adjust → builder (loop)
                          ↑________________________|  (rework when review fails)
    """
    return {
        "entry_step": "planner",
        "steps": [
            {"id": "planner", "name": "Planner", "prompt": PLANNER_PROMPT, "allowed_tools": []},
            {"id": "builder", "name": "Builder", "prompt": BUILDER_PROMPT, "allowed_tools": _builder_tools()},
            {"id": "reviewer", "name": "Reviewer", "prompt": REVIEWER_PROMPT, "allowed_tools": _reviewer_tools()},
            {"id": "renderer", "name": "Renderer", "executor": "render_executor"},
            {"id": "wait_for_adjust", "name": "Wait", "executor": "wait_for_adjust"},
        ],
        "edges": [
            {"from": "planner", "to": "builder"},
            # builder → reviewer (first pass) or builder → renderer (adjust passes).
            {"from": "builder", "to": "reviewer"},
            {"from": "builder", "to": "renderer"},
            # reviewer → renderer (passed) or reviewer → builder (failed, rework).
            {"from": "reviewer", "to": "renderer"},
            {"from": "reviewer", "to": "builder"},
            # renderer → wait_for_adjust (blocks for human input).
            {"from": "renderer", "to": "wait_for_adjust"},
            # wait_for_adjust → builder (adjust loop).
            {"from": "wait_for_adjust", "to": "builder"},
        ],
    }


# ════════════════════════════════════════════════════════════════
# Wrapper classes — expose raw callbacks for testing
# 包装类——暴露原始回调用于测试
# ════════════════════════════════════════════════════════════════

class JudgeWrapper:
    """Wraps an lh.Judge, exposing the raw callback for testing.

    封装 lh.Judge，额外暴露原始回调函数用于测试。
    """

    __slots__ = ("callback", "_judge")

    def __init__(self, callback, judge: lh.Judge):
        self.callback = callback
        self._judge = judge

    def __call__(self, *args, **kwargs):
        """Delegate to the raw callback for direct testing."""
        return self.callback(*args, **kwargs)


class ExecutorWrapper:
    """Wraps an lh.Executor, exposing the raw callback for testing.

    The SDK executor callback is synchronous (called via spawn_blocking).
    For async operations (like bridge.send), the callback uses asyncio.run()
    internally.

    封装 lh.Executor，额外暴露原始回调用于测试。
    SDK 执行器回调是同步的（通过 spawn_blocking 调用）。
    对于异步操作（如 bridge.send），回调内部使用 asyncio.run()。
    """

    __slots__ = ("callback", "_executor")

    def __init__(self, callback, executor: lh.Executor):
        self.callback = callback
        self._executor = executor

    def __call__(self, *args, **kwargs):
        """Delegate to the raw callback for direct testing."""
        return self.callback(*args, **kwargs)


# ════════════════════════════════════════════════════════════════
# BlenderJudge — ROUTE_RULES table from judge.rs
# 路由规则表
# ════════════════════════════════════════════════════════════════

MAX_BUILDER_RUNS = 3  # initial + 2 retries


def create_blender_judge():
    """Create the Blender workflow judge.

    The Python SDK judge callback receives ctx with:
    - step_id: str — current step ID
    - output: str — step output
    - step_count: int — total steps in history
    - structured: dict | None — structured result

    Since the SDK does not pass step_history, we track builder execution
    count via closure-captured state, incremented after each builder judgment.
    This mirrors Rust's count_step_executions(history, "builder").

    创建 Blender 工作流 judge。
    由于 SDK 不传递 step_history，我们通过闭包捕获的状态跟踪 builder 执行次数。
    """
    state = {"builder_count": 0}

    def judge_cb(ctx: dict) -> str:
        step_id = ctx.get("step_id", "")
        structured = ctx.get("structured")
        builder_count = state["builder_count"]

        # Helper predicates (from judge.rs)
        def is_adjust_pass() -> bool:
            # builder has run before → adjust pass → skip reviewer
            return builder_count >= 1

        def review_passed() -> bool:
            # structured.get("passed"), default True if missing (tolerate glm-5.2)
            if structured is None:
                return True
            passed = structured.get("passed")
            if passed is None:
                return True
            return bool(passed)

        def builder_under_limit() -> bool:
            return builder_count < MAX_BUILDER_RUNS

        # ── Route rules (first match wins) ──
        if step_id == "planner":
            result = "to:builder"

        elif step_id == "builder":
            if is_adjust_pass() and builder_under_limit():
                result = "to:renderer"
            elif is_adjust_pass():
                result = "fail:adjust retry limit reached"
            else:
                result = "to:reviewer"

        elif step_id == "reviewer":
            if review_passed():
                result = "to:renderer"
            elif builder_under_limit():
                result = "to:builder"
            else:
                result = "fail:builder re-hop limit reached"

        elif step_id == "renderer":
            result = "to:wait_for_adjust"

        elif step_id == "wait_for_adjust":
            result = "to:builder"

        else:
            result = f"fail:no route rule matched for step '{step_id}'"

        # Increment builder counter AFTER routing decision (mirrors
        # step_history not including current step at judge time).
        if step_id == "builder":
            state["builder_count"] += 1

        logger.info("judge: %s → %s (builder_count=%d)", step_id, result, builder_count)
        return result
    return JudgeWrapper(judge_cb, lh.create_judge(judge_cb))


# ════════════════════════════════════════════════════════════════
# RenderExecutor — calls bridge.send("render", ...)
# 渲染执行器
# ════════════════════════════════════════════════════════════════

def create_render_executor(bridge, output_dir: str):
    """Create a render executor that calls bridge.send("render", ...).

    The SDK executor callback is synchronous (runs in spawn_blocking).
    bridge.send() is async, so we run it via asyncio.run() inside the callback.

    创建一个调用 bridge.send("render", ...) 的渲染执行器。
    SDK 执行器回调是同步的（在 spawn_blocking 中运行）。
    bridge.send() 是异步的，因此在回调内部通过 asyncio.run() 运行。
    """
    def executor_cb(ctx: dict) -> dict:
        # Generate a unique output path
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = f"{output_dir}/render_{timestamp}.png"

        logger.info("render_executor: rendering to %s", output_path)

        # Tell Blender to render — bridge.send is async, run via asyncio.run()
        async def _do_render():
            return await bridge.send("render", {"output_path": output_path})

        render_result = asyncio.run(_do_render())
        image_path = render_result.get("image_path", output_path) if render_result else output_path
        logger.info("render_executor: done, image_path=%s", image_path)

        return {
            "output": f"rendered to {image_path}",
            "structured": {"image_path": image_path},
        }

    return ExecutorWrapper(executor_cb, lh.create_executor(executor_cb))


# ════════════════════════════════════════════════════════════════
# WaitForAdjustExecutor — blocks on threading.Event
# 等待调整执行器
# ════════════════════════════════════════════════════════════════

import queue


class AdjustHandle:
    """Handle held by the web server to push adjustment instructions.

    The executor blocks on a queue.Queue.get(); submit() puts the instruction.
    Thread-safe: submit() can be called from any thread.

    Unlike threading.Event (which stays set after the first submit), a queue
    is consumed per iteration — each wait_for_adjust step blocks for a NEW
    instruction, correctly supporting the adjust loop:
    wait_for_adjust → builder → renderer → wait_for_adjust → ...

    Web 服务器持有的句柄，用于推送调整指令。
    执行器阻塞在 queue.Queue.get() 上；submit() 放入指令。
    线程安全：submit() 可从任意线程调用。
    与 threading.Event（首次 set 后保持 set 状态）不同，queue 每轮被消费——
    每个 wait_for_adjust 步骤阻塞等待新指令，正确支持调整循环。
    """

    def __init__(self):
        self._queue: queue.Queue[str] = queue.Queue()

    def submit(self, instruction: str) -> None:
        """Push an adjustment instruction into the waiting executor.

        推送调整指令到等待中的执行器。
        """
        self._queue.put(instruction)


def create_wait_for_adjust_executor(engine=None):
    """Create the wait-for-adjust executor + handle.

    Returns (executor, handle). The executor blocks (sync, in spawn_blocking)
    until handle.submit(instruction) is called from the web server.

    After receiving the instruction, writes it to the workflow context as
    `adjustment_instruction` so the builder step can read it. Requires the
    engine reference to call set_context_variable.

    The SDK executor callback is synchronous, so we use queue.Queue for
    cross-thread signaling (submit() is called from the web server thread,
    executor runs in a spawn_blocking thread).

    创建等待调整的执行器 + 句柄。
    返回 (executor, handle)。执行器阻塞（同步，在 spawn_blocking 中）
    直到 web 服务器调用 handle.submit(instruction)。
    收到指令后，将其写入工作流上下文（键 `adjustment_instruction`），
    供 builder 步骤读取。需要 engine 引用以调用 set_context_variable。
    SDK 执行器回调是同步的，因此使用 queue.Queue 进行跨线程信号传递。
    """
    handle = AdjustHandle()

    def executor_cb(ctx: dict) -> dict:
        # Block until submit() is called from another thread.
        # Each call consumes one item from the queue, so the next
        # wait_for_adjust iteration blocks for a new instruction.
        logger.info("wait_for_adjust: blocking until user submits adjustment")
        instruction = handle._queue.get()
        logger.info("wait_for_adjust: received instruction: %s", instruction[:80])
        # Write the instruction into the workflow context so the builder
        # step (next after this executor) can read it.
        # 将指令写入工作流上下文，供随后的 builder 步骤读取。
        if engine is not None:
            engine.set_context_variable("adjustment_instruction", instruction)
        return {
            "output": f"adjustment: {instruction}",
            "structured": {"instruction": instruction},
        }

    executor = ExecutorWrapper(executor_cb, lh.create_executor(executor_cb))
    return executor, handle
