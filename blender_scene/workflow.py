"""Workflow definition, judge, and executors — 10+1 step expansion.

Expanded topology with scene refinement, human approval, batch planning loop,
and professional division of labor (builder / material_artist / lighting_designer).

工作流定义、judge 和 executor —— 10+1 步扩展版本。
"""
import datetime
import logging
import queue
from typing import Any

import llm_harness_py as lh

from blender_scene.plugin import create_system_prompt_plugin

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════
# Prompts — 8 LLM steps
# 提示词 —— 8 个 LLM 步骤
# ════════════════════════════════════════════════════════════════

SCENE_REFINER_PROMPT = r"""You are a 3D scene refiner. Read the user's scene description from the "Context" block above (key `user_description`), then refine it into a detailed, vivid description suitable for a professional 3D artist to build.

Elaborate on:
- Visual style and atmosphere
- Key objects and their spatial relationships
- Color palette and material suggestions
- Lighting mood and camera composition

Output the refined description as text, then call the submit_step_result tool with a JSON object: {"refined_description": "your refined description text here"}.

Do NOT output the JSON as text — it MUST go in the tool call's result parameter.
"""

SCENE_ANALYST_PROMPT = r"""You are a 3D scene analyst. Read the refined scene description from the "Context" block above — find the `step_history` entry for step `scene_refiner` and read its `structured` field (`{"refined_description": "..."}`).

Analyze the refined description and extract a structured creative brief:
- `style`: the overall visual style (e.g. "minimalist modern", "cozy low-poly")
- `atmosphere`: the mood/atmosphere (e.g. "warm and inviting", "cold and industrial")
- `color_palette`: list of dominant colors as hex strings or descriptive names
- `key_elements`: list of the most important visual elements to include

Call the submit_step_result tool with: {"style": "...", "atmosphere": "...", "color_palette": [...], "key_elements": [...]}

Do NOT output the JSON as text — it MUST go in the tool call's result parameter.
"""

OBJECT_PLANNER_PROMPT = r"""You are a 3D object planner. Read the analyst's result from the "Context" block above — find the `step_history` entry for step `scene_analyst` and read its `structured` field.

Plan objects in BATCHES of 5-8 objects per batch. For each batch, output the objects with: name, type (cube|sphere|cylinder|plane|cone|torus), location [x,y,z], scale [x,y,z], and material_hint (short description of appearance).

If there are more objects to plan, set `has_more` to true. If this is the last batch, set `has_more` to false.

Call the submit_step_result tool with: {"objects": [{"name": "...", "type": "...", "location": [x,y,z], "scale": [x,y,z], "material_hint": "..."}], "has_more": true/false}

Do NOT output the JSON as text — it MUST go in the tool call's result parameter.
"""

LIGHTING_PLANNER_PROMPT = r"""You are a 3D lighting planner. Read the analyst's result from the "Context" block above — find the `step_history` entry for step `scene_analyst` and read its `structured` field.

Plan the lighting setup and camera for the scene:
- `lights`: list of lights with type (point|sun|area|spot), name, location [x,y,z], and energy
- `camera`: location [x,y,z] and rotation [rx,ry,rz] in radians

Call the submit_step_result tool with: {"lights": [...], "camera": {"location": [...], "rotation": [...]}}

Do NOT output the JSON as text — it MUST go in the tool call's result parameter.
"""

