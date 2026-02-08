"""Graceful shutdown with LLM memory saving."""

from fnmatch import fnmatch
import json

from ..context import ContextBuilder, Conversation
from ..llm.base import LLMClient
from ..llm.schema import Message, ToolCall
from ..reviewer import PostReviewer, RequiredAction
from ..tools import ToolRegistry
from ..workspace import WorkspaceManager
from .console import ChatConsole

_MAX_TOOL_ITERATIONS = 20


def _is_failed_memory_edit_result(result: str) -> bool:
    """Check whether memory_edit tool returned failed status."""
    if result.startswith("Error"):
        return True
    if not result.startswith("{"):
        return False
    try:
        payload = json.loads(result)
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, dict):
        return False
    return payload.get("status") == "failed"


def _has_conversation_content(conversation: Conversation) -> bool:
    """Check if conversation has any user messages."""
    return any(m.role == "user" for m in conversation.get_messages())


def _get_last_user_timestamp(conversation: Conversation):
    """Return timestamp of the latest user message, if any."""
    for message in reversed(conversation.get_messages()):
        if message.role == "user":
            return message.timestamp
    return None


def _collect_turn_tool_calls(turn_messages: list[Message]) -> list[ToolCall]:
    """Collect tool calls from assistant messages."""
    tool_calls: list[ToolCall] = []
    for msg in turn_messages:
        if msg.role == "assistant" and msg.tool_calls:
            tool_calls.extend(msg.tool_calls)
    return tool_calls


def _match_path(path: str, action: RequiredAction) -> bool:
    """Check whether path satisfies action path constraints."""
    if not action.target_path and not action.target_path_glob:
        return True
    if action.target_path and path == action.target_path:
        return True
    if action.target_path_glob and fnmatch(path, action.target_path_glob):
        return True
    return False


def _extract_memory_edit_paths(tool_call: ToolCall) -> list[str]:
    """Extract target/index paths from a memory_edit tool call."""
    requests = tool_call.arguments.get("requests", [])
    if not isinstance(requests, list):
        return []

    paths: list[str] = []
    for request in requests:
        if not isinstance(request, dict):
            continue
        target_path = request.get("target_path")
        if isinstance(target_path, str) and target_path:
            paths.append(target_path)
        index_path = request.get("index_path")
        if isinstance(index_path, str) and index_path:
            paths.append(index_path)
    return paths


def _is_memory_edit_index_update(tool_call: ToolCall, index_path: str) -> bool:
    """Check if memory_edit call updates an index file."""
    requests = tool_call.arguments.get("requests", [])
    if not isinstance(requests, list):
        return False

    for request in requests:
        if not isinstance(request, dict):
            continue
        req_index = request.get("index_path")
        req_target = request.get("target_path")
        if req_index == index_path or req_target == index_path:
            return True
    return False


def _match_action_call(tool_call: ToolCall, action: RequiredAction) -> bool:
    """Check whether one tool call satisfies one action."""
    if action.tool == "write_or_edit":
        if tool_call.name not in {"write_file", "edit_file", "memory_edit"}:
            return False
    elif action.tool == "memory_edit":
        if tool_call.name != "memory_edit":
            return False
        if not action.target_path and not action.target_path_glob:
            return True
        return any(_match_path(path, action) for path in _extract_memory_edit_paths(tool_call))
    elif tool_call.name != action.tool:
        return False

    if action.tool in {"write_file", "edit_file", "write_or_edit", "read_file"}:
        if tool_call.name == "memory_edit":
            return any(_match_path(path, action) for path in _extract_memory_edit_paths(tool_call))
        path = str(tool_call.arguments.get("path", ""))
        return _match_path(path, action)

    if action.tool == "execute_shell":
        command = str(tool_call.arguments.get("command", ""))
        if action.command_must_contain and action.command_must_contain not in command:
            return False
        return True

    if action.tool == "get_current_time":
        return True

    return False


def _is_action_satisfied(tool_calls: list[ToolCall], action: RequiredAction) -> bool:
    """Verify action completion, including index updates when required."""
    primary_ok = any(_match_action_call(tc, action) for tc in tool_calls)
    if not primary_ok:
        return False

    if not action.index_path:
        return True

    return any(
        (
            tc.name in {"write_file", "edit_file"}
            and str(tc.arguments.get("path", "")) == action.index_path
        )
        or (
            tc.name == "memory_edit"
            and _is_memory_edit_index_update(tc, action.index_path)
        )
        for tc in tool_calls
    )


def _find_missing_actions(
    turn_messages: list[Message],
    required_actions: list[RequiredAction],
) -> list[RequiredAction]:
    """Return required actions not satisfied in selected messages."""
    if not required_actions:
        return []
    tool_calls = _collect_turn_tool_calls(turn_messages)
    return [a for a in required_actions if not _is_action_satisfied(tool_calls, a)]


