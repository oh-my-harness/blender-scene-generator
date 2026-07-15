"""Main entry point — Blender path resolution, addon startup, uvicorn server.

Translates REDACTED:
- resolve_blender_path(): BLENDER_PATH env → PATH lookup → macOS default
- resolve_addon_path(): next to binary → CWD → REDACTED (dev mode)
- start_blender_with_addon(): spawn Blender process, wait for TCP socket
- Start uvicorn server on port 3000

主入口——Blender 路径解析、插件启动、uvicorn 服务器。
翻译 REDACTED。
"""
from __future__ import annotations

import logging
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Constants — mirror REDACTED
# 常量——镜像 REDACTED
BLENDER_ADDR = ("127.0.0.1", 9876)
RENDER_DIR = "renders"
SESSION_DIR = "sessions"
SERVER_HOST = "0.0.0.0"
SERVER_PORT = 3000

# Time to wait for the addon socket (seconds).
# 等待插件 socket 的时间（秒）。
_ADDON_TIMEOUT = 30.0
_ADDON_POLL_INTERVAL = 0.5


def resolve_blender_path() -> str:
    """Resolve the Blender executable path.

    解析 Blender 可执行文件路径。

    Order:
    1. BLENDER_PATH env var
    2. PATH lookup (which blender)
    3. Common macOS install location

    Returns:
        Path to the Blender executable.

    Raises:
        FileNotFoundError: If Blender is not found.
    """
    # 1. BLENDER_PATH env var
    env_path = os.environ.get("BLENDER_PATH", "")
    if env_path:
        return env_path

    # 2. PATH lookup
    which = shutil.which("blender")
    if which:
        return which

    # 3. Common macOS location
    mac = "/Applications/Blender.app/Contents/MacOS/Blender"
    if os.path.exists(mac):
        return mac

    raise FileNotFoundError(
        "Blender not found. Set BLENDER_PATH or install Blender "
        "from https://www.blender.org/download/"
    )


def resolve_addon_path() -> str:
    """Resolve the addon.py path.

    解析 addon.py 路径。

    Order:
    1. Next to the executable (binary distribution)
    2. CWD (blender_addon.py in current directory)
    3. Dev mode: blender_scene/blender_addon.py relative to CWD

    Returns:
        Path to addon.py.

    Raises:
        FileNotFoundError: If addon.py is not found.
    """
    # 1. Next to the executable
    exe_dir = Path(sys.argv[0]).resolve().parent
    candidate = exe_dir / "blender_addon.py"
    if candidate.exists():
        return str(candidate)

    # 2. CWD
    if os.path.exists("blender_addon.py"):
        return "blender_addon.py"

    # 3. Dev mode: blender_scene/blender_addon.py
    dev = "blender_scene/blender_addon.py"
    if os.path.exists(dev):
        return dev

    raise FileNotFoundError(
        "blender_addon.py not found. It should be next to the binary or in blender_scene/."
    )


def _is_addon_listening() -> bool:
    """Check if the Blender addon is listening on BLENDER_ADDR.

    检查 Blender 插件是否在 BLENDER_ADDR 上监听。
    """
    try:
        with socket.create_connection(BLENDER_ADDR, timeout=1.0):
            return True
    except (OSError, ConnectionRefusedError):
        return False


