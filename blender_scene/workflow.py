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

SCENE_REFINER_PROMPT = r"""你是一名 3D 场景细化师。请阅读上方"Context"块中的用户场景描述（键 `user_description`），然后将其细化为一段详细、生动的描述，供专业 3D 美术师构建场景。

请从以下方面展开：
- 视觉风格与氛围
- 关键物体及其空间关系
- 色彩搭配与材质建议
- 光影情绪与相机构图

先用文本输出细化后的描述，然后调用 submit_step_result 工具，传入 JSON 对象：{"refined_description": "你细化后的描述文本"}。

不要将 JSON 作为文本输出——它必须放在工具调用的 result 参数中。
"""

SCENE_ANALYST_PROMPT = r"""你是一名 3D 场景分析师。请阅读上方"Context"块中 `scene_refiner` 步骤的 `step_history` 条目，读取其 `structured` 字段（`{"refined_description": "..."}`）。

分析细化后的描述，提取结构化的创意简报：
- `style`：整体视觉风格（如"极简现代"、"温馨低多边形"）
- `atmosphere`：氛围与情绪（如"温暖宜人"、"冷峻工业风"）
- `color_palette`：主色调列表，用十六进制或描述性名称
- `key_elements`：最重要的视觉元素列表

调用 submit_step_result 工具，传入：{"style": "...", "atmosphere": "...", "color_palette": [...], "key_elements": [...]}

不要将 JSON 作为文本输出——它必须放在工具调用的 result 参数中。
"""

OBJECT_PLANNER_PROMPT = r"""你是一名 3D 物体规划师。请阅读上方"Context"块中 `scene_analyst` 步骤的 `step_history` 条目，读取其 `structured` 字段。

分批规划物体，每批 5-8 个。为每个物体输出：名称(name)、类型(type: cube|sphere|cylinder|plane|cone|torus)、位置(location [x,y,z])、缩放(scale [x,y,z])和材质提示(material_hint，简短的外观描述)。

如果还有更多物体需要规划，将 `has_more` 设为 true。如果这是最后一批，将 `has_more` 设为 false。

调用 submit_step_result 工具，传入：{"objects": [{"name": "...", "type": "...", "location": [x,y,z], "scale": [x,y,z], "material_hint": "..."}], "has_more": true/false}

不要将 JSON 作为文本输出——它必须放在工具调用的 result 参数中。
"""

LIGHTING_PLANNER_PROMPT = r"""你是一名 3D 灯光规划师。请阅读上方"Context"块中 `scene_analyst` 步骤的 `step_history` 条目，读取其 `structured` 字段。

规划场景的灯光布置和相机：
- `lights`：灯光列表，包含类型(type: point|sun|area|spot)、名称(name)、位置(location [x,y,z])和能量(energy)
- `camera`：位置(location [x,y,z])和旋转(rotation [rx,ry,rz]，弧度)

调用 submit_step_result 工具，传入：{"lights": [...], "camera": {"location": [...], "rotation": [...]}}

不要将 JSON 作为文本输出——它必须放在工具调用的 result 参数中。
"""

BUILDER_PROMPT = r"""你是一名 3D 场景构建师，在 Blender 中工作。你仅通过工具调用来操作场景。

## 常规构建

当前批次的物体在上方"Context"块中——找到 `object_planner` 步骤的 `step_history` 条目，读取其 `structured.objects` 字段。每个物体有一个 `material_hint` 描述其外观。

将当前批次的物体分小组构建（每轮 5-8 次工具调用）：
1. 调用 add_object 创建几个物体。等待工具返回结果后再继续。
2. 如果某次调用失败，调整参数后在下一轮重试。
不要在单次响应中输出所有工具调用。

## 高级工具（超越基本图元）

- `boolean_modify`：用临时切割体对现有网格进行雕刻、合并或相交操作。
- `extrude_shape`：从 2D 轮廓多边形创建棱柱网格。提供 `profile`（[[x,y],...]）、`depth` 和 `axis`。
- `add_curve`：沿控制点创建管道。提供 `points`（[[x,y,z],...]）和 `bevel_depth`。

## 调整（当 step_history 中包含 wait_for_adjust 条目时）

用户的调整指令在上方"Context"块中——找到 `wait_for_adjust` 步骤的 `step_history` 条目，读取其 `structured.instruction` 字段。这是用户想要修改的内容。请执行所需的修改。

## 完成时

调用 submit_step_result，传入：{"built_objects": [...]}
"""

