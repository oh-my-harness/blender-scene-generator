"""AppState — shared application state across all HTTP/WS handlers.

Translates REDACTEDstate.rs. Holds the persistent workflow engine,
review/adjust handles, event iterator, and task_running flag.

The Rust version uses tokio::sync::Mutex for interior mutability. In Python,
we use plain attributes — the FastAPI server runs on a single asyncio event
loop, and the workflow runs in a background thread. Access to task_running
and handles is serialized by the GIL + asyncio's cooperative scheduling.

共享应用状态——所有 HTTP/WS 处理器共享。
翻译 REDACTEDstate.rs。持有持久化的工作流引擎、review/adjust 句柄、
事件迭代器和 task_running 标志。
"""
from __future__ import annotations

from typing import Any


class AppState:
    """Shared application state across all HTTP/WS handlers.

    所有 HTTP/WS 处理器共享的应用状态。

    Fields:
        render_dir: Directory where rendered images are saved.
                    渲染图片保存目录。
        bridge: Shared BlenderBridge for tool construction (None in test mode).
                共享的 BlenderBridge（测试模式下为 None）。
        task_running: Whether a workflow task is currently running
                      (single-task enforcement).
                      工作流任务是否正在运行（单任务强制）。
        engine: The persistent workflow engine. Set when a task is submitted;
                stays alive across adjust rounds so step_history and context
                accumulate.
                持久化的工作流引擎。提交任务时设置；跨调整轮次保持存活。
        review_handle: Handle to push human review decisions into the waiting
                       review tool. Set when a task starts.
                       推送人工审核决策的句柄。任务启动时设置。
        adjust_handle: Handle to push adjustment instructions into the waiting
                       wait_for_adjust executor. Set when a task starts.
                       推送调整指令的句柄。任务启动时设置。
        event_iterator: WorkflowEventIterator from engine.subscribe().
                        Set when a workflow starts.
                        来自 engine.subscribe() 的事件迭代器。工作流启动时设置。
    """

    def __init__(self, render_dir: str, bridge: Any = None):
        self.render_dir: str = render_dir
        self.bridge: Any = bridge
        self.task_running: bool = False
        self.engine: Any = None
        self.review_handle: Any = None
        self.adjust_handle: Any = None
        self.event_iterator: Any = None

    def clear_active_task(self) -> None:
        """Clear all task-related handles so the next task starts fresh.

        清除所有任务相关句柄，使下一个任务从头开始。
        """
        self.engine = None
        self.review_handle = None
        self.adjust_handle = None
        self.event_iterator = None