def start_blender_with_addon(blender_path: str, addon_path: str) -> subprocess.Popen:
    """Start Blender with the addon, wait for the TCP socket to become reachable.

    启动 Blender 并加载插件，等待 TCP socket 可连接。

    Args:
        blender_path: Path to the Blender executable.
        addon_path: Path to addon.py.

    Returns:
        The Blender subprocess handle.

    Raises:
        RuntimeError: If the addon does not start listening within 30s,
                      or if Blender exits before the addon starts.
    """
    child = subprocess.Popen(
        [blender_path, "--python", addon_path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    deadline = time.monotonic() + _ADDON_TIMEOUT
    while time.monotonic() < deadline:
        if _is_addon_listening():
            return child

        # Check if Blender process exited prematurely.
        # 检查 Blender 进程是否提前退出。
        if child.poll() is not None:
            raise RuntimeError(
                "Blender process exited before addon started. "
                "Check the Blender window for errors."
            )
        time.sleep(_ADDON_POLL_INTERVAL)

    child.kill()
    raise RuntimeError(
        f"Blender addon did not start listening on {BLENDER_ADDR[0]}:{BLENDER_ADDR[1]} "
        f"within {_ADDON_TIMEOUT:.0f}s. Check the Blender window for errors."
    )


# ── Workflow runner ────────────────────────────────────────────

def _run_scene_workflow(state, description: str, skip_refine: bool = False) -> None:
    """Build and run the scene workflow.

    This is the production workflow_runner passed to create_app().
    It mirrors REDACTEDruntime.rs:run_scene_workflow().

    构建并运行场景工作流。
    这是传递给 create_app() 的生产级 workflow_runner。
    镜像 REDACTEDruntime.rs:run_scene_workflow()。

    The engine.run() call blocks (uses rt.block_on internally), so this
    function must be called in a background thread (which server.py does).
    engine.run() 是阻塞调用（内部使用 rt.block_on），
    因此此函数必须在后台线程中调用（server.py 会这样做）。
    """
    import llm_harness_py as lh

    from blender_scene.hooks import create_non_empty_response_hooks, create_steer_on_empty_hook
    from blender_scene.plugin import create_system_prompt_plugin
    from blender_scene.tools import all_blender_tools
    from blender_scene.workflow import (
        build_workflow,
        create_blender_judge,
        create_render_executor,
        create_scene_review_executor,
        create_wait_for_adjust_executor,
        SCENE_REFINER_SYSTEM,
        SCENE_ANALYST_SYSTEM,
        OBJECT_PLANNER_SYSTEM,
        LIGHTING_PLANNER_SYSTEM,
        BUILDER_SYSTEM,
        MATERIAL_ARTIST_SYSTEM,
        LIGHTING_DESIGNER_SYSTEM,
        REVIEWER_SYSTEM,
    )

    # ── Build LLM client from env vars ──
    # 从环境变量构建 LLM 客户端
    provider_type = os.environ.get("LLM_PROVIDER", "openai").lower()

    if provider_type == "anthropic":
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set (LLM_PROVIDER=anthropic)")
        model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
        base_url = os.environ.get("ANTHROPIC_API_BASE", "")
        logger.info("building LLM provider: anthropic model=%s base_url=%s", model, base_url or "(default)")
        provider = lh.create_anthropic_provider(
            api_key,
            base_url=base_url if base_url else None,
        )
    else:
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY not set")
        model = os.environ.get("OPENAI_MODEL", "gpt-4o")
        base_url = os.environ.get("OPENAI_API_BASE", "")
        logger.info("building LLM provider: openai model=%s base_url=%s", model, base_url or "(default)")
        provider = lh.create_openai_provider(
            api_key,
            base_url=base_url if base_url else None,
        )

    # ── Build engine ──
    logger.info("building workflow engine")
    workflow = build_workflow()
    judge = create_blender_judge(skip_refine=skip_refine)

    engine = lh.WorkflowEngine(workflow, provider, model, judge._judge)
    # glm-5.2 的 thinking 很长（~8K token），默认 max_tokens=8192 不够，
    # thinking 还没结束就触发 MaxTokens 截断，导致 text content 和 tool_call 无法输出。
    engine = engine.with_max_tokens(32768)

    # ── Hooks: non-empty response + steer on empty ──
    should_stop_hook, before_run_hook = create_non_empty_response_hooks()
    steer_hook = create_steer_on_empty_hook()
    engine = engine.with_hooks([should_stop_hook._hook, before_run_hook._hook, steer_hook._hook])

    # ── Per-step system prompts ──
    engine = engine.with_step_plugin("scene_refiner", create_system_prompt_plugin(SCENE_REFINER_SYSTEM))
    engine = engine.with_step_plugin("scene_analyst", create_system_prompt_plugin(SCENE_ANALYST_SYSTEM))
    engine = engine.with_step_plugin("object_planner", create_system_prompt_plugin(OBJECT_PLANNER_SYSTEM))
    engine = engine.with_step_plugin("builder", create_system_prompt_plugin(BUILDER_SYSTEM))
    engine = engine.with_step_plugin("lighting_planner", create_system_prompt_plugin(LIGHTING_PLANNER_SYSTEM))
    engine = engine.with_step_plugin("material_artist", create_system_prompt_plugin(MATERIAL_ARTIST_SYSTEM))
    engine = engine.with_step_plugin("lighting_designer", create_system_prompt_plugin(LIGHTING_DESIGNER_SYSTEM))
    engine = engine.with_step_plugin("reviewer", create_system_prompt_plugin(REVIEWER_SYSTEM))

    # ── Review channel ──
    task_id = engine.task_id()
    state.task_id = task_id
    review_handle, review_tool = lh.create_event_channel(task_id)

    # ── Adjust wait executor ──
    adjust_executor, adjust_handle = create_wait_for_adjust_executor()
    state.adjust_handle = adjust_handle

    # ── Event iterator (for WebSocket) ──
    state.event_iterator = engine.subscribe()

    # ── Tools: review tool + all blender tools ──
    engine = engine.with_external_tool(review_tool)
    tools = all_blender_tools(state.bridge)
    for tool in tools:
        engine = engine.with_tool(tool._tool)
    logger.info("engine ready: %d tools registered", len(tools))

    # ── Executors: renderer + wait_for_adjust + scene_review ──
    render_executor = create_render_executor(state.bridge, state.render_dir)
    engine = engine.with_executor("render_executor", render_executor._executor)
    engine = engine.with_executor("wait_for_adjust", adjust_executor._executor)
    scene_review_executor, scene_review_handle = create_scene_review_executor()
    state.scene_review_handle = scene_review_handle
    engine = engine.with_executor("scene_review_executor", scene_review_executor._executor)

    # ── Write user description into context ──
    # The planner step reads this from the context variables.
    # 将用户描述写入上下文。planner 步骤从上下文变量中读取。
    engine.set_context_variable("user_description", description)
    engine.set_context_variable("skip_refine", skip_refine)

    # ── Store engine in state ──
    state.engine = engine

    # ── Run ──
    logger.info("starting workflow engine: task_id=%s", task_id)
    engine.run()
    logger.info("workflow engine finished: task_id=%s", task_id)


def main() -> None:
    """Main entry point — start Blender, build app, run uvicorn.

    主入口——启动 Blender，构建应用，运行 uvicorn。
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    # Check API key early — fail fast before starting Blender.
    # 尽早检查 API key——在启动 Blender 前快速失败。
    provider_type = os.environ.get("LLM_PROVIDER", "openai").lower()
    if provider_type == "anthropic":
        if not os.environ.get("ANTHROPIC_API_KEY", ""):
            print("✗ LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY not set.", file=sys.stderr)
            print('  export ANTHROPIC_API_KEY="sk-ant-..."', file=sys.stderr)
            print('  (also set ANTHROPIC_MODEL and ANTHROPIC_API_BASE if needed)', file=sys.stderr)
            sys.exit(1)
    else:
        if not os.environ.get("OPENAI_API_KEY", ""):
            print("✗ OPENAI_API_KEY not set.", file=sys.stderr)
            print('  export OPENAI_API_KEY="sk-..."', file=sys.stderr)
            print(
                "  (also set OPENAI_API_BASE and OPENAI_MODEL if not using OpenAI directly)",
                file=sys.stderr,
            )
            print('  (set LLM_PROVIDER=anthropic to use Anthropic instead)', file=sys.stderr)
            sys.exit(1)

    # If addon is not already listening, start Blender + addon.
    # 如果插件未在监听，则启动 Blender + 插件。
    if _is_addon_listening():
        logger.info("blender addon already listening, reusing")
        blender_child = None
    else:
        blender_path = resolve_blender_path()
        addon_path = resolve_addon_path()
        logger.info("starting Blender: %s --python %s", blender_path, addon_path)
        blender_child = start_blender_with_addon(blender_path, addon_path)

    logger.info("blender connection verified")

    # Connect the bridge.
    # 连接桥。
    from blender_scene.bridge import BlenderBridge

    bridge = BlenderBridge(host=BLENDER_ADDR[0], port=BLENDER_ADDR[1])
    # Bridge connects lazily on first send — no explicit connect needed.
    # 桥在首次发送时延迟连接——无需显式连接。
    logger.info("blender bridge ready")

    # Ensure render + session dirs exist.
    # 确保渲染和会话目录存在。
    os.makedirs(RENDER_DIR, exist_ok=True)
    os.makedirs(SESSION_DIR, exist_ok=True)

    # Build the FastAPI app.
    # 构建 FastAPI 应用。
    from blender_scene.server import create_app

    app = create_app(
        render_dir=RENDER_DIR,
        bridge=bridge,
        workflow_runner=_run_scene_workflow,
    )

    # Start uvicorn.
    # 启动 uvicorn。
    print("\n  ▶ Blender Scene Generator running")
    print(f"  ▶ Open browser → http://localhost:{SERVER_PORT}")
    print("  ▶ Press Ctrl+C to stop.\n")
    logger.info("web server listening on http://localhost:%d", SERVER_PORT)

    import uvicorn

    uvicorn.run(app, host=SERVER_HOST, port=SERVER_PORT, log_level="info")


if __name__ == "__main__":
    main()
