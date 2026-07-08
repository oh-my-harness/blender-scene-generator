"""Tests for NonEmptyResponseHook and SteerOnEmptyHook.

NonEmptyResponseHook 和 SteerOnEmptyHook 的测试。
翻译自 REDACTEDhooks.rs 的 tests 模块。
"""
import llm_harness_py as lh
from blender_scene.hooks import (
    create_non_empty_response_hooks,
    create_steer_on_empty_hook,
    _is_empty_response,
)


# ── _is_empty_response ────────────────────────────────────────

def test_is_empty_response_empty_list():
    """No content blocks → empty."""
    assert _is_empty_response([]) is True


def test_is_empty_response_text_only():
    """Text but no tool_use → empty (the glm-5.2 failure mode)."""
    content = [{"type": "text", "text": "hello"}]
    assert _is_empty_response(content) is True


def test_is_empty_response_thinking_only():
    """Thinking-only (no text, no tool) → empty."""
    content = [{"type": "thinking", "thinking": "I should call a tool", "signature": None}]
    assert _is_empty_response(content) is True


def test_is_empty_response_tool_use():
    """ToolUse block → not empty."""
    content = [{"type": "tool_use", "id": "1", "name": "get_scene_state", "input": {}}]
    assert _is_empty_response(content) is False


def test_is_empty_response_mixed_with_tool_use():
    """Text + ToolUse → not empty."""
    content = [
        {"type": "text", "text": "checking"},
        {"type": "tool_use", "id": "1", "name": "get_scene_state", "input": {}},
    ]
    assert _is_empty_response(content) is False


# ── create_non_empty_response_hooks ───────────────────────────

def test_non_empty_response_hooks_exist():
    """Factory returns (should_stop_hook, before_run_hook), both non-None."""
    should_stop, before_run = create_non_empty_response_hooks()
    assert should_stop is not None
    assert before_run is not None


def test_non_empty_response_hooks_are_hook_wrappers():
    """Both hooks must expose a callable callback and an lh.Hook."""
    should_stop, before_run = create_non_empty_response_hooks()
    assert hasattr(should_stop, "callback")
    assert hasattr(before_run, "callback")
    assert hasattr(should_stop, "_hook")
    assert hasattr(before_run, "_hook")


def test_should_stop_returns_true_for_tool_use():
    """A response with a tool_use block → should_stop = True (stop normally)."""
    should_stop, _ = create_non_empty_response_hooks(max_retries=3)
    ctx = {
        "turn_index": 0,
        "stop_reason": "end_turn",
        "last_assistant": {
            "content": [{"type": "tool_use", "id": "1", "name": "get_scene_state", "input": {}}],
        },
    }
    assert should_stop(ctx) is True


def test_should_stop_returns_false_for_empty():
    """An empty response (no tool_use) → should_stop = False (retry)."""
    should_stop, _ = create_non_empty_response_hooks(max_retries=3)
    ctx = {
        "turn_index": 0,
        "stop_reason": "end_turn",
        "last_assistant": {"content": []},
    }
    assert should_stop(ctx) is False


def test_should_stop_returns_false_for_text_only():
    """Text but no tool_use → should_stop = False (retry)."""
    should_stop, _ = create_non_empty_response_hooks(max_retries=3)
    ctx = {
        "turn_index": 0,
        "stop_reason": "end_turn",
        "last_assistant": {
            "content": [{"type": "text", "text": "I'll check the scene state"}],
        },
    }
    assert should_stop(ctx) is False


def test_should_stop_gives_up_after_max_retries():
    """After max_retries consecutive empty responses → should_stop = True."""
    should_stop, _ = create_non_empty_response_hooks(max_retries=3)
    empty_ctx = {
        "turn_index": 0,
        "stop_reason": "end_turn",
        "last_assistant": {"content": []},
    }
    # First 3 empty responses → False (retrying)
    assert should_stop(empty_ctx) is False  # attempt 1
    assert should_stop(empty_ctx) is False  # attempt 2
    assert should_stop(empty_ctx) is False  # attempt 3
    # 4th empty → True (giving up)
    assert should_stop(empty_ctx) is True


