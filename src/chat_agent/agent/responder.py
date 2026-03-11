"""Brain responder loop and staged-planning orchestration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .shared_state import SharedStateStore
    from .skill_governance import SkillGovernanceRegistry
    from .turn_context import TurnContext
from .turn_context import ProactiveTurnYield

from ..context import ContextBuilder, Conversation
from ..core.schema import AppConfig, ToolsConfig
from ..llm import LLMResponse
from ..llm.base import LLMClient
from ..llm.schema import Message, ToolCall, ToolDefinition
from ..memory import is_failed_memory_edit_result, summarize_memory_edit_failure
from ..tools import ToolRegistry, is_claude_code_stream_json_command
from .run_helpers import (
    _debug_print_responder_output,
    _emit_reasoning_block_if_needed,
    _raise_if_cancel_requested,
    _surface_error_message,
)
from .skill_governance import (
    build_skill_deferral_text,
    build_skill_prerequisite_messages,
)
from .staged_planning import (
    STAGE1_SYNTHETIC_TOOL_NAME,
    build_plan_context_message,
    build_stage1_findings_for_conversation,
    build_stage1_findings_overlay_message,
    build_stage3_plan_overlay_message,
    format_stage2_plan_for_tui,
    run_stage1_information_gathering,
    run_stage2_brain_planning,
)
from .ui_event_console import AgentUiPort

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _CommonGroundTurnDebug:
    """Stable per-turn common-ground debug snapshot captured during overlay build."""

    scope_id: str | None = None
    anchor_shared_rev: int | None = None
    current_shared_rev: int | None = None
    store_available: bool = False


def _is_error_tool_result(result: object) -> bool:
    """Return True when a tool result is an error ToolResult."""
    from ..tools.registry import ToolResult

    return isinstance(result, ToolResult) and result.is_error


def _can_short_circuit_terminal_round(
    *,
    tool_calls: list[ToolCall],
    tool_results: dict[str, object],
    tools_config: ToolsConfig | None,
) -> bool:
    """Return True when this tool round can terminate responder immediately."""
    if tools_config is None:
        return False
    cfg = tools_config.terminal_tool_short_circuit
    if not cfg.enabled:
        return False
    if not tool_calls:
        return False

    allowed_tools = set(cfg.allowed_tools)
    allowed_schedule_actions = set(cfg.schedule_action_allowed_actions)
    for tool_call in tool_calls:
        if tool_call.name not in allowed_tools:
            return False
        if tool_call.name == "schedule_action":
            action = tool_call.arguments.get("action")
            if not isinstance(action, str) or action not in allowed_schedule_actions:
                return False
        result = tool_results.get(tool_call.id)
        if result is None or _is_error_tool_result(result):
            return False
    return True


def _format_memory_edit_failure_summaries(summaries: list[str]) -> str:
    """Format per-call memory_edit failure summaries for warning output."""
    if not summaries:
        return "unknown_failure"
    unique: list[str] = []
    for item in summaries:
        if item not in unique:
            unique.append(item)
    text = " | ".join(unique[:2])
    if len(unique) > 2:
        text += " | +"
    return text


def _make_synthetic_message_overlay(
    extra_messages: list[Message] | tuple[Message, ...],
) -> Callable[[list[Message]], list[Message]]:
    """Return an overlay callback that appends synthetic context messages."""
    extras = tuple(extra_messages)

    def _overlay(messages: list[Message]) -> list[Message]:
        return [*messages, *extras]

    return _overlay


def _compose_message_overlays(
    first: Callable[[list[Message]], list[Message]] | None,
    second: Callable[[list[Message]], list[Message]] | None,
) -> Callable[[list[Message]], list[Message]] | None:
    """Compose two message overlays in order."""
    if first is None:
        return second
    if second is None:
        return first

    def _overlay(messages: list[Message]) -> list[Message]:
        return second(first(messages))

    return _overlay


def _load_plan_context_files(
    *,
    rel_paths: list[str],
    builder: ContextBuilder,
    console: AgentUiPort,
) -> list[tuple[str, str]]:
    """Load plan_context_files from agent_os_dir and warn on failure."""
    agent_os_dir = getattr(builder, "agent_os_dir", None)
    if not isinstance(agent_os_dir, Path):
        if rel_paths:
            console.print_warning(
                "plan_context_files unavailable: agent_os_dir is not set.",
                indent=2,
            )
        return []

    loaded: list[tuple[str, str]] = []
    for rel_path in rel_paths:
        try:
            content = (agent_os_dir / rel_path).read_text(encoding="utf-8")
            loaded.append((rel_path, content))
        except Exception as error:
            console.print_warning(
                f"plan_context_files: skipping {rel_path}: "
                f"{_surface_error_message(error)}",
                indent=2,
            )
    return loaded


def _maybe_defer_tool_round_for_skills(
    *,
    response: LLMResponse,
    conversation: Conversation,
    console: AgentUiPort,
    skill_registry: "SkillGovernanceRegistry | None",
) -> dict[str, object] | None:
    """Inject missing skill guides and defer the current tool round once."""
    if skill_registry is None:
        return None

    loaded_skill_ids = skill_registry.loaded_skill_ids_from_conversation(conversation)
    requirements = skill_registry.find_missing_requirements(
        response.tool_calls,
        loaded_skill_ids=loaded_skill_ids,
    )
    if not requirements:
        return None

    injected = skill_registry.build_injected_guides(requirements)
    if not injected:
        return None

    from ..tools.registry import ToolResult

    deferral_text = build_skill_deferral_text(
        missing_skill_ids=[item.skill_id for item in injected],
    )
    tool_results_this_round: dict[str, object] = {}
    for tool_call in response.tool_calls:
        console.print_tool_call(tool_call)
        result = ToolResult(deferral_text, is_error=True)
        console.print_tool_result(tool_call, result.content)
        conversation.add_tool_result(tool_call.id, tool_call.name, result.content)
        tool_results_this_round[tool_call.id] = result

    for item in injected:
        call_msg, result_msg = build_skill_prerequisite_messages(item)
        conversation.add_assistant_with_tools(None, call_msg.tool_calls or [])
        conversation.add_tool_result(
            result_msg.tool_call_id or item.call.id,
            result_msg.name or item.call.name,
            result_msg.content or "",
        )
    return tool_results_this_round


def _run_responder(
    client: LLMClient,
    messages: list[Message],
    tools: list[ToolDefinition],
    conversation: Conversation,
    builder: ContextBuilder,
    registry: ToolRegistry,
    console: AgentUiPort,
    on_before_tool_call: Callable[[ToolCall], None] | None = None,
    memory_edit_allow_failure: bool = False,
    max_iterations: int = 10,
    memory_edit_turn_retry_limit: int = 3,
    is_cancel_requested: Callable[[], bool] | None = None,
    on_cancel_pending: Callable[[], None] | None = None,
    message_overlay: Callable[[list[Message]], list[Message]] | None = None,
    on_model_response: Callable[[LLMResponse], None] | None = None,
    thinking_channel: str | None = None,
    thinking_sender: str | None = None,
    tools_config: ToolsConfig | None = None,
    skill_registry: "SkillGovernanceRegistry | None" = None,
    turn_context: "TurnContext | None" = None,
) -> LLMResponse:
    """Run responder with the tool-call loop and return the final response."""
    if message_overlay is not None:
        messages = message_overlay(messages)
    _raise_if_cancel_requested(is_cancel_requested, on_pending=on_cancel_pending)
    with console.spinner():
        response = client.chat_with_tools(messages, tools)
    if on_model_response is not None:
        on_model_response(response)
    _raise_if_cancel_requested(is_cancel_requested, on_pending=on_cancel_pending)
    _debug_print_responder_output(console, response, label="responder")
    _emit_reasoning_block_if_needed(
        console,
        response,
        channel=thinking_channel,
        sender=thinking_sender,
    )

    memory_edit_turn_fail_streak = 0
    iterations = 0
    while response.has_tool_calls():
        iterations += 1
        if iterations > max_iterations:
            logger.warning(
                "Responder loop exceeded %d iterations; breaking.",
                max_iterations,
            )
            console.print_warning(
                f"Tool loop exceeded {max_iterations} iterations; stopping.",
            )
            break
        chunk = response.content or ""
        if chunk.strip():
            console.print_assistant(chunk)

        conversation.add_assistant_with_tools(
            response.content,
            response.tool_calls,
            reasoning_content=response.reasoning_content,
            reasoning_details=response.reasoning_details,
        )

        failed_memory_edit_this_round = False
        memory_edit_failure_summaries: list[str] = []
        tool_results_this_round: dict[str, object] = {}
        deferred_results = _maybe_defer_tool_round_for_skills(
            response=response,
            conversation=conversation,
            console=console,
            skill_registry=skill_registry,
        )
        if deferred_results is not None:
            tool_results_this_round = deferred_results
            messages = builder.build(conversation)
            if message_overlay is not None:
                messages = message_overlay(messages)
            _raise_if_cancel_requested(
                is_cancel_requested,
                on_pending=on_cancel_pending,
            )
            with console.spinner():
                response = client.chat_with_tools(messages, tools)
            if on_model_response is not None:
                on_model_response(response)
            _raise_if_cancel_requested(
                is_cancel_requested,
                on_pending=on_cancel_pending,
            )
            _debug_print_responder_output(console, response, label="responder")
            _emit_reasoning_block_if_needed(
                console,
                response,
                channel=thinking_channel,
                sender=thinking_sender,
            )
            continue

        for tool_call in response.tool_calls:
            _raise_if_cancel_requested(
                is_cancel_requested,
                on_pending=on_cancel_pending,
            )
            if not registry.has_tool(tool_call.name):
                from ..tools.registry import ToolResult

                result = ToolResult(
                    f"Error: Unknown tool '{tool_call.name}'",
                    is_error=True,
                )
                conversation.add_tool_result(
                    tool_call.id,
                    tool_call.name,
                    result.content,
                )
                tool_results_this_round[tool_call.id] = result
                continue
            console.print_tool_call(tool_call)
            if on_before_tool_call is not None:
                on_before_tool_call(tool_call)

            shell_command = tool_call.arguments.get("command")
            skip_spinner = (
                tool_call.name == "gui_task"
                or (
                    tool_call.name == "execute_shell"
                    and console.show_tool_use
                    and isinstance(shell_command, str)
                    and is_claude_code_stream_json_command(shell_command)
                )
            )
            if skip_spinner:
                result = registry.execute(tool_call)
            else:
                with console.spinner("Executing..."):
                    result = registry.execute(tool_call)
            console.print_tool_result(tool_call, result.content)
            conversation.add_tool_result(tool_call.id, tool_call.name, result.content)
            tool_results_this_round[tool_call.id] = result
            if turn_context is not None and turn_context.proactive_yield is not None:
                scope_id = turn_context.proactive_yield.scope_id
                raise ProactiveTurnYield(scope_id)
            _raise_if_cancel_requested(
                is_cancel_requested,
                on_pending=on_cancel_pending,
            )
            if (
                tool_call.name == "memory_edit"
                and isinstance(result.content, str)
                and is_failed_memory_edit_result(result.content)
            ):
                failed_memory_edit_this_round = True
                summary = summarize_memory_edit_failure(result.content)
                if summary:
                    memory_edit_failure_summaries.append(summary)

        if failed_memory_edit_this_round:
            memory_edit_turn_fail_streak += 1
            failure_detail = _format_memory_edit_failure_summaries(
                memory_edit_failure_summaries,
            )
            if memory_edit_turn_fail_streak >= memory_edit_turn_retry_limit:
                if memory_edit_allow_failure:
                    console.print_warning(
                        "memory_edit turn-level retries exhausted"
                        f" ({failure_detail}); failed "
                        f"{memory_edit_turn_fail_streak} time(s); "
                        "allow_failure=true, continuing turn.",
                    )
                    break
                raise RuntimeError(
                    "memory_edit turn-level retries exhausted "
                    f"({failure_detail}); failed "
                    f"{memory_edit_turn_fail_streak} time(s); fail-closed for this turn."
                )
            console.print_warning(
                "memory_edit failed this round "
                f"({failure_detail}); retrying turn "
                f"({memory_edit_turn_fail_streak}/{memory_edit_turn_retry_limit})",
                indent=2,
            )
        else:
            memory_edit_turn_fail_streak = 0

        if _can_short_circuit_terminal_round(
            tool_calls=response.tool_calls,
            tool_results=tool_results_this_round,
            tools_config=tools_config,
        ):
            tool_names = [tool_call.name for tool_call in response.tool_calls]
            logger.info(
                "terminal_tool_short_circuit hit: tools=%s count=%d",
                ",".join(tool_names),
                len(tool_names),
            )
            if console.debug:
                console.print_debug(
                    "responder",
                    "terminal_tool_short_circuit hit: "
                    f"tools=[{', '.join(tool_names)}]",
                )
            return LLMResponse(
                content=None,
                tool_calls=[],
                finish_reason="terminal_tool_short_circuit",
            )

        messages = builder.build(conversation)
        if message_overlay is not None:
            messages = message_overlay(messages)
        _raise_if_cancel_requested(is_cancel_requested, on_pending=on_cancel_pending)
        with console.spinner():
            response = client.chat_with_tools(messages, tools)
        if on_model_response is not None:
            on_model_response(response)
        _raise_if_cancel_requested(is_cancel_requested, on_pending=on_cancel_pending)
        _debug_print_responder_output(console, response, label="responder")
        _emit_reasoning_block_if_needed(
            console,
            response,
            channel=thinking_channel,
            sender=thinking_sender,
        )

    return response


def _run_brain_responder(
    *,
    client: LLMClient,
    messages: list[Message],
    tools: list[ToolDefinition],
    conversation: Conversation,
    builder: ContextBuilder,
    registry: ToolRegistry,
    console: AgentUiPort,
    config: AppConfig,
    channel: str,
    sender: str | None,
    on_before_tool_call: Callable[[ToolCall], None] | None = None,
    memory_edit_allow_failure: bool = False,
    max_iterations: int = 10,
    memory_edit_turn_retry_limit: int = 3,
    is_cancel_requested: Callable[[], bool] | None = None,
    on_cancel_pending: Callable[[], None] | None = None,
    message_overlay: Callable[[list[Message]], list[Message]] | None = None,
    on_model_response: Callable[[LLMResponse], None] | None = None,
    run_responder_fn: Callable[..., LLMResponse] | None = None,
    stage1_gather_fn: Callable[..., object] = run_stage1_information_gathering,
    stage2_plan_fn: Callable[..., object | None] = run_stage2_brain_planning,
    skill_registry: "SkillGovernanceRegistry | None" = None,
    turn_context: "TurnContext | None" = None,
) -> LLMResponse:
    """Run the brain responder, optionally using staged planning."""
    tools_cfg = (
        config.tools
        if isinstance(getattr(config, "tools", None), ToolsConfig)
        else None
    )
    if run_responder_fn is None:
        run_responder_fn = _run_responder

    brain_cfg = config.agents.get("brain")
    staged = getattr(brain_cfg, "staged_planning", None)
    if staged is None or not staged.enabled:
        return run_responder_fn(
            client,
            messages,
            tools,
            conversation,
            builder,
            registry,
            console,
            on_before_tool_call=on_before_tool_call,
            memory_edit_allow_failure=memory_edit_allow_failure,
            max_iterations=max_iterations,
            memory_edit_turn_retry_limit=memory_edit_turn_retry_limit,
            is_cancel_requested=is_cancel_requested,
            on_cancel_pending=on_cancel_pending,
            message_overlay=message_overlay,
            on_model_response=on_model_response,
            thinking_channel=channel,
            thinking_sender=sender,
            tools_config=tools_cfg,
            skill_registry=skill_registry,
            turn_context=turn_context,
        )

    def raise_cancel() -> None:
        _raise_if_cancel_requested(
            is_cancel_requested,
            on_pending=on_cancel_pending,
        )

    overlayed_messages = (
        list(message_overlay(messages))
        if message_overlay is not None
        else list(messages)
    )
    stage1_max_iterations = max(1, min(staged.gather_max_iterations, max_iterations))
    has_prior_findings = any(
        getattr(entry, "name", None) == STAGE1_SYNTHETIC_TOOL_NAME
        for entry in conversation.get_messages()
    )

    try:
        console.print_info("Stage 1/3: gather")
        stage1 = stage1_gather_fn(
            client=client,
            messages=overlayed_messages,
            all_tools=tools,
            registry=registry,
            console=console,
            raise_if_cancel_requested=raise_cancel,
            max_iterations=stage1_max_iterations,
            skip_memory_search_gate=has_prior_findings,
        )
        if console.debug:
            console.print_debug(
                "staged-plan",
                f"stage1 tool_calls={stage1.tool_calls} "
                f"transcript_chars={len(stage1.transcript)}",
            )

        if (
            stage1.findings_text
            and stage1.findings_text != "(no stage1 tools available)"
        ):
            stage1_call, stage1_content = build_stage1_findings_for_conversation(
                stage1.findings_text,
            )
            conversation.add_assistant_with_tools(None, [stage1_call])
            conversation.add_tool_result(
                stage1_call.id,
                stage1_call.name,
                stage1_content,
            )

        console.print_info("Stage 2/3: plan")
        stage2_messages = list(overlayed_messages)
        plan_context_loaded = _load_plan_context_files(
            rel_paths=staged.plan_context_files,
            builder=builder,
            console=console,
        )
        plan_context_msg = build_plan_context_message(plan_context_loaded)
        if plan_context_msg is not None:
            stage2_messages.append(plan_context_msg)
        stage2 = stage2_plan_fn(
            client=client,
            messages=stage2_messages,
            stage1=stage1,
            console=console,
            raise_if_cancel_requested=raise_cancel,
        )
        if stage2 is None:
            console.print_warning(
                "Stage 2 planning failed; falling back to legacy responder loop.",
                indent=2,
            )
            return run_responder_fn(
                client,
                messages,
                tools,
                conversation,
                builder,
                registry,
                console,
                on_before_tool_call=on_before_tool_call,
                memory_edit_allow_failure=memory_edit_allow_failure,
                max_iterations=max_iterations,
                memory_edit_turn_retry_limit=memory_edit_turn_retry_limit,
                is_cancel_requested=is_cancel_requested,
                on_cancel_pending=on_cancel_pending,
                message_overlay=message_overlay,
                on_model_response=on_model_response,
                thinking_channel=channel,
                thinking_sender=sender,
                tools_config=tools_cfg,
                skill_registry=skill_registry,
                turn_context=turn_context,
            )
    except KeyboardInterrupt:
        raise
    except Exception as error:
        logger.warning("Staged planning failed; falling back to legacy responder", exc_info=True)
        console.print_warning(
            "Staged planning failed; falling back to legacy responder loop: "
            f"{_surface_error_message(error)}",
            indent=2,
        )
        return run_responder_fn(
            client,
            messages,
            tools,
            conversation,
            builder,
            registry,
            console,
            on_before_tool_call=on_before_tool_call,
            memory_edit_allow_failure=memory_edit_allow_failure,
            max_iterations=max_iterations,
            memory_edit_turn_retry_limit=memory_edit_turn_retry_limit,
            is_cancel_requested=is_cancel_requested,
            on_cancel_pending=on_cancel_pending,
            message_overlay=message_overlay,
            on_model_response=on_model_response,
            thinking_channel=channel,
            thinking_sender=sender,
            tools_config=tools_cfg,
            skill_registry=skill_registry,
            turn_context=turn_context,
        )

    plan_text = format_stage2_plan_for_tui(stage2.plan_text)
    console.print_inner_thoughts(channel, sender, f"[PLAN][Stage2]\n{plan_text}")

    stage3_overlay_messages: list[Message] = [
        build_stage1_findings_overlay_message(stage1.findings_text),
        build_stage3_plan_overlay_message(stage2.plan_text),
    ]
    if plan_context_msg is not None:
        stage3_overlay_messages.append(plan_context_msg)
    stage3_extra = _make_synthetic_message_overlay(stage3_overlay_messages)
    stage3_overlay = _compose_message_overlays(message_overlay, stage3_extra)

    console.print_info("Stage 3/3: execute")
    return run_responder_fn(
        client,
        messages,
        tools,
        conversation,
        builder,
        registry,
        console,
        on_before_tool_call=on_before_tool_call,
        memory_edit_allow_failure=memory_edit_allow_failure,
        max_iterations=max_iterations,
        memory_edit_turn_retry_limit=memory_edit_turn_retry_limit,
        is_cancel_requested=is_cancel_requested,
        on_cancel_pending=on_cancel_pending,
        message_overlay=stage3_overlay,
        on_model_response=on_model_response,
        thinking_channel=channel,
        thinking_sender=sender,
        tools_config=tools_cfg,
        skill_registry=skill_registry,
        turn_context=turn_context,
    )


def _build_common_ground_overlay(
    *,
    shared_state_store: SharedStateStore | None,
    config: AppConfig,
    turn_metadata: dict[str, object] | None,
    console: AgentUiPort,
    debug: bool,
) -> tuple[Callable[[list[Message]], list[Message]] | None, _CommonGroundTurnDebug]:
    """Build per-turn common-ground synthetic tool overlay when revisions diverge."""
    metadata = turn_metadata or {}
    scope_id = metadata.get("scope_id")
    anchor_shared_rev = metadata.get("anchor_shared_rev")
    debug_scope_id = scope_id if isinstance(scope_id, str) and scope_id else None
    debug_anchor_rev = anchor_shared_rev if isinstance(anchor_shared_rev, int) else None
    base_debug = _CommonGroundTurnDebug(
        scope_id=debug_scope_id,
        anchor_shared_rev=debug_anchor_rev,
        store_available=shared_state_store is not None,
    )
    if shared_state_store is None:
        return None, base_debug

    cg_cfg = config.context.common_ground
    if not cg_cfg.enabled:
        return None, base_debug
    if debug_scope_id is None:
        return None, base_debug
    if debug_anchor_rev is None:
        return None, base_debug

    current_shared_rev = shared_state_store.get_current_rev(debug_scope_id)
    current_debug = _CommonGroundTurnDebug(
        scope_id=debug_scope_id,
        anchor_shared_rev=debug_anchor_rev,
        current_shared_rev=current_shared_rev,
        store_available=True,
    )
    if debug_anchor_rev > current_shared_rev:
        console.print_warning(
            "common-ground skipped: cache underflow "
            f"(anchor={debug_anchor_rev} > current={current_shared_rev})",
            indent=2,
        )
        if debug:
            console.print_debug(
                "common-ground",
                "skip underflow "
                f"scope={debug_scope_id} anchor={debug_anchor_rev} "
                f"current={current_shared_rev}",
            )
        return None, current_debug

    if debug_anchor_rev == current_shared_rev:
        if debug:
            console.print_debug(
                "common-ground",
                f"no inject scope={debug_scope_id} anchor=current={debug_anchor_rev}",
            )
        return None, current_debug

    pair = shared_state_store.build_common_ground_synthetic_messages(
        scope_id=debug_scope_id,
        upto_rev=debug_anchor_rev,
        current_rev=current_shared_rev,
        max_entries=cg_cfg.max_entries,
        max_chars=cg_cfg.max_chars,
        max_entry_chars=cg_cfg.max_entry_chars,
    )
    if pair is None:
        return None, current_debug

    if debug:
        tool_text = pair[1].content if isinstance(pair[1].content, str) else ""
        console.print_debug(
            "common-ground",
            "injected "
            f"scope={debug_scope_id} anchor={debug_anchor_rev} "
            f"current={current_shared_rev} chars={len(tool_text)}",
        )
    return _make_synthetic_message_overlay(list(pair)), current_debug
