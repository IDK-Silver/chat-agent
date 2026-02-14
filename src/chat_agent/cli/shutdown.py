"""Graceful shutdown with LLM memory saving."""

from ..context import ContextBuilder, Conversation
from ..llm.base import LLMClient
from ..reviewer import PostReviewer, RequiredAction
from ..reviewer.enforcement import (
    detect_persistence_anomalies,
    is_failed_memory_edit_result,
    find_missing_actions,
    build_target_enforcement_actions,
    merge_anomaly_signals,
)
from ..tools import ToolRegistry
from ..workspace import WorkspaceManager
from .console import ChatConsole

_MAX_TOOL_ITERATIONS = 20
_MEMORY_EDIT_RETRY_LIMIT = 3


def _has_conversation_content(conversation: Conversation) -> bool:
    """Check if conversation has any user messages."""
    return any(m.role == "user" for m in conversation.get_messages())


def _get_last_user_timestamp(conversation: Conversation):
    """Return timestamp of the latest user message, if any."""
    for message in reversed(conversation.get_messages()):
        if message.role == "user":
            return message.timestamp
    return None


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
            lines.append("   - use exact keys: as_of, turn_id, requests")
            if action.target_path:
                lines.append(
                    "   - memory_edit minimal payload: "
                    '{"as_of":"<ISO-8601>","turn_id":"<turn-id>",'
                    '"requests":[{"request_id":"r1",'
                    f'"target_path":"{action.target_path}",'
                    '"instruction":"<what to change>"}]}'
                )
            elif action.target_path_glob:
                lines.append(
                    "   - target_path_glob is a constraint, not a writable target_path."
                )
                lines.append(
                    "   - NEVER use wildcard characters in requests[].target_path."
                )
                lines.append(
                    "   - first locate an exact file path, then use instruction."
                )
                lines.append(
                    "   - if no file exists, create one using a concrete target_path."
                )
            else:
                lines.append(
                    "   - memory_edit minimal payload: "
                    '{"as_of":"<ISO-8601>","turn_id":"<turn-id>",'
                    '"requests":[{"request_id":"r1",'
                    '"target_path":"memory/agent/short-term.md",'
                    '"instruction":"<what to change>"}]}'
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
    memory_edit_allow_failure: bool = False,
) -> bool:
    """Run one shutdown execution loop (prompt already injected in conversation)."""
    messages = builder.build(conversation)
    tools = registry.get_definitions()

    with console.spinner("Saving memories..."):
        response = client.chat_with_tools(messages, tools)

    iterations = 0
    memory_edit_fail_streak = 0
    while response.has_tool_calls() and iterations < _MAX_TOOL_ITERATIONS:
        iterations += 1

        conversation.add_assistant_with_tools(response.content, response.tool_calls)

        failed_memory_edit_this_round = False
        for tool_call in response.tool_calls:
            if not registry.has_tool(tool_call.name):
                conversation.add_tool_result(
                    tool_call.id, tool_call.name,
                    f"Error: Unknown tool '{tool_call.name}'",
                )
                continue
            console.print_tool_call(tool_call)
            with console.spinner("Executing..."):
                result = registry.execute(tool_call)
            console.print_tool_result(tool_call, result)
            conversation.add_tool_result(tool_call.id, tool_call.name, result)
            if tool_call.name == "memory_edit" and isinstance(result, str) and is_failed_memory_edit_result(result):
                failed_memory_edit_this_round = True

        if failed_memory_edit_this_round:
            memory_edit_fail_streak += 1
            if memory_edit_fail_streak >= _MEMORY_EDIT_RETRY_LIMIT:
                if memory_edit_allow_failure:
                    console.print_warning(
                        f"memory_edit failed {memory_edit_fail_streak} times during shutdown; "
                        "allow_failure=true, continuing.",
                    )
                    break
                return False
            console.print_warning(
                f"memory_edit failed during shutdown; retrying ({memory_edit_fail_streak}/{_MEMORY_EDIT_RETRY_LIMIT})",
                indent=2,
            )
        else:
            memory_edit_fail_streak = 0

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
    reviewer_allow_unresolved: bool = False,
    reviewer_warn_on_failure: bool = True,
    memory_edit_allow_failure: bool = False,
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
        if not _run_shutdown_tool_loop(client, conversation, builder, registry, console, memory_edit_allow_failure=memory_edit_allow_failure):
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

            shutdown_messages = conversation.get_messages()[initial_anchor:]
            missing_actions = find_missing_actions(
                shutdown_messages,
                result.required_actions,
            )

            # Strict target-signal enforcement and anomaly checks.
            target_enforcement_actions = build_target_enforcement_actions(
                result.target_signals,
                shutdown_messages,
                current_user=user_id,
            )
            deterministic_anomaly_signals = detect_persistence_anomalies(
                result.target_signals,
                shutdown_messages,
                current_user=user_id,
            )
            merged_anomaly_signals = merge_anomaly_signals(
                result.anomaly_signals,
                deterministic_anomaly_signals,
            )

            if (
                result.passed
                and not missing_actions
                and not target_enforcement_actions
                and not merged_anomaly_signals
            ):
                return True
            if (
                result.required_actions
                and not missing_actions
                and not target_enforcement_actions
                and not merged_anomaly_signals
            ):
                return True

            actions_for_retry = missing_actions or result.required_actions

            # Merge target enforcement actions into retry actions.
            if target_enforcement_actions:
                existing_codes = {a.code for a in actions_for_retry}
                for action in target_enforcement_actions:
                    if action.code not in existing_codes:
                        actions_for_retry.append(action)

            anomalies_for_signature = tuple(
                sorted(
                    f"{a.signal}:{a.target_signal or '-'}"
                    for a in merged_anomaly_signals
                )
            )
            signature = tuple(sorted(a.code for a in actions_for_retry) + list(anomalies_for_signature))
            if signature and signature == last_signature:
                if reviewer_allow_unresolved:
                    console.print_warning(
                        "Shutdown review detected repeated missing actions; "
                        "allow_unresolved=true, accepting."
                    )
                    return True
                if reviewer_warn_on_failure:
                    console.print_warning(
                        "Shutdown review detected repeated missing actions; fail-closed."
                    )
                return False
            last_signature = signature

            if retry_count >= reviewer_max_retries:
                if reviewer_allow_unresolved:
                    console.print_warning(
                        "Shutdown review found unresolved actions after max retries; "
                        "allow_unresolved=true, accepting."
                    )
                    return True
                if reviewer_warn_on_failure:
                    console.print_warning(
                        "Shutdown review found unresolved actions after max retries; fail-closed."
                    )
                return False

            repair_prompt = _build_shutdown_retry_prompt(
                retry_instruction="\n\n".join(
                    part
                    for part in [
                        result.retry_instruction or (result.guidance or ""),
                        (
                            "Fix anomaly signals:\n"
                            + "\n".join(
                                f"- {a.signal} | target={a.target_signal or '-'} | "
                                f"{a.reason or 'no reason provided'}"
                                for a in merged_anomaly_signals
                            )
                            if merged_anomaly_signals
                            else ""
                        ),
                    ]
                    if part
                ),
                required_actions=actions_for_retry,
            )
            conversation.add(
                "user",
                repair_prompt,
                timestamp=_get_last_user_timestamp(conversation),
            )
            if not _run_shutdown_tool_loop(client, conversation, builder, registry, console, memory_edit_allow_failure=memory_edit_allow_failure):
                return False
            retry_count += 1

        return True

    except KeyboardInterrupt:
        return False
