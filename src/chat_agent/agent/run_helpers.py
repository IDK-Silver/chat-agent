"""Shared helpers for agent turn execution."""

from __future__ import annotations

from collections.abc import Callable
import logging
import re

from ..llm import LLMResponse
from ..session.schema import SessionEntry
from .ui_event_console import AgentUiPort

_TIMESTAMP_PREFIX_RE = re.compile(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}[^\]]*\]\s*")
_DEBUG_RESPONSE_PREVIEW_CHARS = 4000
_THINKING_PREVIEW_CHARS = 12000
_SENSITIVE_URL_PARAM_RE = re.compile(
    r"([?&](?:key|api_key|token|access_token)=)[^&\s]+",
    re.IGNORECASE,
)
_GOOGLE_API_KEY_RE = re.compile(r"AIza[0-9A-Za-z_-]{20,}")
logger = logging.getLogger(__name__)


def _raise_if_cancel_requested(
    is_cancel_requested: Callable[[], bool] | None,
    *,
    on_pending: Callable[[], None] | None = None,
) -> None:
    """Raise KeyboardInterrupt when a turn-level cancel has been requested."""
    if is_cancel_requested is None:
        return
    if not is_cancel_requested():
        return
    if on_pending is not None:
        on_pending()
    raise KeyboardInterrupt


def _strip_timestamp_prefix(text: str) -> str:
    """Strip leading [YYYY-MM-DD HH:MM...] prefix that LLM may echo."""
    return _TIMESTAMP_PREFIX_RE.sub("", text)


def _latest_nonempty_assistant_content(messages: list[SessionEntry]) -> str:
    """Return the newest non-empty assistant content from non-tool messages."""
    for msg in reversed(messages):
        if msg.role != "assistant" or msg.tool_calls:
            continue
        content = (msg.content or "").strip()
        if content:
            return msg.content or ""
    return ""


def _latest_intermediate_text(messages: list[SessionEntry]) -> str:
    """Return newest non-empty content from assistant messages that have tool_calls."""
    for msg in reversed(messages):
        if msg.role != "assistant" or not msg.tool_calls:
            continue
        content = (msg.content or "").strip()
        if content:
            return msg.content or ""
    return ""


def _resolve_final_content(
    response_content: str | None,
    turn_messages: list[SessionEntry],
) -> tuple[str, bool]:
    """Resolve user-visible content; fallback to prior assistant text."""
    if isinstance(response_content, str) and response_content.strip():
        return response_content, False

    fallback = _latest_nonempty_assistant_content(turn_messages)
    if fallback:
        return fallback, True

    intermediate = _latest_intermediate_text(turn_messages)
    if intermediate:
        return intermediate, True

    return "", False


def _sanitize_error_message(message: str) -> str:
    """Redact known sensitive tokens from surfaced error messages."""
    redacted = _SENSITIVE_URL_PARAM_RE.sub(r"\1***", message)
    return _GOOGLE_API_KEY_RE.sub("***", redacted)


def _debug_print_responder_output(
    console: AgentUiPort,
    response: LLMResponse,
    *,
    label: str,
) -> None:
    """Print responder model output details for debug investigations."""
    if not console.debug:
        return

    tool_calls = response.tool_calls or []
    tool_names = ", ".join(tc.name for tc in tool_calls) if tool_calls else "(none)"
    content = response.content or ""
    reasoning = response.reasoning_content or ""
    finish = response.finish_reason or "?"
    cache_read = response.cache_read_tokens
    cache_write = response.cache_write_tokens
    console.print_debug(
        label,
        f"content_chars={len(content)}, tool_calls={len(tool_calls)}, "
        f"reasoning_chars={len(reasoning)}, finish={finish}, tools=[{tool_names}], "
        f"cache_read={cache_read}, cache_write={cache_write}",
    )
    if cache_read > 0 or cache_write > 0:
        total = cache_read + cache_write
        hit_pct = (cache_read / total * 100) if total > 0 else 0
        logger.info(
            "cache: read=%d write=%d hit=%.0f%%",
            cache_read, cache_write, hit_pct,
        )

    if not content.strip():
        if tool_calls:
            console.print_debug(
                f"{label} output",
                "(tool-only response; no textual content)",
            )
        else:
            console.print_debug(
                f"{label} output",
                "(empty; no textual content and no tool calls)",
            )
        return

    preview = content
    if len(preview) > _DEBUG_RESPONSE_PREVIEW_CHARS:
        preview = preview[:_DEBUG_RESPONSE_PREVIEW_CHARS] + "\n...[truncated]"
    console.print_debug_block(f"{label} output", preview)


def _emit_reasoning_block_if_needed(
    console: AgentUiPort,
    response: LLMResponse,
    *,
    channel: str | None,
    sender: str | None,
) -> None:
    """Show tool-loop reasoning in TUI as a side-channel block."""
    if not response.has_tool_calls():
        return
    text = (response.reasoning_content or "").strip()
    if not text:
        return
    total_chars = len(text)
    preview = text
    if len(preview) > _THINKING_PREVIEW_CHARS:
        preview = preview[:_THINKING_PREVIEW_CHARS] + "\n...[truncated]"
    console.print_inner_thoughts(
        channel or "internal",
        sender,
        f"[THINKING][chars={total_chars}]\n{preview}",
    )
