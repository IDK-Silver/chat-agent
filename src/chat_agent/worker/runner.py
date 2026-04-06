"""Worker subagent execution engine."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..llm.base import LLMClient
from ..llm.schema import LLMResponse, Message, ToolCall, ToolDefinition, make_tool_result_message
from ..session.debug_client import DebugLoggingLLMClient
from ..tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class WorkerResult:
    """Outcome of a single worker invocation."""

    success: bool
    text: str
    turns_used: int
    tokens_used: int
    duration_ms: int
    truncated: bool
    error: str | None = None


class _DebugSinkProtocol:
    """Minimal type hint for the session debug sink."""


class WorkerRunner:
    """Run autonomous tool loops with an independent context window."""

    def __init__(
        self,
        client: LLMClient,
        source_registry: ToolRegistry,
        excluded_tools: frozenset[str],
        system_prompt: str,
        *,
        max_turns: int = 30,
        cache_control: dict[str, str] | None = None,
        sink: Any = None,
        provider: str | None = None,
        model: str | None = None,
    ) -> None:
        self._client = client
        self._source_registry = source_registry
        self._excluded_tools = excluded_tools
        self._system_prompt = system_prompt
        self._max_turns = max_turns
        self._cache_control = cache_control
        self._sink = sink
        self._provider = provider
        self._model = model

    def _build_filtered_registry(self) -> ToolRegistry:
        """Clone tools from source registry, excluding blocked names."""
        filtered = ToolRegistry()
        for name, (func, defn) in self._source_registry._tools.items():
            if name not in self._excluded_tools:
                filtered.register(name, func, defn)
        return filtered

    def _build_user_message(
        self,
        prompt: str,
        context_files: list[str] | None,
        agent_os_dir: Path | None,
    ) -> str:
        """Build user message with optional context file preamble."""
        parts: list[str] = []
        for path_str in context_files or []:
            resolved = Path(path_str).expanduser()
            if not resolved.is_absolute() and agent_os_dir:
                resolved = agent_os_dir / resolved
            try:
                content = resolved.read_text(encoding="utf-8")
                parts.append(f"[Context: {path_str}]\n{content}\n[/Context]")
            except OSError:
                parts.append(f"[Context: {path_str}]\n(file not found)\n[/Context]")
        parts.append(prompt)
        return "\n\n".join(parts)

    def _wrap_client(self, worker_label: str) -> LLMClient:
        """Wrap the base client with per-invocation debug logging."""
        if self._sink is None:
            return self._client
        return DebugLoggingLLMClient(
            self._client,
            sink=self._sink,
            client_label=worker_label,
            provider=self._provider,
            model=self._model,
        )

    def run(
        self,
        prompt: str,
        *,
        context_files: list[str] | None = None,
        max_turns_override: int | None = None,
        agent_os_dir: Path | None = None,
        worker_label: str = "worker",
    ) -> WorkerResult:
        """Execute the worker agentic loop and return the result."""
        effective_max_turns = max_turns_override or self._max_turns
        client = self._wrap_client(worker_label)
        registry = self._build_filtered_registry()
        tool_defs = registry.get_definitions()

        # Build initial messages
        system_msg = Message(
            role="system",
            content=self._system_prompt,
            cache_control=self._cache_control,
        )
        user_content = self._build_user_message(prompt, context_files, agent_os_dir)
        user_msg = Message(role="user", content=user_content)
        messages: list[Message] = [system_msg, user_msg]

        turns = 0
        tokens_used = 0
        last_text: str | None = None
        started_ms = _now_ms()

        try:
            response = client.chat_with_tools(messages, tool_defs)
            tokens_used += response.total_tokens or 0

            while response.tool_calls and turns < effective_max_turns:
                # Capture assistant text if present
                if response.content:
                    last_text = response.content

                messages.append(Message(
                    role="assistant",
                    content=response.content,
                    tool_calls=response.tool_calls,
                ))

                for tc in response.tool_calls:
                    result = registry.execute(tc)
                    messages.append(make_tool_result_message(
                        tool_call_id=tc.id,
                        name=tc.name,
                        content=result.content,
                    ))

                turns += 1
                response = client.chat_with_tools(messages, tool_defs)
                tokens_used += response.total_tokens or 0

            # Final response (no tool calls)
            if response.content:
                last_text = response.content

            truncated = bool(response.tool_calls) and turns >= effective_max_turns
            return WorkerResult(
                success=not truncated,
                text=last_text or "",
                turns_used=turns,
                tokens_used=tokens_used,
                duration_ms=_now_ms() - started_ms,
                truncated=truncated,
            )

        except Exception as exc:
            logger.warning("Worker %s failed: %s", worker_label, exc)
            return WorkerResult(
                success=False,
                text=last_text or "",
                turns_used=turns,
                tokens_used=tokens_used,
                duration_ms=_now_ms() - started_ms,
                truncated=False,
                error=str(exc),
            )


def _now_ms() -> int:
    return int(time.monotonic() * 1000)