MATERIAL_ARTIST_PROMPT = r"""你是一名 3D 材质师，在 Blender 中工作。你仅通过工具调用来操作场景。

阅读上方"Context"块中 `object_planner` 步骤的结构化结果。每个物体有一个 `material_hint` 描述其期望外观（如"灰色砖墙"、"红色漆面木头"）。

调用 get_scene_state 检查当前场景，然后对每个网格物体调用 set_material，根据 material_hint 选择合适的颜色、粗糙度和金属度。

分小组工作（每轮 5-8 次工具调用）。不要在单次响应中输出所有工具调用。

完成后，调用 submit_step_result，传入：{"applied_materials": [...]}
"""

LIGHTING_DESIGNER_PROMPT = r"""你是一名 3D 灯光师，在 Blender 中工作。你仅通过工具调用来操作场景。

阅读上方"Context"块中 `lighting_planner` 步骤的结构化结果。其中包含 `lights`（列表，含类型、名称、位置、能量）和 `camera`（位置、旋转）。

为每个灯光调用 add_light，最后调用 set_camera。分小组工作。不要在单次响应中输出所有工具调用。

完成后，调用 submit_step_result，传入：{"placed_lights": [...], "camera_set": true}
"""

REVIEWER_PROMPT = r"""你是一名场景审查员。你仅通过工具调用来操作场景。

调用 get_scene_state（无需参数）检查场景。结果包含每个物体的：名称、类型、位置、缩放、旋转、材质和灯光字段。

审查以下方面：
- 物体是否重叠或悬浮
- 是否缺少材质
- 灯光是否太暗或太亮
- 相机角度是否有问题

然后调用 submit_step_result，传入：{"passed": true/false, "issues": ["问题1", "问题2"]}
- 如果场景看起来不错：passed=true, issues=[]
- 如果存在问题：passed=false, issues=[需要修复的问题列表]
"""


# ── System prompts (per-step) ─────────────────────────────────

SCENE_REFINER_SYSTEM = "你是一名专业的 3D 场景细化师。你将粗略的场景描述展开为生动、详细的描述，供专业 3D 美术师使用。你从视觉风格、氛围、色彩理论和空间构图的角度思考。"

SCENE_ANALYST_SYSTEM = "你是一名专业的 3D 场景分析师。你将细化后的场景描述分解为结构化的创意简报：风格、氛围、色彩搭配和关键视觉元素。你像艺术总监一样思考。"

OBJECT_PLANNER_SYSTEM = "你是一名专业的 3D 物体规划师。你将创意简报转化为使用基本图元（立方体、球体、圆柱、圆锥、圆环、平面）的分批物体规划。你以每批 5-8 个物体的方式规划，并跟踪是否还有更多批次。"

LIGHTING_PLANNER_SYSTEM = "你是一名专业的 3D 灯光规划师。你设计匹配场景氛围的灯光布置和相机构图。你从主光/补光/轮廓光、色温和相机取景的角度思考。"

BUILDER_SYSTEM = "你是一名专业的 Blender 操作员，专精于建模。你通过调用执行 bpy 操作的工具来构建和修改 3D 场景。你理解 3D 坐标（Blender 中 Z 轴朝上）、物体层级以及布尔/挤出/曲线操作。你高效工作：批量调用独立工具、检查结果、失败时重试。"

MATERIAL_ARTIST_SYSTEM = "你是一名专业的 Blender 材质师。你根据材质提示为网格物体应用 PBR 材质。你理解色彩理论、粗糙度、金属度，以及材质与灯光的交互。"

LIGHTING_DESIGNER_SYSTEM = "你是一名专业的 Blender 灯光师。你根据灯光计划放置灯光和设置相机。你理解灯光类型（点光、太阳光、面光、聚光灯）、能量级别、色温和相机构图。"

REVIEWER_SYSTEM = "你是一名专业的 3D 场景审查员。你通过查询场景状态来检查 Blender 场景，评估物体放置、材质、灯光和相机构图。你简洁地报告问题并做出通过/不通过的判定。"


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
            # object_planner → builder (always, builder handles batch loop)
            {"from": "object_planner", "to": "builder"},
            # builder → object_planner (batch loop back) or lighting_planner (batch done)
            {"from": "builder", "to": "object_planner"},
            {"from": "builder", "to": "lighting_planner"},
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
            # Always go to builder first — builder will route back to
            # object_planner if has_more, or forward to material_artist.
            result = "to:builder"

        elif step_id == "builder":
            # Batch loop: if has_more, go back to object_planner for next batch;
            # else forward to lighting_planner (which precedes material_artist).
            if state["has_more"]:
                result = "to:object_planner"
            else:
                result = "to:lighting_planner"

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
