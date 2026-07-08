"""Tests for the FastAPI web server — TDD failing tests first.

Tests the 5 HTTP endpoints + WebSocket handler, translating the behavior
of REDACTEDroutes.rs and REDACTEDevents.rs.

FastAPI Web 服务器的测试——先写失败测试（TDD）。
测试 5 个 HTTP 端点 + WebSocket 处理器，翻译 REDACTEDroutes.rs 和 events.rs 的行为。
"""
import asyncio
import json
import os

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from blender_scene.server import create_app


# ── Factory ────────────────────────────────────────────────────

@pytest.fixture
def app():
    """Create an app with a no-op workflow runner (no real LLM/Blender needed).

    使用空操作工作流运行器创建 app（不需要真实 LLM/Blender）。
    """
    return create_app(render_dir="renders_test")


@pytest.fixture
def app_with_runner():
    """Create an app with a blocking workflow runner for testing task lifecycle.

    The runner blocks on an Event so task_running stays True until the test
    is done, avoiding races between the background thread and the test.
    """
    import threading
    block = threading.Event()

    def runner(state, desc):
        block.wait(timeout=5)

    app = create_app(render_dir="renders_test", workflow_runner=runner)
    app._test_block = block  # type: ignore[attr-defined]
    return app


# ── POST /api/task ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_task_endpoint_returns_accepted(app):
    """POST /api/task with a description returns 202."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/task", json={"description": "a red cube"})
        assert resp.status_code == 202


@pytest.mark.asyncio
async def test_task_endpoint_conflict_when_already_running(app_with_runner):
    """Second POST /api/task while one is running returns 409."""
    transport = ASGITransport(app=app_with_runner)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp1 = await client.post("/api/task", json={"description": "a red cube"})
        assert resp1.status_code == 202

        # task_running is True because the runner blocks. No need to
        # set it manually.
        resp2 = await client.post("/api/task", json={"description": "a blue sphere"})
        assert resp2.status_code == 409
        # Release the blocking runner so the background thread can finish.
        app_with_runner._test_block.set()


@pytest.mark.asyncio
async def test_task_endpoint_missing_description_returns_422(app):
    """POST /api/task without description returns 422 (validation error)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/task", json={})
        assert resp.status_code == 422


# ── POST /api/adjust ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_adjust_without_task_returns_conflict(app):
    """POST /api/adjust with no active scene returns 409."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/adjust", json={"instruction": "make it bigger"})
        assert resp.status_code == 409


@pytest.mark.asyncio
async def test_adjust_with_handle_returns_accepted(app):
    """POST /api/adjust with an active adjust_handle returns 202."""
    from blender_scene.workflow import AdjustHandle

    app.state.adjust_handle = AdjustHandle()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/adjust", json={"instruction": "make it bigger"})
        assert resp.status_code == 202


@pytest.mark.asyncio
async def test_adjust_missing_instruction_returns_422(app):
    """POST /api/adjust without instruction returns 422."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/adjust", json={})
        assert resp.status_code == 422


# ── POST /api/review ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_review_without_task_returns_conflict(app):
    """POST /api/review with no active review returns 409."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/review", json={"passed": True, "feedback": ""})
        assert resp.status_code == 409


@pytest.mark.asyncio
async def test_review_with_handle_returns_ok(app):
    """POST /api/review with an active review_handle returns 200."""
    # Use a mock handle that has a submit method
    class FakeReviewHandle:
        def submit(self, content, details):
            pass

    app.state.review_handle = FakeReviewHandle()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/review", json={"passed": True, "feedback": "looks good"})
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_review_missing_passed_returns_422(app):
    """POST /api/review without passed field returns 422."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/review", json={"feedback": ""})
        assert resp.status_code == 422


# ── GET /api/render/{filename} ─────────────────────────────────

@pytest.mark.asyncio
async def test_render_not_found_returns_404(app):
    """GET /api/render/nonexistent.png returns 404."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/render/nonexistent.png")
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_render_serves_existing_file(tmp_path):
    """GET /api/render/{filename} serves a file from render_dir."""
    render_dir = tmp_path / "renders"
    render_dir.mkdir()
    img = render_dir / "test.png"
    img.write_bytes(b"\x89PNG fake png data")

    app = create_app(render_dir=str(render_dir))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/render/test.png")
        assert resp.status_code == 200
        assert resp.content == b"\x89PNG fake png data"
        assert resp.headers["content-type"] == "image/png"


# ── GET /ws (WebSocket) ────────────────────────────────────────

@pytest.mark.asyncio
async def test_ws_connects_and_closes(app):
    """WebSocket /ws connects and closes gracefully when no events."""
    from starlette.testclient import TestClient

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            # The WS should connect; without a running workflow, it will
            # timeout after 30s (too long for test). We just verify it connects
            # by receiving nothing immediately and closing.
            pass  # Connection established successfully


# ── AppState ───────────────────────────────────────────────────

def test_app_state_initial_values():
    """AppState initializes with correct defaults."""
    from blender_scene.state import AppState

    state = AppState(render_dir="renders")
    assert state.render_dir == "renders"
    assert state.task_running is False
    assert state.engine is None
    assert state.review_handle is None
    assert state.adjust_handle is None
    assert state.event_iterator is None


def test_app_state_clear_active_task():
    """clear_active_task() resets all task-related fields."""
    from blender_scene.state import AppState

    state = AppState(render_dir="renders")
    state.engine = "fake_engine"
    state.review_handle = "fake_review"
    state.adjust_handle = "fake_adjust"
    state.event_iterator = "fake_iter"
    state.task_running = True

    state.clear_active_task()

    assert state.engine is None
    assert state.review_handle is None
    assert state.adjust_handle is None
    assert state.event_iterator is None