BUILDER_PROMPT = r"""You are a 3D scene builder working in Blender. You interact with the scene exclusively through tool calls.

## Normal build

The current batch of objects is in the "Context" block above — find the `step_history` entry for step `object_planner` and read its `structured.objects` field. Each object has a `material_hint` describing its appearance.

Build the objects from the current batch in small groups (5-8 tool calls per turn):
1. Call add_object for a few objects. Wait for tool results before continuing.
2. If a call failed, adjust parameters and retry in the next turn.
Do NOT output all tool calls in a single response.

## Advanced tools (beyond primitives)

- `boolean_modify`: carve, merge, or intersect an existing mesh with a transient cutter primitive.
- `extrude_shape`: create a prismatic mesh from a 2D profile polygon. Provide `profile` as [[x,y],...], `depth`, and `axis`.
- `add_curve`: create a tube/pipe along control points. Provide `points` as [[x,y,z],...] and `bevel_depth`.

## Adjustment (when step_history contains a wait_for_adjust entry)

The user's adjustment instruction is in the "Context" block above — find the `step_history` entry for step `wait_for_adjust` and read its `structured.instruction` field. This is what the user wants changed. Make the requested changes.

## When done

Call submit_step_result with: {"built_objects": [...]}
"""

MATERIAL_ARTIST_PROMPT = r"""You are a 3D material artist working in Blender. You interact with the scene exclusively through tool calls.

Read the object_planner's structured result from the "Context" block above (step_history entry for `object_planner`). Each object has a `material_hint` describing its desired appearance (e.g. "gray brick wall", "red lacquer wood").

Call get_scene_state to inspect the current scene, then call set_material on each mesh object, choosing appropriate colors, roughness, and metallic values based on the material_hint.

Work in small batches (5-8 tool calls per turn). Do NOT output all tool calls in a single response.

When done, call submit_step_result with: {"applied_materials": [...]}
"""

LIGHTING_DESIGNER_PROMPT = r"""You are a 3D lighting designer working in Blender. You interact with the scene exclusively through tool calls.

Read the lighting_planner's structured result from the "Context" block above (step_history entry for `lighting_planner`). It contains `lights` (list with type, name, location, energy) and `camera` (location, rotation).

Call add_light for each light, then call set_camera last. Work in small batches. Do NOT output all tool calls in a single response.

When done, call submit_step_result with: {"placed_lights": [...], "camera_set": true}
"""

REVIEWER_PROMPT = r"""You are a scene reviewer. You interact with the scene exclusively through tool calls.

Call get_scene_state (no arguments) to inspect the scene. The result includes, per object: name, type, location, scale, rotation, materials, and light fields.

Review the scene for:
- Objects overlapping or floating
- Missing materials
- Lights too dim or too bright
- Camera angle issues

Then call submit_step_result with: {"passed": true/false, "issues": ["issue1", "issue2"]}
- If the scene looks good: passed=true, issues=[]
- If there are problems: passed=false, issues=[list of problems to fix]
"""


# ── System prompts (per-step) ─────────────────────────────────

SCENE_REFINER_SYSTEM = "You are a professional 3D scene refiner. You take rough scene descriptions and elaborate them into vivid, detailed descriptions suitable for professional 3D artists. You think in terms of visual style, atmosphere, color theory, and spatial composition."

SCENE_ANALYST_SYSTEM = "You are a professional 3D scene analyst. You decompose refined scene descriptions into structured creative briefs: style, atmosphere, color palette, and key visual elements. You think like an art director."

OBJECT_PLANNER_SYSTEM = "You are a professional 3D object planner. You translate creative briefs into batched object plans using primitive shapes (cube, sphere, cylinder, cone, torus, plane). You plan in batches of 5-8 objects, tracking whether more batches remain."

LIGHTING_PLANNER_SYSTEM = "You are a professional 3D lighting planner. You design lighting setups and camera composition that match the scene's atmosphere. You think in terms of key/fill/rim lighting, light temperature, and camera framing."

BUILDER_SYSTEM = "You are a professional Blender operator specializing in modeling. You build and modify 3D scenes by calling tools that execute bpy operations. You understand 3D coordinates (Z-up in Blender), object hierarchy, and boolean/extrude/curve operations. You work efficiently: batching independent tool calls, checking results, and retrying on failure."

MATERIAL_ARTIST_SYSTEM = "You are a professional Blender material artist. You apply PBR materials to mesh objects based on material hints. You understand color theory, roughness, metallic values, and how materials interact with lighting."

