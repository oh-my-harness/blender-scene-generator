"""NonEmptyResponseHook and SteerOnEmptyHook — Python translation of hooks.rs.

NonEmptyResponseHook: should_stop + before_run hooks that prevent the agent
loop from terminating when the LLM produces an empty response (no tool calls).
Reasoning models like glm-5.2 sometimes put all output in the thinking channel
with empty text content and EndTurn stop_reason.

SteerOnEmptyHook: transform_context hook that nudges the LLM when its previous
reply was empty, appending a user message that demands a tool call.

防止空响应终止的 hook 和空响应引导 hook——hooks.rs 的 Python 翻译。
"""
import datetime
import llm_harness_py as lh


# ── Helpers ────────────────────────────────────────────────────

def _is_empty_response(content: list) -> bool:
    """Check if the content list has no tool_use blocks.

    检查内容块列表中是否没有 tool_use 块。
    glm-5.2 有时输出思考 + 一行文本（如"我先检查场景状态"）但没有 tool_use 块。
    没有此检查，agent 循环会将其视为有效的最终答案并停止。
    """
    if not content:
        return True
    return not any(b.get("type") == "tool_use" for b in content)


# The nudge text injected by SteerOnEmptyHook. Must match hooks.rs:125-132.
_NUDGE_TEXT = (
    "Your previous reply was rejected: it contained no tool call. "
    "You MUST call a tool RIGHT NOW in this response. "
    "Look at the tools available to you and call one immediately. "
    "If you are planning or done with your task, call submit_step_result with your result. "
    "If you are building or adjusting a scene, call get_scene_state with no arguments. "
    "If you are reviewing, call get_scene_state. "
    "Do not explain. Do not reason. Just emit the tool_use block immediately."
)


# ── Hook wrapper ───────────────────────────────────────────────

class HookWrapper:
    """Wraps an lh.Hook, exposing the raw callback for testing.

    封装 lh.Hook，额外暴露原始回调函数用于测试。
    lh.create_*_hook 返回的 Hook 对象不可从 Python 直接调用，
    因此用此包装类补充 callback 属性。
    """

    __slots__ = ("callback", "_hook")

    def __init__(self, callback, hook: lh.Hook):
        self.callback = callback
        self._hook = hook

    def __call__(self, *args, **kwargs):
        """Delegate to the raw callback for direct testing.

        委托给原始回调，便于直接测试。
        """
        return self.callback(*args, **kwargs)


# ── NonEmptyResponseHook ───────────────────────────────────────

def create_non_empty_response_hooks(max_retries: int = 3):
    """Create the should_stop + before_run hook pair sharing a retry counter.

    Rust uses AtomicU32; Python uses a closure-captured dict `state`.
    The should_stop callback returns False (don't stop) when the assistant
    message has no tool calls, giving the model another chance. After
    max_retries consecutive empty responses, it gives up and returns True.

    创建共享重试计数器的 should_stop + before_run hook 对。
    Rust 使用 AtomicU32；Python 使用闭包捕获的 dict state。
    """
    state = {"empty_count": 0}

    def should_stop_cb(ctx: dict) -> bool:
        """Return True to stop, False to force another turn.

        当助手消息没有 tool_use 块时返回 False（不停），给模型另一次机会。
        连续 max_retries 次空响应后放弃，返回 True（停）。
        """
        last_assistant = ctx.get("last_assistant", {})
        content = last_assistant.get("content", [])

        if _is_empty_response(content):
            count = state["empty_count"]
            state["empty_count"] = count + 1
            if count < max_retries:
                return False  # retry — don't stop
            # exhausted retries → stop
            return True
        else:
            state["empty_count"] = 0
            return True  # normal stop

    def before_run_cb(ctx: dict):
        """Reset the retry counter at the start of each step.

        在每步开始时重置重试计数器，使一步消耗的重试不会延续到下一步。
        """
        state["empty_count"] = 0
        return None

    should_stop_hook = HookWrapper(
        should_stop_cb, lh.create_should_stop_hook(should_stop_cb)
    )
    before_run_hook = HookWrapper(
        before_run_cb, lh.create_before_run_hook(before_run_cb)
    )
    return should_stop_hook, before_run_hook


# ── SteerOnEmptyHook ───────────────────────────────────────────

def create_steer_on_empty_hook():
    """Create a transform_context hook that nudges the LLM after an empty reply.

    NonEmptyResponseHook already retries by returning should_stop=False, but
    the retried turn sees the identical prompt. This hook breaks the loop:
    at the start of each turn it inspects the last message; if it's an empty
    assistant reply, it appends a short user message demanding a tool call.

    创建一个 transform_context hook，在空响应后引导 LLM。
    """
    def transform_cb(ctx: dict) -> dict:
        """Inspect last message; if empty assistant reply, append nudge.

        检查最后一条消息；如果是空的助手回复，追加一条要求工具调用的用户消息。
        """
        messages = ctx.get("messages", [])
        if not messages:
            return ctx

        last = messages[-1]
        if last.get("role") != "assistant":
            return ctx

        content = last.get("content", [])
        if not _is_empty_response(content):
            return ctx

        # Append the nudge as a user message.
        # timestamp must be valid RFC3339 — the SDK deserializes messages via
        # serde_json::from_value::<Vec<AgentMessage>>, and UserMessage.timestamp
        # is DateTime<Utc> with no serde(default); empty string fails.
        nudge_msg = {
            "role": "user",
            "content": [{"type": "text", "text": _NUDGE_TEXT}],
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        new_messages = list(messages) + [nudge_msg]
        return {
            "system_prompt": ctx.get("system_prompt"),
            "messages": new_messages,
        }

    return HookWrapper(transform_cb, lh.create_transform_context_hook(transform_cb))
