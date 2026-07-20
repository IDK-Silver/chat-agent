"""Brain-facing worker tool definition and factory."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..llm.schema import ToolDefinition, ToolParameter
from .runner import WorkerResult, WorkerRunner

WORKER_TOOL_DEFINITION = ToolDefinition(
    name="worker",
    description=(
        "Delegate a multi-step task to an autonomous worker subagent. "
        "The worker runs with its own independent context window and can use "
        "all available tools except gui_task and worker itself. "
        "Write the prompt as a self-contained task description -- "
        "the worker has NO access to the current conversation context. "
        "Include all necessary details, file paths, and success criteria."
    ),
    parameters={
        "prompt": ToolParameter(
            type="string",
            description="Complete task description for the worker subagent.",
        ),
        "description": ToolParameter(
            type="string",
            description="3-5 word summary shown in logs.",
        ),
        "context_files": ToolParameter(
            type="array",
            description="Optional file paths to read and inject as context.",
            items={"type": "string"},
        ),
        "max_turns": ToolParameter(
            type="integer",
            description="Optional override for max agentic loop iterations.",
        ),
    },
    required=["prompt", "description"],
)


class WorkerCounter:
    """Thread-safe per-session counter for worker numbering."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._count = 0

    def next(self) -> int:
        with self._lock:
            self._count += 1
            return self._count


def format_worker_result(result: WorkerResult, description: str) -> str:
    """Format a WorkerResult into a human-readable status string."""
    if result.truncated:
        status = "TRUNCATED"
    elif result.success:
        status = "SUCCESS"
    else:
        status = "FAILED"
    header = (
        f"[WORKER {status}] ({description}) "
        f"turns: {result.turns_used}, tokens: {result.tokens_used}, "
        f"time: {result.duration_ms}ms"
    )
    parts = [header]
    if result.text:
        parts.append(result.text)
    if result.error:
        parts.append(f"Error: {result.error}")
    return "\n".join(parts)


def create_worker_tool(
    runner: WorkerRunner,
    agent_os_dir: Path,
    counter: WorkerCounter,
) -> Callable[..., str]:
    """Create worker tool callable bound to a WorkerRunner."""

    def worker_impl(
        prompt: str = "",
        description: str = "",
        context_files: Any = None,
        max_turns: Any = None,
        **_kwargs: Any,
    ) -> str:
        if not prompt:
            return "Error: prompt is required"
        if not description:
            description = "worker task"

        worker_num = counter.next()
        worker_label = f"worker-{worker_num}"

        file_list: list[str] | None = None
        if isinstance(context_files, list):
            file_list = [str(f) for f in context_files]

        turns_override: int | None = None
        if isinstance(max_turns, int) and max_turns > 0:
            turns_override = max_turns

        result = runner.run(
            prompt,
            context_files=file_list,
            max_turns_override=turns_override,
            agent_os_dir=agent_os_dir,
            worker_label=worker_label,
        )
        return format_worker_result(result, description)

    return worker_impl