LIGHTING_DESIGNER_SYSTEM = "You are a professional Blender lighting designer. You place lights and set up cameras based on a lighting plan. You understand light types (point, sun, area, spot), energy levels, color temperature, and camera composition."

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
    """Builder-only tools: modeling only (no set_material, no add_light, no set_camera)."""
    return ["add_object", "delete_object", "update_object", "boolean_modify",
            "extrude_shape", "add_curve", "get_scene_state", "viewport_refresh"]


def _material_artist_tools() -> list[str]:
    """Material Artist tools: set_material + inspection only."""
    return ["set_material", "get_scene_state", "viewport_refresh"]


def _lighting_designer_tools() -> list[str]:
    """Lighting Designer tools: add_light + set_camera + inspection only."""
    return ["add_light", "set_camera", "get_scene_state", "viewport_refresh"]


def _reviewer_tools() -> list[str]:
    """Reviewer-only tools (review + submit)."""
    return ["get_scene_state"]


# ════════════════════════════════════════════════════════════════
# build_workflow — workflow topology (10+1 steps, 14 edges)
# 工作流拓扑
# ════════════════════════════════════════════════════════════════

def build_workflow() -> dict:
    """Build the expanded 10+1 step workflow.

    构建扩展的 10+1 步工作流。

    Topology:
      scene_refiner → scene_review → scene_analyst → object_planner
        object_planner → builder (has_more=true, batch loop forward)
        object_planner → lighting_planner (has_more=false, batch done)
        builder → object_planner (batch loop back, has_more=true)
        builder → material_artist (batch done, has_more=false)
      lighting_planner → material_artist → lighting_designer → reviewer
        reviewer → renderer (passed)
        reviewer → material_artist (failed, rework)
      renderer → wait_for_adjust → builder (adjust loop)
    """
    return {
        "entry_step": "scene_refiner",
        "steps": [
            {"id": "scene_refiner", "name": "Scene Refiner", "prompt": SCENE_REFINER_PROMPT, "allowed_tools": []},
            {"id": "scene_review", "name": "Scene Review", "executor": "scene_review_executor"},
            {"id": "scene_analyst", "name": "Scene Analyst", "prompt": SCENE_ANALYST_PROMPT, "allowed_tools": []},
            {"id": "object_planner", "name": "Object Planner", "prompt": OBJECT_PLANNER_PROMPT, "allowed_tools": []},
            {"id": "builder", "name": "Builder", "prompt": BUILDER_PROMPT, "allowed_tools": _builder_tools()},
            {"id": "lighting_planner", "name": "Lighting Planner", "prompt": LIGHTING_PLANNER_PROMPT, "allowed_tools": []},
            {"id": "material_artist", "name": "Material Artist", "prompt": MATERIAL_ARTIST_PROMPT, "allowed_tools": _material_artist_tools()},
            {"id": "lighting_designer", "name": "Lighting Designer", "prompt": LIGHTING_DESIGNER_PROMPT, "allowed_tools": _lighting_designer_tools()},
            {"id": "reviewer", "name": "Reviewer", "prompt": REVIEWER_PROMPT, "allowed_tools": _reviewer_tools()},
            {"id": "renderer", "name": "Renderer", "executor": "render_executor"},
            {"id": "wait_for_adjust", "name": "Wait", "executor": "wait_for_adjust"},
        ],
        "edges": [
            {"from": "scene_refiner", "to": "scene_review"},
            {"from": "scene_review", "to": "scene_analyst"},
            {"from": "scene_analyst", "to": "object_planner"},
            # object_planner → builder (batch loop forward) or lighting_planner (batch done)
            {"from": "object_planner", "to": "builder"},
            {"from": "object_planner", "to": "lighting_planner"},
            # builder → object_planner (batch loop back) or material_artist (batch done)
            {"from": "builder", "to": "object_planner"},
            {"from": "builder", "to": "material_artist"},
            {"from": "lighting_planner", "to": "material_artist"},
            {"from": "material_artist", "to": "lighting_designer"},
            {"from": "lighting_designer", "to": "reviewer"},
            # reviewer → renderer (passed) or material_artist (failed, rework)
            {"from": "reviewer", "to": "renderer"},
            {"from": "reviewer", "to": "material_artist"},
            {"from": "renderer", "to": "wait_for_adjust"},
            # wait_for_adjust → builder (adjust loop)
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

    封装 lh.Executor，额外暴露原始回调用于测试。
    SDK 执行器回调是同步的（通过 spawn_blocking 调用）。
    """

    __slots__ = ("callback", "_executor")

    def __init__(self, callback, executor: lh.Executor):
        self.callback = callback
        self._executor = executor

    def __call__(self, *args, **kwargs):
        """Delegate to the raw callback for direct testing."""
        return self.callback(*args, **kwargs)


# ════════════════════════════════════════════════════════════════
# BlenderJudge — batch-loop state in closure
# 路由规则表（含批量循环状态）
# ════════════════════════════════════════════════════════════════

MAX_BUILDER_RUNS = 3  # initial + 2 retries


def create_blender_judge():
    """Create the Blender workflow judge with batch-loop state.

    The Python SDK judge callback receives ctx with:
    - step_id: str — current step ID
    - output: str — step output
    - step_count: int — total steps in history
    - structured: dict | None — structured result

    State tracked via closure:
    - builder_count: number of builder executions (for rework limit)
    - has_more: cached from object_planner's structured output, used to
      route builder back to object_planner (batch loop) or forward to
      material_artist (batch done).

    创建带批量循环状态的 Blender 工作流 judge。
    """
    state = {"builder_count": 0, "has_more": False}

    def judge_cb(ctx: dict) -> str:
        step_id = ctx.get("step_id", "")
        structured = ctx.get("structured")

        def review_passed() -> bool:
            """structured.get("passed"), default True if missing (tolerate glm-5.2)."""
            if structured is None:
                return True
            passed = structured.get("passed")
            if passed is None:
                return True
            return bool(passed)

        def builder_under_limit() -> bool:
            return state["builder_count"] < MAX_BUILDER_RUNS

        # ── Route rules ──
        if step_id == "scene_refiner":
            result = "to:scene_review"

        elif step_id == "scene_review":
            result = "to:scene_analyst"

        elif step_id == "scene_analyst":
            result = "to:object_planner"

        elif step_id == "object_planner":
            # Cache has_more for the subsequent builder routing decision
            has_more = (structured or {}).get("has_more", False)
            state["has_more"] = bool(has_more)
            result = "to:builder" if has_more else "to:lighting_planner"

        elif step_id == "builder":
            # Batch loop: if has_more, go back to object_planner; else forward to material_artist
            if state["has_more"]:
                result = "to:object_planner"
            else:
                result = "to:material_artist"

        elif step_id == "lighting_planner":
            result = "to:material_artist"

        elif step_id == "material_artist":
            result = "to:lighting_designer"

        elif step_id == "lighting_designer":
            result = "to:reviewer"

        elif step_id == "reviewer":
            if review_passed():
                result = "to:renderer"
            elif builder_under_limit():
                result = "to:material_artist"
            else:
                result = "fail:rework limit reached"

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

        logger.info("judge: %s → %s (builder_count=%d, has_more=%s)",
                    step_id, result, state["builder_count"], state["has_more"])
        return result

    return JudgeWrapper(judge_cb, lh.create_judge(judge_cb))


# ════════════════════════════════════════════════════════════════
# SceneReviewExecutor — blocks on queue for human approval
# 场景评审执行器（等待人工审批）
# ════════════════════════════════════════════════════════════════

class SceneReviewHandle:
    """Handle held by the web server to push human approval decisions.

    The executor blocks on a queue.Queue.get(); submit() puts the decision.
    Thread-safe: submit() can be called from any thread.

    Web 服务器持有的句柄，用于推送人工审批决策。
    执行器阻塞在 queue.Queue.get() 上；submit() 放入决策。
    线程安全：submit() 可从任意线程调用。
    """

    def __init__(self):
        self._queue: queue.Queue[tuple[bool, str]] = queue.Queue()

    def submit(self, approved: bool, feedback: str = "") -> None:
        """Push an approval decision into the waiting executor.

        推送审批决策到等待中的执行器。
        """
        self._queue.put((approved, feedback))


def create_scene_review_executor():
    """Create the scene-review executor + handle.

    Returns (executor, handle). The executor blocks (sync, in spawn_blocking)
    until handle.submit(approved, feedback) is called from the web server.

    创建场景评审执行器 + 句柄。
    返回 (executor, handle)。执行器阻塞（同步，在 spawn_blocking 中）
    直到 web 服务器调用 handle.submit(approved, feedback)。
    """
    handle = SceneReviewHandle()

    def executor_cb(ctx: dict) -> dict:
        logger.info("scene_review: blocking until user submits approval")
        approved, feedback = handle._queue.get()
        logger.info("scene_review: received approved=%s, feedback=%s", approved, feedback[:80])
        output = f"approved={approved}" + (f", feedback={feedback}" if feedback else "")
        return {
            "output": output,
            "structured": {"approved": approved, "feedback": feedback},
        }

    executor = ExecutorWrapper(executor_cb, lh.create_executor(executor_cb))
    return executor, handle


# ════════════════════════════════════════════════════════════════
# RenderExecutor — calls bridge.send("render", ...)
# 渲染执行器
# ════════════════════════════════════════════════════════════════

def create_render_executor(bridge, output_dir: str):
    """Create a render executor that calls bridge.send("render", ...).

    The SDK executor callback is synchronous (runs in spawn_blocking).
    bridge.send() is synchronous, so we call it directly.

    创建一个调用 bridge.send("render", ...) 的渲染执行器。
    SDK 执行器回调是同步的（在 spawn_blocking 中运行）。
    bridge.send() 是同步的，直接调用即可。
    """
    def executor_cb(ctx: dict) -> dict:
        # Use output_path from ctx if provided, else generate a unique one
        output_path = ctx.get("output_path") if ctx else None
        if not output_path:
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = f"{output_dir}/render_{timestamp}.png"

        logger.info("render_executor: rendering to %s", output_path)

        # Tell Blender to render — bridge.send is sync
        render_result = bridge.send("render", {"output_path": output_path})
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

    The instruction is returned via `structured` and `output`, which appear
    in the step_history summary that the next builder step reads from its
    "Context" block. We do NOT call engine.set_context_variable() here —
    that would re-borrow the engine while run() already holds &mut self,
    causing `RuntimeError: Already mutably borrowed`.

    The SDK executor callback is synchronous, so we use queue.Queue for
    cross-thread signaling (submit() is called from the web server thread,
    executor runs in a spawn_blocking thread).

    创建等待调整的执行器 + 句柄。
    返回 (executor, handle)。执行器阻塞（同步，在 spawn_blocking 中）
    直到 web 服务器调用 handle.submit(instruction)。
    指令通过 `structured` 和 `output` 返回，进入 step_history 摘要，
    供随后的 builder 步骤从其 "Context" 块中读取。
    不在此处调用 engine.set_context_variable()——那会在 run() 已持有
    &mut self 时再次借用 engine，导致 `RuntimeError: Already mutably borrowed`。
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
        # The instruction is passed via `structured` and `output`, which
        # the engine records into step_history. The builder step reads it
        # from the "Context" block (step_history summary).
        # 指令通过 `structured` 和 `output` 返回，引擎记录到 step_history。
        # builder 步骤从 "Context" 块（step_history 摘要）中读取。
        return {
            "output": f"adjustment: {instruction}",
            "structured": {"instruction": instruction},
        }

    executor = ExecutorWrapper(executor_cb, lh.create_executor(executor_cb))
    return executor, handle
