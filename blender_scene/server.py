"""FastAPI web server — 5 HTTP endpoints + WebSocket handler.

Translates REDACTEDroutes.rs and REDACTEDevents.rs to FastAPI.

Endpoints:
- POST /api/task    — submit a scene description, start workflow in background
- POST /api/adjust  — push an adjustment instruction into the waiting executor
- POST /api/review  — submit a human review decision
- GET  /api/status — return a snapshot of the workflow runtime state
- GET  /api/render/{filename} — serve a rendered image file

FastAPI Web 服务器——5 个 HTTP 端点 + WebSocket 处理器。
翻译 REDACTEDroutes.rs 和 REDACTEDevents.rs。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from typing import Any, Callable

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from blender_scene.state import AppState

# Configure the blender_scene logger explicitly so it survives uvicorn's
# root logger reconfiguration. Without this, uvicorn overwrites the root
# handler and our workflow logs are silently swallowed.
_bs_logger = logging.getLogger("blender_scene")
_bs_logger.setLevel(logging.INFO)
_bs_handler = logging.StreamHandler()
_bs_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
_bs_logger.addHandler(_bs_handler)
_bs_logger.propagate = False
logger = logging.getLogger(__name__)

# WebSocket event polling timeout (seconds).
# Mirrors REDACTEDroutes.rs:131 — wait up to 30s for event_iterator.
# WebSocket 事件轮询超时（秒）。镜像 routes.rs 的 30 秒等待。
_WS_EVENT_TIMEOUT = 30.0



# ── Request models ─────────────────────────────────────────────

class TaskRequest(BaseModel):
    """POST /api/task request body."""
    description: str


class AdjustRequest(BaseModel):
    """POST /api/adjust request body."""
    instruction: str


class ReviewRequest(BaseModel):
    """POST /api/review request body."""
    passed: bool
    feedback: str = ""



class SceneReviewRequest(BaseModel):
    """POST /api/scene-review request body."""
    approved: bool
    feedback: str = ""

# ── App factory ────────────────────────────────────────────────

def create_app(
    render_dir: str = "renders",
    bridge: Any = None,
    workflow_runner: Callable[[AppState, str], Any] | None = None,
) -> FastAPI:
    """Create the FastAPI application.

    创建 FastAPI 应用。

    Args:
        render_dir: Directory where rendered images are saved.
        bridge: Shared BlenderBridge (None in test mode).
        workflow_runner: Callable(state, description) that runs the workflow.
                         If None, POST /api/task returns 202 but does nothing.
                         If None，POST /api/task 返回 202 但不执行任何操作。
    """
    state = AppState(render_dir=render_dir, bridge=bridge)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield

    app = FastAPI(lifespan=lifespan)
    app.state = state  # type: ignore[assignment]

    # ── POST /api/task ──────────────────────────────────────────

    @app.post("/api/task")
    async def submit_task(req: TaskRequest):
        """Submit a scene description and start the workflow.

        提交场景描述并启动工作流。

        Returns 202 if the task was started, 409 if a task is already running.
        """
        if state.task_running:
            return JSONResponse(
                status_code=409,
                content={"error": "a task is already running"},
            )

        state.task_running = True

        # Spawn the workflow in a background thread.
        # engine.run() is a blocking call (uses rt.block_on internally),
        # so we cannot run it on the asyncio event loop.
        # engine.run() 是阻塞调用（内部使用 rt.block_on），
        # 因此不能在 asyncio 事件循环上运行。
        def _run_in_background():
            try:
                if workflow_runner is not None:
                    logger.info("workflow starting: %s", req.description[:80])
                    workflow_runner(state, req.description)
                    logger.info("workflow completed")
            except Exception:
                logger.exception("workflow failed")
            finally:
                state.task_running = False
                state.clear_active_task()

        thread = threading.Thread(target=_run_in_background, daemon=True)
        thread.start()

        return JSONResponse(
            status_code=202,
            content={"message": "task started"},
        )

    # ── POST /api/adjust ────────────────────────────────────────

    @app.post("/api/adjust")
    async def adjust_scene(req: AdjustRequest):
        """Push an adjustment instruction into the waiting wait_for_adjust executor.

        推送调整指令到等待中的 wait_for_adjust 执行器。

        Returns 202 if submitted, 409 if no active scene to adjust.
        """
        handle = state.adjust_handle
        if handle is None:
            return JSONResponse(
                status_code=409,
                content={"error": "no active scene to adjust — generate a scene first"},
            )

        handle.submit(req.instruction)
        return JSONResponse(
            status_code=202,
            content={"message": "adjust submitted"},
        )

    # ── POST /api/review ────────────────────────────────────────

    @app.post("/api/review")
    async def submit_review(req: ReviewRequest):
        """Submit a human review decision.

        提交人工审核决策。

        Returns 200 if submitted, 409 if no active review.
        """
        handle = state.review_handle
        if handle is None:
            return JSONResponse(
                status_code=409,
                content={"error": "no active review"},
            )

        # The review handle's submit takes (content, details).
        # content is a JSON string of the decision; details is the dict.
        # review 句柄的 submit 接收 (content, details)。
        # content 是决策的 JSON 字符串；details 是字典。
        decision = {"passed": req.passed, "feedback": req.feedback}
        content = json.dumps(decision, indent=2)
        handle.submit(content, decision)
        return JSONResponse(
            status_code=200,
            content={"message": "review submitted"},
        )

    # ── POST /api/scene-review ──────────────────────────────────

    @app.post("/api/scene-review")
    async def submit_scene_review(req: SceneReviewRequest):
        """Submit a human scene review decision (approve/reject refined description).

        提交人工场景审批决策（批准/拒绝细化后的描述）。

        Returns 200 if submitted, 409 if no active scene review.
        """
        handle = state.scene_review_handle
        if handle is None:
            return JSONResponse(
                status_code=409,
                content={"error": "no active scene review"},
            )

        handle.submit(req.approved, req.feedback)
        return JSONResponse(
            status_code=200,
            content={"message": "scene review submitted"},
        )

    # ── GET /api/status ─────────────────────────────────────────

    @app.get("/api/status")
    async def get_status():
        """Return a snapshot of the workflow runtime state.

        Exposes 0.3.0 observability: current_step, state, total_cost,
        step_history. Returns an idle shape when no engine is active.

        返回工作流运行时状态快照。
        暴露 0.3.0 观测能力：current_step、state、total_cost、step_history。
        无活跃 engine 时返回 idle 形态。
        """
        return JSONResponse(status_code=200, content=state.status_snapshot())

    # ── GET /api/render/{filename} ──────────────────────────────

    @app.get("/api/render/{filename}")
    async def get_render(filename: str):
        """Serve a rendered image file from render_dir.

        从 render_dir 提供渲染图片文件。

        Returns 200 with the file if found, 404 otherwise.
        """
        # Prevent path traversal — only allow the basename.
        # 防止路径遍历——只允许文件名。
        safe_name = os.path.basename(filename)
        path = os.path.join(state.render_dir, safe_name)

        if os.path.isfile(path):
            return FileResponse(path, media_type="image/png")
        return PlainTextResponse("not found", status_code=404)

    # ── WS /ws ──────────────────────────────────────────────────

    @app.websocket("/ws")
    async def ws_handler(websocket: WebSocket):
        """Forward WorkflowEvents to the client.

        Waits for the event_iterator to become available (up to 30s),
        then forwards events as JSON. Tolerates Lagged (continue) and
        Closed (break) — mirrors events.rs:89-101.

        转发工作流事件到客户端。
        等待事件迭代器可用（最多 30 秒），然后转发事件为 JSON。
        容忍 Lagged（继续）和 Closed（中断）——镜像 events.rs:89-101。
        """
        await websocket.accept()

        # Poll for the event iterator to become available (up to 30s).
        # 轮询等待事件迭代器可用（最多 30 秒）。
        iterator = None
        deadline = asyncio.get_event_loop().time() + _WS_EVENT_TIMEOUT
        while iterator is None:
            iterator = state.event_iterator
            if iterator is not None:
                break
            if asyncio.get_event_loop().time() >= deadline:
                # Timeout waiting for a workflow to start; close the socket.
                # 等待工作流启动超时；关闭连接。
                await websocket.close()
                return
            await asyncio.sleep(0.1)

        # Forward events to the client.
        # 转发事件到客户端。
        #
        # The SDK iterator's __next__ blocks for its timeout, then returns:
        # - a dict event on success
        # - {"type": "lagged", ...} on Lagged (skipped events)
        # - None on timeout OR channel close (the SDK does not distinguish)
        #
        # To tell them apart: on timeout, __next__ blocks for ~5s before
        # returning None; on channel close, it returns None immediately.
        # We time each call — if None comes back in <1s, it's almost certainly
        # a closed channel, so we break the loop and close the WS.
        # SDK 迭代器的 __next__ 阻塞至超时后返回 None；通道关闭时立即返回 None。
        # 通过计时区分：<1s 返回 None → 通道关闭 → 中断循环。
        try:
            while True:
                t0 = time.monotonic()
                event = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: _next_event(iterator)
                )
                elapsed = time.monotonic() - t0

                if event is None:
                    if elapsed < 1.0:
                        # Channel closed — stop forwarding.
                        # 通道关闭——停止转发。
                        break
                    # Timeout — keep polling to keep WS alive.
                    # 超时——继续轮询以保持 WS 存活。
                    continue

                # Skip Lagged events (dropped events) — continue.
                # 跳过 Lagged 事件（丢弃的事件）——继续。
                if isinstance(event, dict) and event.get("type") == "lagged":
                    continue

                await websocket.send_text(json.dumps(event))
        except WebSocketDisconnect:
            # Client disconnected — stop forwarding.
            # 客户端断开连接——停止转发。
            pass
        except Exception as e:
            logger.error("websocket error: %s", e)
        finally:
            try:
                await websocket.close()
            except Exception:
                pass

    # ── Static files (must be after all API routes) ──
    # 静态文件（必须在所有 API 路由之后挂载）
    app.mount("/", StaticFiles(directory="static", html=True), name="static")

    return app


# Module-level app for uvicorn (run.sh uses `blender_scene.server:app`).
# 模块级 app，供 uvicorn 使用（run.sh 使用 `blender_scene.server:app`）。
app = create_app()


def _next_event(iterator: Any) -> Any:
    """Call next() on the iterator, returning None on StopIteration or error.

    对迭代器调用 next()，StopIteration 或错误时返回 None。
    """
    try:
        return next(iterator)
    except StopIteration:
        return None
    except Exception:
        return None
