"""Post-turn conscience agent: checks if the brain's stated intent
matches its actual tool usage and provides corrective feedback.

Designed for models (e.g. Qwen 3.5) that tend to "say" they will do
something in text output but skip the corresponding tool call,
especially under long context.
"""

from __future__ import annotations

import logging
import re

from ..llm.base import LLMClient
from ..llm.schema import Message, ToolCall
from ..session.schema import SessionEntry

logger = logging.getLogger(__name__)

_NONE_RE = re.compile(r"^\s*NONE\b", re.IGNORECASE)

_SYSTEM_PROMPT = """\
You check ONE thing: did the AI agent forget to call send_message?

The agent's text output is an INTERNAL LOG. The user NEVER sees it. \
The ONLY way to deliver a message to the user is the send_message tool.

Answer NONE or MISSING:

MISSING: The text output looks like it is meant for the user \
(a reply, greeting, question, chat, emoji, concern, answer, etc.) \
AND send_message is NOT in the tool call list.

NONE: Any of these:
- send_message IS in the tool call list (message was delivered)
- The text is clearly internal thinking / self-notes / action summary \
  (e.g. "Message sent.", "Waiting for reply.", "Will check later.")
- The user input is a system trigger ([HEARTBEAT], [SCHEDULED], \
  [STARTUP]) and the text is not a user-facing message
- The text output is empty

Reply EXACTLY `NONE` or `MISSING` followed by a short reason.\
"""


class ConscienceAgent:
    """Sub-agent that audits brain tool-use compliance."""

    def __init__(self, client: LLMClient):
        self.client = client

    def check(
        self,
        *,
        user_input: str,
        tool_history: list[str],
        agent_response: str | None,
        available_tools: list[str] | None = None,
    ) -> str | None:
        """Return corrective feedback, or None if no issues found.

        Parameters
        ----------
        user_input:
            The original user message text for this turn.
        tool_history:
            List of "tool_name(summary)" strings for all tool calls
            executed this turn.
        agent_response:
            The brain's final text content (may be None if all output
            went through tool calls).
        available_tools:
            Names of all tools the brain can use this turn.
        """
        if not agent_response or not agent_response.strip():
            return None

        # System triggers have no human user to reply to; skip LLM call.
        stripped_input = user_input.strip()
        if stripped_input.startswith("[") and any(
            stripped_input.startswith(tag)
            for tag in ("[HEARTBEAT]", "[SCHEDULED]", "[STARTUP]")
        ):
            return None

        user_prompt = _build_check_prompt(
            user_input=user_input,
            tool_history=tool_history,
            agent_response=agent_response,
            available_tools=available_tools,
        )
        messages = [
            Message(role="system", content=_SYSTEM_PROMPT),
            Message(role="user", content=user_prompt),
        ]
        try:
            response = self.client.chat(messages)
        except Exception:
            logger.warning("Conscience agent LLM call failed", exc_info=True)
            return None

        if not response or _NONE_RE.match(response):
            return None
        return response.strip()


def collect_turn_tool_history(
    entries: list[SessionEntry],
    turn_anchor: int,
) -> list[str]:
    """Collect tool call summaries from conversation entries since turn_anchor."""
    history: list[str] = []
    for entry in entries[turn_anchor:]:
        msg = entry.message
        if msg.role == "assistant" and msg.tool_calls:
            for tc in msg.tool_calls:
                summary = _summarize_tool_call(tc)
                history.append(summary)
    return history


def _summarize_tool_call(tc: ToolCall) -> str:
    """Create a short summary of a tool call for the conscience prompt."""
    args = tc.arguments
    if tc.name == "send_message":
        channel = args.get("channel", "?")
        to = args.get("to", "")
        to_str = f" to={to}" if to else ""
        return f"send_message(channel={channel}{to_str})"
    if tc.name == "memory_edit":
        requests = args.get("requests", [])
        targets = [r.get("target_path", "?") for r in requests if isinstance(r, dict)]
        return f"memory_edit(targets={targets})"
    if tc.name == "schedule_action":
        action = args.get("action", "?")
        reason = args.get("reason", "")
        return f"schedule_action(action={action}, reason={reason[:50]})"
    if tc.name == "agent_task":
        action = args.get("action", "?")
        title = args.get("title", "")
        return f"agent_task(action={action}, title={title[:50]})"
    if tc.name == "agent_note":
        action = args.get("action", "?")
        key = args.get("key", "")
        return f"agent_note(action={action}, key={key})"
    if tc.name == "memory_search":
        query = args.get("query", "")
        return f"memory_search(query={query[:50]})"
    # Generic fallback
    short_args = str(args)[:80]
    return f"{tc.name}({short_args})"


def _build_check_prompt(
    *,
    user_input: str,
    tool_history: list[str],
    agent_response: str,
    available_tools: list[str] | None = None,
) -> str:
    lines = []
    if available_tools:
        lines.extend([
            "## Available tools",
            ", ".join(available_tools),
            "",
        ])
    lines.extend([
        "## User input",
        user_input.strip(),
        "",
        "## Agent tool calls this turn",
    ])
    if tool_history:
        for item in tool_history:
            lines.append(f"- {item}")
    else:
        lines.append("(none)")
    lines.extend([
        "",
        "## Agent final text output",
        agent_response.strip(),
    ])
    return "\n".join(lines)
