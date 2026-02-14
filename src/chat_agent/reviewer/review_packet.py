"""Build compact post-review packets with per-field budget controls."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field

from ..llm.content import content_to_text
from ..llm.schema import Message, ToolCall


class ReviewPacketConfig(BaseModel):
    """Per-field budget controls for post-review packet construction."""

    history_turns: int = Field(default=6, ge=1)
    history_turn_max_chars: int = Field(default=1200, ge=200)
    reply_max_chars: int = Field(default=3000, ge=200)
    tool_preview_max_chars: int = Field(default=180, ge=50)


class MemoryEditRequestSummary(BaseModel):
    """Compact summary of one memory_edit request."""

    request_id: str | None = None
    target_path: str | None = None
    instruction: str | None = None


class TruncationRecord(BaseModel):
    """One truncation action applied while fitting packet to budget."""

    section: str
    action: Literal["drop", "trim"]
    detail: str
    original_chars: int | None = None
    final_chars: int | None = None


class ReviewPacket(BaseModel):
    """Deterministic evidence packet for post-review model."""

    latest_user_turn: str = ""
    candidate_assistant_reply: str = ""
    current_turn_tool_calls_summary: list[str] = Field(default_factory=list)
    current_turn_memory_edit_summary: list[MemoryEditRequestSummary] = Field(
        default_factory=list
    )
    current_turn_tool_errors: list[str] = Field(default_factory=list)
    recent_context_tail: list[str] = Field(default_factory=list)
    truncation_report: list[TruncationRecord] = Field(default_factory=list)


def _packet_payload(packet: ReviewPacket) -> dict[str, object]:
    """Return packet payload used by rendering."""
    return packet.model_dump(mode="json")


def _truncate_text(text: str, max_chars: int) -> tuple[str, bool]:
    """Truncate text while preserving semantic continuity."""
    if len(text) <= max_chars:
        return text, False
    if max_chars <= 3:
        return text[:max_chars], True
    return text[: max_chars - 3] + "...", True


def _is_failed_tool_result(content: str) -> bool:
    """Check whether a tool result should be treated as an error signal."""
    if content.startswith("Error"):
        return True
    if not content.startswith("{"):
        return False
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return False
    return isinstance(payload, dict) and payload.get("status") == "failed"


def _safe_json_dumps(data: object) -> str:
    """Serialize unknown data shape into compact JSON-ish text."""
    try:
        return json.dumps(data, ensure_ascii=False, sort_keys=True)
    except Exception:
        return str(data)


def _extract_memory_edit_requests(tool_call: ToolCall) -> list[MemoryEditRequestSummary]:
    """Extract compact request summaries from one memory_edit call."""
    requests = tool_call.arguments.get("requests", [])
    if isinstance(requests, str):
        try:
            requests = json.loads(requests)
        except json.JSONDecodeError:
            requests = []
    if not isinstance(requests, list):
        return []

    summaries: list[MemoryEditRequestSummary] = []
    for request in requests:
        if not isinstance(request, dict):
            continue
        summaries.append(
            MemoryEditRequestSummary(
                request_id=str(request.get("request_id")) if request.get("request_id") else None,
                target_path=str(request.get("target_path")) if request.get("target_path") else None,
                instruction=(
                    str(request.get("instruction"))
                    if request.get("instruction")
                    else None
                ),
            )
        )
    return summaries


def _summarize_current_turn_tool_calls(
    turn_messages: list[Message],
    *,
    tool_result_max_chars: int,
) -> tuple[list[str], list[MemoryEditRequestSummary], list[str]]:
    """Summarize tool calls, memory_edit requests, and failed tool outputs."""
    tool_call_map: dict[str, str] = {}
    summaries: list[str] = []
    memory_requests: list[MemoryEditRequestSummary] = []
    errors: list[str] = []

    for message in turn_messages:
        if message.role == "assistant" and message.tool_calls:
            for tool_call in message.tool_calls:
                tool_call_map[tool_call.id] = tool_call.name
                args_preview = _safe_json_dumps(tool_call.arguments)
                args_preview, _ = _truncate_text(args_preview, tool_result_max_chars)
                summaries.append(f"{tool_call.name}({args_preview})")
                if tool_call.name == "memory_edit":
                    memory_requests.extend(_extract_memory_edit_requests(tool_call))
            continue

        if message.role == "tool" and message.content:
            text_content = content_to_text(message.content)
            if not text_content or not _is_failed_tool_result(text_content):
                continue
            tool_name = message.name or tool_call_map.get(message.tool_call_id or "", "tool")
            detail, _ = _truncate_text(text_content.strip(), tool_result_max_chars)
            errors.append(f"{tool_name}: {detail}")

    return summaries, memory_requests, errors


def _summarize_turn(
    turn_messages: list[Message],
    *,
    turn_max_chars: int,
    tool_result_max_chars: int,
) -> tuple[str, list[TruncationRecord]]:
    """Summarize one historical turn into a concise reviewer summary."""
    user_text = ""
    assistant_text = ""
    tools: list[str] = []
    tool_failures: list[str] = []

    for message in turn_messages:
        if message.role == "user" and message.content and not user_text:
            user_text = content_to_text(message.content).strip()
        elif message.role == "assistant":
            if message.tool_calls:
                tools.extend(tc.name for tc in message.tool_calls)
            elif message.content:
                assistant_text = content_to_text(message.content).strip()
        elif message.role == "tool" and message.content:
            text_content = content_to_text(message.content)
            if text_content and _is_failed_tool_result(text_content):
                failed_text, _ = _truncate_text(text_content.strip(), tool_result_max_chars)
                tool_failures.append(failed_text)

    lines: list[str] = []
    if user_text:
        lines.append(f"U: {user_text}")
    if assistant_text:
        lines.append(f"A: {assistant_text}")
    if tools:
        lines.append("Tools: " + ", ".join(dict.fromkeys(tools)))
    if tool_failures:
        lines.append("ToolErrors: " + " | ".join(tool_failures))

    summary = "\n".join(lines).strip()
    if not summary:
        return "", []

    records: list[TruncationRecord] = []
    original_len = len(summary)
    summary, truncated = _truncate_text(summary, turn_max_chars)
    if truncated:
        records.append(
            TruncationRecord(
                section="recent_context_tail",
                action="trim",
                detail="trim_turn_summary",
                original_chars=original_len,
                final_chars=len(summary),
            )
        )
    return summary, records


def _group_turns(messages: list[Message]) -> list[list[Message]]:
    """Group messages by user turns."""
    turns: list[list[Message]] = []
    current: list[Message] = []

    for message in messages:
        if message.role == "user":
            if current:
                turns.append(current)
                current = []
            current.append(message)
            continue

        if current:
            current.append(message)

    if current:
        turns.append(current)
    return turns


def _latest_user_text(messages: list[Message]) -> str:
    """Return latest user text from the provided message list."""
    for message in reversed(messages):
        if message.role == "user" and message.content:
            return content_to_text(message.content).strip()
    return ""


def _latest_assistant_reply(messages: list[Message]) -> str:
    """Return latest non-tool assistant reply from provided messages."""
    for message in reversed(messages):
        if message.role != "assistant":
            continue
        if message.tool_calls:
            continue
        if not message.content:
            continue
        text = content_to_text(message.content).strip()
        if text:
            return text
    return ""


def build_post_review_packet(
    messages: list[Message],
    *,
    turn_anchor: int,
    config: ReviewPacketConfig,
) -> ReviewPacket:
    """Build a compact packet for post-review model input."""
    anchor = max(0, min(turn_anchor, len(messages)))
    previous_messages = messages[:anchor]
    turn_messages = messages[anchor:]

    tool_call_summaries, memory_edit_summaries, tool_errors = (
        _summarize_current_turn_tool_calls(
            turn_messages,
            tool_result_max_chars=config.tool_preview_max_chars,
        )
    )

    turn_summaries: list[str] = []
    truncation_records: list[TruncationRecord] = []
    for turn in _group_turns(previous_messages)[-config.history_turns :]:
        summary, turn_records = _summarize_turn(
            turn,
            turn_max_chars=config.history_turn_max_chars,
            tool_result_max_chars=config.tool_preview_max_chars,
        )
        truncation_records.extend(turn_records)
        if summary:
            turn_summaries.append(summary)

    latest_user_turn, _ = _truncate_text(
        _latest_user_text(turn_messages),
        config.history_turn_max_chars,
    )
    candidate_reply, candidate_truncated = _truncate_text(
        _latest_assistant_reply(turn_messages),
        config.reply_max_chars,
    )
    if candidate_truncated:
        truncation_records.append(
            TruncationRecord(
                section="candidate_assistant_reply",
                action="trim",
                detail="trim_reply_budget",
                original_chars=len(_latest_assistant_reply(turn_messages)),
                final_chars=len(candidate_reply),
            )
        )

    packet = ReviewPacket(
        latest_user_turn=latest_user_turn,
        candidate_assistant_reply=candidate_reply,
        current_turn_tool_calls_summary=tool_call_summaries,
        current_turn_memory_edit_summary=memory_edit_summaries,
        current_turn_tool_errors=tool_errors,
        recent_context_tail=turn_summaries,
        truncation_report=truncation_records,
    )
    return packet


def render_review_packet(packet: ReviewPacket) -> str:
    """Render packet into JSON for post-review model consumption."""
    return json.dumps(_packet_payload(packet), ensure_ascii=False, indent=2)