def _build_shutdown_retry_prompt(
    retry_instruction: str,
    required_actions: list[RequiredAction],
) -> str:
    """Build a retry prompt that asks brain to only fix missing shutdown actions."""
    lines = [
        "Shutdown compliance retry.",
        "Complete ALL required actions below before finishing.",
        "Use tools to persist memory updates now.",
        "",
        "Required actions:",
    ]
    for i, action in enumerate(required_actions, start=1):
        lines.append(f"{i}. [{action.code}] {action.description}")
        lines.append(f"   - tool: {action.tool}")
        if action.target_path:
            lines.append(f"   - target_path: {action.target_path}")
        if action.target_path_glob:
            lines.append(f"   - target_path_glob: {action.target_path_glob}")
        if action.command_must_contain:
            lines.append(f"   - command_must_contain: {action.command_must_contain}")
        if action.index_path:
            lines.append(f"   - also_update_index: {action.index_path}")
        if action.tool == "memory_edit":
            sample_target = action.target_path or "memory/short-term.md"
            lines.append("   - use exact keys: as_of, turn_id, requests")
            lines.append("   - memory_edit minimal payload:")
            lines.append(
                "     "
                + (
                    '{"as_of":"<ISO-8601>","turn_id":"<turn-id>",'
                    '"requests":[{"request_id":"r1","kind":"append_entry",'
                    f'"target_path":"{sample_target}",'
                    '"payload_text":"<entry>"}]}'
                )
            )

    if retry_instruction:
        lines.extend(["", "Reviewer instruction:", retry_instruction])

    return "\n".join(lines)


def _run_shutdown_tool_loop(
    client: LLMClient,
    conversation: Conversation,
    builder: ContextBuilder,
    registry: ToolRegistry,
    console: ChatConsole,
) -> bool:
    """Run one shutdown execution loop (prompt already injected in conversation)."""
    messages = builder.build(conversation)
    tools = registry.get_definitions()

    with console.spinner("Saving memories..."):
        response = client.chat_with_tools(messages, tools)

    iterations = 0
    while response.has_tool_calls() and iterations < _MAX_TOOL_ITERATIONS:
        iterations += 1

        conversation.add_assistant_with_tools(response.content, response.tool_calls)

        for tool_call in response.tool_calls:
            console.print_tool_call(tool_call)
            with console.spinner("Executing..."):
                result = registry.execute(tool_call)
            console.print_tool_result(tool_call, result)
            conversation.add_tool_result(tool_call.id, tool_call.name, result)
            if tool_call.name == "memory_edit" and _is_failed_memory_edit_result(result):
                return False

        messages = builder.build(conversation)
        with console.spinner("Saving memories..."):
            response = client.chat_with_tools(messages, tools)

    if response.content:
        conversation.add("assistant", response.content)

    return True


def perform_shutdown(
    client: LLMClient,
    conversation: Conversation,
    builder: ContextBuilder,
    registry: ToolRegistry,
    console: ChatConsole,
    workspace: WorkspaceManager,
    user_id: str,
    reviewer: PostReviewer | None = None,
    reviewer_max_retries: int = 0,
    reviewer_warn_on_failure: bool = True,
) -> bool:
    """Send shutdown prompt to LLM and execute tool calls for memory saving.

    Returns:
        True if completed, False if interrupted or fail-closed.
    """
    try:
        shutdown_prompt = workspace.get_agent_prompt(
            "brain", "shutdown", current_user=user_id
        )
    except FileNotFoundError:
        return True

    # Keep shutdown-triggered prompt on the same user-time anchor as
    # the latest real user input, so archives don't shift to quit time.
    conversation.add(
        "user",
        shutdown_prompt,
        timestamp=_get_last_user_timestamp(conversation),
    )
    try:
        initial_anchor = len(conversation.get_messages())
        if not _run_shutdown_tool_loop(client, conversation, builder, registry, console):
            return False

        if reviewer is None:
            return True

        retry_count = 0
        last_signature: tuple[str, ...] | None = None
        while retry_count <= reviewer_max_retries:
            review_messages = builder.build(conversation)
            with console.spinner("Checking shutdown..."):
                result = reviewer.review(review_messages)

            if result is None:
                if reviewer_warn_on_failure:
                    console.print_warning(
                        "Shutdown review failed; fail-closed."
                    )
                return False

            if result.passed:
                return True

            shutdown_messages = conversation.get_messages()[initial_anchor:]
            missing_actions = _find_missing_actions(
                shutdown_messages,
                result.required_actions,
            )
            if result.required_actions and not missing_actions:
                return True

            actions_for_retry = missing_actions or result.required_actions
            signature = tuple(sorted(a.code for a in actions_for_retry))
            if signature and signature == last_signature:
                if reviewer_warn_on_failure:
                    console.print_warning(
                        "Shutdown review detected repeated missing actions; fail-closed."
                    )
                return False
            last_signature = signature

            if retry_count >= reviewer_max_retries:
                if reviewer_warn_on_failure:
                    console.print_warning(
                        "Shutdown review found unresolved actions after max retries; fail-closed."
                    )
                return False

            repair_prompt = _build_shutdown_retry_prompt(
                retry_instruction=result.retry_instruction or (result.guidance or ""),
                required_actions=actions_for_retry,
            )
            conversation.add(
                "user",
                repair_prompt,
                timestamp=_get_last_user_timestamp(conversation),
            )
            if not _run_shutdown_tool_loop(client, conversation, builder, registry, console):
                return False
            retry_count += 1

        return True

    except KeyboardInterrupt:
        return False
