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

_NONE_RE = re.compile(r"^\s*NONE\s*$", re.IGNORECASE)

_SYSTEM_PROMPT = """\
You audit an AI agent's tool usage. The agent MUST use tools to act. \
Its text output is an internal log that the user NEVER sees.

Check these rules IN ORDER. Report the FIRST violation found.

RULE 1 (SEND): The agent's text output looks like a message to the \
user (reply, greeting, question, chat, emoji, concern, answer...) \
BUT send_message is NOT in the tool call list. \
--> The user will NOT receive this message. Agent must use send_message.

RULE 2 (MEMORY): The agent says it will remember/record/note something \
BUT memory_edit is NOT in the tool call list. \
Memory targets: long-term.md (important), temp-memory.md (scratch), \
people/ (per-person), knowledge/ (topics). \
Important facts should NOT go to temp-memory.md.

RULE 3 (SCHEDULE): The agent promises to remind/follow-up/check later \
BUT schedule_action is NOT in the tool call list.

RULE 4 (TASK): The agent says it will add a task/todo \
BUT agent_task is NOT in the tool call list.

RULE 5 (NOTE): The agent says it will track a status (location, mood) \
BUT agent_note is NOT in the tool call list.

Reply EXACTLY:
- NONE -- if no rule is violated
- Otherwise: 1-2 sentences saying which tool was missed and why.\
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
        """
        if not agent_response or not agent_response.strip():
            return None

        user_prompt = _build_check_prompt(
            user_input=user_input,
            tool_history=tool_history,
            agent_response=agent_response,
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
) -> str:
    lines = [
        "## User input",
        user_input.strip(),
        "",
        "## Agent tool calls this turn",
    ]
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