def test_should_stop_resets_counter_on_non_empty():
    """A non-empty response resets the counter to 0."""
    should_stop, _ = create_non_empty_response_hooks(max_retries=3)
    empty_ctx = {
        "turn_index": 0,
        "stop_reason": "end_turn",
        "last_assistant": {"content": []},
    }
    tool_ctx = {
        "turn_index": 0,
        "stop_reason": "end_turn",
        "last_assistant": {
            "content": [{"type": "tool_use", "id": "1", "name": "get_scene_state", "input": {}}],
        },
    }
    # Two empty → counter at 2
    assert should_stop(empty_ctx) is False
    assert should_stop(empty_ctx) is False
    # Non-empty → resets counter, returns True (normal stop)
    assert should_stop(tool_ctx) is True
    # Now empty again → counter starts from 0
    assert should_stop(empty_ctx) is False


def test_before_run_resets_counter():
    """before_run hook resets the empty counter to 0."""
    should_stop, before_run = create_non_empty_response_hooks(max_retries=3)
    empty_ctx = {
        "turn_index": 0,
        "stop_reason": "end_turn",
        "last_assistant": {"content": []},
    }
    # Consume 2 retries
    assert should_stop(empty_ctx) is False
    assert should_stop(empty_ctx) is False
    # before_run resets
    before_run({"prompt_text": "", "initial_messages": [], "system_prompt": None})
    # Counter reset → first empty is retry 1 again
    assert should_stop(empty_ctx) is False
    assert should_stop(empty_ctx) is False
    assert should_stop(empty_ctx) is False
    assert should_stop(empty_ctx) is True


# ── create_steer_on_empty_hook ────────────────────────────────

def test_steer_on_empty_hook_exists():
    """Factory returns a transform_context hook."""
    hook = create_steer_on_empty_hook()
    assert hook is not None


def test_steer_on_empty_hook_is_hook_wrapper():
    """The hook must expose a callable callback and an lh.Hook."""
    hook = create_steer_on_empty_hook()
    assert hasattr(hook, "callback")
    assert hasattr(hook, "_hook")


def test_steer_injects_nudge_after_empty_assistant():
    """Empty assistant reply → nudge user message appended."""
    hook = create_steer_on_empty_hook()
    ctx = {
        "system_prompt": None,
        "messages": [
            {"role": "assistant", "content": [], "message_id": "t1", "turn_id": "t1",
             "kind": "final_answer", "stop_reason": "end_turn", "timestamp": ""},
        ],
    }
    result = hook(ctx)
    assert len(result["messages"]) == 2
    last = result["messages"][-1]
    assert last["role"] == "user"


def test_steer_injects_after_text_without_tool_use():
    """Text but no tool_use → nudge injected."""
    hook = create_steer_on_empty_hook()
    ctx = {
        "system_prompt": None,
        "messages": [
            {"role": "assistant", "content": [{"type": "text", "text": "hi"}],
             "message_id": "t1", "turn_id": "t1", "kind": "final_answer",
             "stop_reason": "end_turn", "timestamp": ""},
        ],
    }
    result = hook(ctx)
    assert len(result["messages"]) == 2


def test_steer_does_not_inject_after_tool_use():
    """ToolUse block present → no nudge."""
    hook = create_steer_on_empty_hook()
    ctx = {
        "system_prompt": None,
        "messages": [
            {"role": "assistant",
             "content": [{"type": "tool_use", "id": "1", "name": "get_scene_state", "input": {}}],
             "message_id": "t1", "turn_id": "t1", "kind": "final_answer",
             "stop_reason": "end_turn", "timestamp": ""},
        ],
    }
    result = hook(ctx)
    assert len(result["messages"]) == 1


def test_steer_no_inject_when_last_is_user():
    """Last message is user, not assistant → no nudge."""
    hook = create_steer_on_empty_hook()
    ctx = {
        "system_prompt": None,
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "hi"}], "timestamp": ""},
        ],
    }
    result = hook(ctx)
    assert len(result["messages"]) == 1


def test_steer_no_inject_when_no_messages():
    """Empty messages list → no nudge."""
    hook = create_steer_on_empty_hook()
    ctx = {"system_prompt": None, "messages": []}
    result = hook(ctx)
    assert len(result["messages"]) == 0
