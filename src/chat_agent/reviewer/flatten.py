"""Flatten conversation messages with tool calls into plain text for reviewer LLMs.

The Ollama basic chat API only supports role + content. Tool call messages
(role="tool", assistant messages with tool_calls) need to be converted
to plain text so reviewer models can read them.
"""

from ..llm.schema import Message


def _summarize_tool_result(name: str, content: str, max_len: int = 150) -> str:
    """Summarize a tool result to a brief string."""
    content = content.strip()
    lines = content.splitlines()
    line_count = len(lines)

    if len(content) <= max_len:
        return f"[{name}: {content}]"

    # For multi-line results, show first line + line count
    first_line = lines[0][:max_len] if lines else ""
    return f"[{name}: {first_line}... ({line_count} lines)]"


def flatten_for_review(messages: list[Message]) -> list[Message]:
    """Convert a conversation with tool calls into plain user/assistant messages.

    Groups tool call sequences (assistant with tool_calls + tool results)
    into a single assistant message summarizing what was called and returned.
    Tool results are aggressively truncated since reviewers only need to verify
    which tools were called, not read the full output.
    """
    result: list[Message] = []
    i = 0

    while i < len(messages):
        msg = messages[i]

        if msg.role == "system":
            i += 1
            continue

        if msg.role == "assistant" and msg.tool_calls:
            # Collect this assistant message + subsequent tool results
            parts: list[str] = []
            if msg.content:
                parts.append(msg.content)

            # Summarize tool calls
            call_summaries: list[str] = []
            for tc in msg.tool_calls:
                args = ", ".join(f"{k}={v}" for k, v in tc.arguments.items())
                call_summaries.append(f"{tc.name}({args})")
            parts.append("[Tool calls: " + "; ".join(call_summaries) + "]")

            # Collect subsequent tool result messages
            i += 1
            while i < len(messages) and messages[i].role == "tool":
                tool_msg = messages[i]
                name = tool_msg.name or "unknown"
                content = tool_msg.content or ""
                parts.append(_summarize_tool_result(name, content))
                i += 1

            result.append(Message(role="assistant", content="\n".join(parts)))
            continue

        if msg.role == "tool":
            # Orphan tool result (shouldn't happen, but handle gracefully)
            i += 1
            continue

        # Regular user or assistant message
        result.append(Message(role=msg.role, content=msg.content))
        i += 1

    return result
