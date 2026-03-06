"""Agent core logic: responder + memory sync.

Extracted from cli/app.py to decouple agent logic from CLI adapter.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta
import logging
from pathlib import Path
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .adapters.protocol import ChannelAdapter
    from .scope import ScopeResolver
    from .shared_state import SharedStateStore

from ..context import ContextBuilder, Conversation
from ..core.schema import AppConfig, MaintenanceConfig
from ..llm import LLMResponse
from ..llm.base import LLMClient
from ..llm.schema import ContextLengthExceededError
from ..memory import find_missing_memory_sync_targets
from ..memory.backup import MemoryBackupManager
from ..session import SessionManager
from ..timezone_utils import get_tz, now as tz_now
from ..tools import ToolRegistry
from ..tui.sink import UiSink
from ..workspace import WorkspaceManager
from . import responder as _responder
from .queue import PersistentPriorityQueue
from .responder import _build_common_ground_overlay
from .run_helpers import (
    _latest_intermediate_text,
    _latest_nonempty_assistant_content,
    _resolve_final_content,
    _sanitize_error_message,
    _strip_timestamp_prefix,
)
from .schema import InboundMessage, MaintenanceSentinel, ShutdownSentinel
from .scope import DEFAULT_SCOPE_RESOLVER
from .staged_planning import run_stage1_information_gathering, run_stage2_brain_planning
from .tool_setup import setup_tools
from .turn_context import TurnContext
from .turn_effects import analyze_turn_effects
# Re-exported for backward compatibility with tests importing from
# chat_agent.agent.core. AgentCore itself does not use every symbol here.
from .turn_runtime import (
    _EMPTY_RESPONSE_NUDGE,
    _LatestTokenStatus,
    _TurnMemorySnapshot,
    _TurnTokenUsage,
    _build_memory_sync_reminder,
    _inject_brain_failure_record,
    _patch_interrupted_tool_calls,
    _rollback_turn_memory_changes,
    _run_empty_response_fallback,
    _run_memory_archive,
    _run_memory_sync_side_channel,
)
from .ui_event_console import AgentUiPort, UiEventConsole

logger = logging.getLogger(__name__)


def _run_responder(*args, **kwargs) -> LLMResponse:
    """Compatibility wrapper for the responder loop implementation."""
    return _responder._run_responder(*args, **kwargs)


def _run_brain_responder(**kwargs) -> LLMResponse:
    """Compatibility wrapper for staged planning plus responder execution."""
    return _responder._run_brain_responder(
        **kwargs,
        run_responder_fn=_run_responder,
        stage1_gather_fn=run_stage1_information_gathering,
        stage2_plan_fn=run_stage2_brain_planning,
    )



class _MaintenanceScheduler:
    """Background timer that enqueues MaintenanceSentinel at daily_hour.

    Retries every retry_interval_minutes until latest_hour.
    Skips the day if latest_hour is passed without a successful run.
    """

    def __init__(
        self,
        queue: PersistentPriorityQueue,
        config: MaintenanceConfig,
    ):
        self._queue = queue
        self._config = config
        self._tz = get_tz()
        self._ran_today = False
        self._last_date: date | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def mark_done(self) -> None:
        """Called after successful maintenance to prevent re-trigger today."""
        self._ran_today = True

    def _loop_once(self) -> bool:
        """Check if maintenance is due. Returns True if sentinel enqueued."""
        now = datetime.now(self._tz)
        today = now.date()

        # Reset flag on new day
        if self._last_date != today:
            self._ran_today = False
            self._last_date = today

        if self._ran_today:
            return False

        hour = now.hour
        if hour < self._config.daily_hour:
            return False
        if hour >= self._config.latest_hour:
            # Past window; skip today
            self._ran_today = True
            logger.info("Maintenance window passed (%02d:00-%02d:00), skipping today",
                        self._config.daily_hour, self._config.latest_hour)
            return False

        self._queue.put(MaintenanceSentinel())
        return True

    def _loop(self) -> None:
        while not self._stop.wait(timeout=60):
            if self._loop_once():
                # Wait retry interval before next attempt
                self._stop.wait(timeout=self._config.retry_interval_minutes * 60)


class AgentCore:
    """Core agent logic: responder + memory sync."""

    def __init__(
        self,
        *,
        client: LLMClient,
        conversation: Conversation,
        builder: ContextBuilder,
        registry: ToolRegistry,
        ui_sink: UiSink,
        workspace: WorkspaceManager,
        config: AppConfig,
        agent_os_dir: Path,
        user_id: str,
        session_mgr: SessionManager | None = None,
        display_name: str = "",
        # Memory
        memory_edit_allow_failure: bool = False,
        memory_backup_mgr: MemoryBackupManager | None = None,
        # Queue
        queue: PersistentPriorityQueue | None = None,
        # Turn context for send_message tool
        turn_context: TurnContext | None = None,
        turn_cancel: object | None = None,
        shared_state_store: SharedStateStore | None = None,
        scope_resolver: ScopeResolver | None = None,
        memory_sync_client: LLMClient | None = None,
        ui_debug: bool = False,
        ui_show_tool_use: bool = False,
        ui_timezone: str | None = None,
        ui_gui_intent_max_chars: int | None = None,
    ):
        self.client = client
        self.memory_sync_client = memory_sync_client
        self.conversation = conversation
        self.builder = builder
        self.registry = registry
        self.ui_sink = ui_sink
        self.console: AgentUiPort = UiEventConsole(
            ui_sink,
            debug=ui_debug,
            show_tool_use=ui_show_tool_use,
        )
        if ui_timezone:
            self.console.set_timezone(ui_timezone)
        self.console.gui_intent_max_chars = ui_gui_intent_max_chars
        self.workspace = workspace
        self.config = config
        self.agent_os_dir = agent_os_dir
        self.user_id = user_id
        self.session_mgr = session_mgr
        self.display_name = display_name
        self.memory_edit_allow_failure = memory_edit_allow_failure
        self.memory_backup_mgr = memory_backup_mgr
        self._queue = queue
        self.turn_context = turn_context
        self.turn_cancel = turn_cancel
        self.shared_state_store = shared_state_store
        self.scope_resolver = scope_resolver or DEFAULT_SCOPE_RESOLVER
        self._maintenance_scheduler: _MaintenanceScheduler | None = None
        self._turns_since_memory_sync: int = 0
        self.adapters: dict[str, ChannelAdapter] = {}
        brain_cfg = self.config.agents.get("brain")
        self._brain_provider = brain_cfg.llm.provider if brain_cfg is not None else ""
        self._soft_max_prompt_tokens = self.config.context.soft_max_prompt_tokens
        self._latest_token_status = _LatestTokenStatus()
        self._turn_token_usage = _TurnTokenUsage()

    def _reset_turn_token_usage(self) -> None:
        """Reset per-turn token aggregation state."""
        self._turn_token_usage = _TurnTokenUsage()

    def _record_brain_response_usage(self, response: LLMResponse) -> None:
        """Record usage from each brain model response in the current turn."""
        self._turn_token_usage.record(response)

    def _finalize_turn_token_status(self) -> None:
        """Publish per-turn aggregated usage to the status model."""
        agg = self._turn_token_usage
        if agg.usage_available:
            self._latest_token_status = _LatestTokenStatus(
                prompt_tokens=agg.max_prompt_tokens,
                completion_tokens=agg.completion_tokens_for_max_prompt,
                total_tokens=agg.total_tokens_for_max_prompt,
                usage_available=True,
                missing_usage=False,
            )
            return

        if self._brain_provider == "copilot" and agg.saw_missing_usage:
            self._latest_token_status = _LatestTokenStatus(
                usage_available=False,
                missing_usage=True,
            )

    def get_token_status_text(self) -> str:
        """Return token status text for toolbar and processing headers."""
        limit = self._soft_max_prompt_tokens
        state = self._latest_token_status
        if state.usage_available and state.prompt_tokens is not None:
            pct = state.prompt_tokens / limit * 100 if limit else 0
            suffix = " soft-over" if state.prompt_tokens > limit else ""
            return f"tok {state.prompt_tokens:,}/{limit:,} ({pct:.1f}%){suffix}"
        if state.missing_usage:
            return f"tok unavailable/{limit:,} (copilot no usage)"
        return f"tok --/{limit:,} (--.-%)"

    def _is_soft_limit_exceeded(self) -> bool:
        """Check if current turn exceeded soft prompt token limit."""
        state = self._latest_token_status
        if not state.usage_available or state.prompt_tokens is None:
            return False
        return state.prompt_tokens > self._soft_max_prompt_tokens

    def _apply_soft_prompt_compaction(self) -> None:
        """Compact history after a turn when soft token budget is exceeded."""
        state = self._latest_token_status
        if not state.usage_available:
            return
        prompt_tokens = state.prompt_tokens
        if prompt_tokens is None or prompt_tokens <= self._soft_max_prompt_tokens:
            return
        removed = self.conversation.compact(self.config.context.preserve_turns)
        if removed <= 0:
            return
        if self.session_mgr is not None:
            self.session_mgr.rewrite_messages(self.conversation.get_messages())
        self.console.print_warning(
            "Soft token limit exceeded "
            f"({prompt_tokens:,}/{self._soft_max_prompt_tokens:,}); "
            f"compacted {removed} messages.",
            indent=2,
        )

    def run_turn(
        self,
        user_input: str,
        *,
        output_fn: Callable[[str | None], None] | None = None,
        channel: str = "cli",
        sender: str | None = None,
        timestamp: datetime | None = None,
    ) -> None:
        """Process one user turn.

        Full lifecycle:
        1. Add user message to conversation
        2. Responder (LLM + tool loop)
        3. Memory sync side-channel
        4. Memory archive + backup hooks

        Handles ContextLengthExceededError (emergency compact + single retry),
        KeyboardInterrupt (patch incomplete tool calls), and general exceptions
        (rollback memory + restore conversation).

        Args:
            output_fn: Callback for the final response.  When *None* the
                direct-call path is used with channel display sections.
            channel: Channel name for display (direct-call path only).
            sender: Sender name for display (direct-call path only).
        """
        if output_fn is not None:
            _output = output_fn
        else:
            # Direct-call path: show channel display sections
            self.console.print_inbound(channel, sender, user_input)
            self.console.print_processing(channel, sender)

            def _output(content: str | None) -> None:
                self.console.print_inner_thoughts(channel, sender, content)
        debug = self.console.debug
        pre_turn_anchor = len(self.conversation.get_messages())
        turn_metadata = dict(self.turn_context.metadata) if self.turn_context is not None else None
        self.conversation.add(
            "user",
            user_input,
            channel=channel,
            sender=sender,
            timestamp=timestamp,
            metadata=turn_metadata,
        )
        messages = self.builder.build(self.conversation)
        cg_scope_id = turn_metadata.get("scope_id") if isinstance(turn_metadata, dict) else None
        cg_anchor_rev = turn_metadata.get("anchor_shared_rev") if isinstance(turn_metadata, dict) else None
        cg_turn_start_current_rev: int | None = None
        if (
            getattr(self, "shared_state_store", None) is not None
            and isinstance(cg_scope_id, str)
            and cg_scope_id
        ):
            cg_turn_start_current_rev = self.shared_state_store.get_current_rev(cg_scope_id)
        common_ground_overlay, _common_ground_scope = _build_common_ground_overlay(
            shared_state_store=getattr(self, "shared_state_store", None),
            config=self.config,
            turn_metadata=turn_metadata,
            console=self.console,
            debug=debug,
        )
        if debug:
            if not self.config.context.common_ground.enabled:
                self.console.print_debug("common-ground-turn", "disabled")
            elif not isinstance(cg_scope_id, str) or not cg_scope_id:
                self.console.print_debug("common-ground-turn", "skip no_scope")
            elif not isinstance(cg_anchor_rev, int):
                self.console.print_debug("common-ground-turn", f"skip no_anchor scope={cg_scope_id}")
            elif cg_turn_start_current_rev is None:
                self.console.print_debug(
                    "common-ground-turn",
                    f"skip no_store scope={cg_scope_id} anchor={cg_anchor_rev}",
                )
            else:
                self.console.print_debug(
                    "common-ground-turn",
                    "injected="
                    f"{common_ground_overlay is not None} "
                    f"scope={cg_scope_id} "
                    f"anchor={cg_anchor_rev} "
                    f"current={cg_turn_start_current_rev}",
                )

        if debug:
            # Show the last user message as seen by LLM (with timestamp prefix)
            for m in reversed(messages):
                if m.role == "user" and isinstance(m.content, str):
                    self.console.print_debug("context", m.content[:200])
                    break

        turn_memory_snapshot = _TurnMemorySnapshot(agent_os_dir=self.agent_os_dir)
        turn_anchor = len(self.conversation.get_messages())

        try:
            tools = self.registry.get_definitions()
            _is_cancel = getattr(self.turn_cancel, "is_requested", None)
            _cancel_pending = getattr(self.turn_cancel, "mark_pending", None)

            # === Responder ===
            self._reset_turn_token_usage()
            response = _run_brain_responder(
                client=self.client,
                messages=messages,
                tools=tools,
                conversation=self.conversation,
                builder=self.builder,
                registry=self.registry,
                console=self.console,
                config=self.config,
                channel=channel,
                sender=sender,
                on_before_tool_call=turn_memory_snapshot.capture_from_tool_call,
                memory_edit_allow_failure=self.memory_edit_allow_failure,
                max_iterations=self.config.tools.max_tool_iterations,
                memory_edit_turn_retry_limit=self.config.tools.memory_edit.turn_retry_limit,
                is_cancel_requested=_is_cancel,
                on_cancel_pending=_cancel_pending,
                message_overlay=common_ground_overlay,
                on_model_response=self._record_brain_response_usage,
            )
            self._finalize_turn_token_status()
            final_content, used_fallback_content = _resolve_final_content(
                response.content,
                self.conversation.get_messages()[turn_anchor:],
            )
            final_content = _strip_timestamp_prefix(final_content)
            if debug:
                self.console.print_debug(
                    "resolve",
                    f"final_content_chars={len(final_content)}, "
                    f"used_fallback={used_fallback_content}",
                )

            # === Memory sync (side-channel, no conversation mutation) ===
            # Tracks consecutive non-heartbeat turns where the agent did not
            # naturally update memory targets (e.g. recent.md).  When the
            # counter reaches every_n_turns, forces one sync call, then resets.
            # every_n_turns=null disables forced sync entirely.
            is_system_heartbeat = (
                self.turn_context is not None
                and self.turn_context.metadata.get("system")
            )
            sync_cfg = self.config.tools.memory_sync
            should_sync = False
            if not is_system_heartbeat and sync_cfg.every_n_turns is not None:
                sync_turn_messages = self.conversation.get_messages()[turn_anchor:]
                missing = find_missing_memory_sync_targets(sync_turn_messages)
                if not missing:
                    # Agent updated targets naturally this turn
                    self._turns_since_memory_sync = 0
                else:
                    self._turns_since_memory_sync += 1
                    if self._turns_since_memory_sync >= sync_cfg.every_n_turns:
                        should_sync = True
                if debug:
                    self.console.print_debug(
                        "memory-sync",
                        f"missing={bool(missing)}, "
                        f"counter={self._turns_since_memory_sync}/{sync_cfg.every_n_turns}",
                    )
            elif debug:
                reason = "heartbeat" if is_system_heartbeat else "disabled"
                self.console.print_debug("memory-sync", f"skipped: {reason}")

            if should_sync:
                try:
                    sync_client = getattr(self, "memory_sync_client", None) or self.client
                    if debug:
                        dispatch = "memory_sync" if sync_client is not self.client else "brain"
                        self.console.print_debug("memory-sync", f"dispatch client={dispatch}")
                    _run_memory_sync_side_channel(
                        sync_client, self.conversation, self.builder,
                        tools, self.registry, self.console,
                        missing_targets=missing,  # type: ignore[possibly-undefined]
                        turns_accumulated=self._turns_since_memory_sync,
                        max_retries=sync_cfg.max_retries,
                        on_before_tool_call=turn_memory_snapshot.capture_from_tool_call,
                        is_cancel_requested=_is_cancel,
                        on_cancel_pending=_cancel_pending,
                    )
                    self._turns_since_memory_sync = 0
                    if debug:
                        self.console.print_debug("memory-sync", "done")
                except ContextLengthExceededError:
                    if debug:
                        self.console.print_debug(
                            "memory-sync", "skipped: context length exceeded",
                        )
                except Exception:
                    if debug:
                        self.console.print_debug("memory-sync", "side-channel failed")

            # === Finalize: thoughts first, then responses ===
            # Text output is inner thoughts (console only); actual delivery
            # happens via send_message tool calls during the responder loop.
            if final_content and not used_fallback_content:
                self.conversation.add("assistant", final_content)
            _output(final_content or None)

            # Flush buffered outbound messages (deferred from send_message)
            if self.turn_context is not None:
                for msg in self.turn_context.pending_outbound:
                    self.console.print_outbound(
                        msg.channel, msg.recipient, msg.body,
                        attachments=msg.attachments or None,
                    )
                self.turn_context.pending_outbound.clear()

            # Sync memory before soft compaction discards old messages.
            # Fires when: soft limit exceeded + accumulated unsync'd turns.
            # Covers: counter sync never ran (heartbeat), or ran but failed.
            # Skips: counter=0 (natural write or successful sync already done).
            if (
                self._is_soft_limit_exceeded()
                and self._turns_since_memory_sync > 0
            ):
                sync_turn_msgs = self.conversation.get_messages()[turn_anchor:]
                pre_compact_missing = find_missing_memory_sync_targets(
                    sync_turn_msgs,
                )
                if pre_compact_missing:
                    try:
                        sync_client = (
                            getattr(self, "memory_sync_client", None)
                            or self.client
                        )
                        _run_memory_sync_side_channel(
                            sync_client,
                            self.conversation,
                            self.builder,
                            tools,
                            self.registry,
                            self.console,
                            missing_targets=pre_compact_missing,
                            turns_accumulated=self._turns_since_memory_sync,
                            max_retries=sync_cfg.max_retries,
                            on_before_tool_call=(
                                turn_memory_snapshot.capture_from_tool_call
                            ),
                            is_cancel_requested=_is_cancel,
                            on_cancel_pending=_cancel_pending,
                        )
                        self._turns_since_memory_sync = 0
                        if debug:
                            self.console.print_debug(
                                "memory-sync", "pre-compaction sync done",
                            )
                    except Exception:
                        if debug:
                            self.console.print_debug(
                                "memory-sync",
                                "pre-compaction sync failed",
                            )

            self._apply_soft_prompt_compaction()

            # Post-turn hooks removed: archive/backup handled by daily maintenance.
            # Only overflow guard (ContextLengthExceededError) still archives.

        except ContextLengthExceededError:
            _rollback_turn_memory_changes(
                turn_memory_snapshot, console=self.console, debug=debug,
            )
            self.conversation._messages = self.conversation._messages[:pre_turn_anchor]

            # Archive to shrink boot files (e.g. recent.md), then reload
            # so builder picks up the smaller content for the retry.
            _run_memory_archive(
                self.agent_os_dir, self.config.maintenance.archive, self.console,
            )
            self.builder.reload_boot_files()
            keep_turns = self.config.context.preserve_turns
            removed = self.conversation.compact(keep_turns)
            if self.session_mgr is not None and removed > 0:
                self.session_mgr.rewrite_messages(self.conversation.get_messages())
            self.console.print_warning(
                "Token limit exceeded. "
                f"Compacting to {keep_turns} turns and retrying once...",
            )

            self.conversation.add(
                "user",
                user_input,
                channel=channel,
                sender=sender,
                metadata=turn_metadata,
            )
            messages = self.builder.build(self.conversation)
            turn_memory_snapshot = _TurnMemorySnapshot(agent_os_dir=self.agent_os_dir)
            try:
                tools = self.registry.get_definitions()
                turn_anchor = len(self.conversation.get_messages())
                self._reset_turn_token_usage()
                response = _run_brain_responder(
                    client=self.client,
                    messages=messages,
                    tools=tools,
                    conversation=self.conversation,
                    builder=self.builder,
                    registry=self.registry,
                    console=self.console,
                    config=self.config,
                    channel=channel,
                    sender=sender,
                    on_before_tool_call=turn_memory_snapshot.capture_from_tool_call,
                    memory_edit_allow_failure=self.memory_edit_allow_failure,
                    max_iterations=self.config.tools.max_tool_iterations,
                    memory_edit_turn_retry_limit=self.config.tools.memory_edit.turn_retry_limit,
                    is_cancel_requested=_is_cancel,
                    on_cancel_pending=_cancel_pending,
                    message_overlay=common_ground_overlay,
                    on_model_response=self._record_brain_response_usage,
                )
                self._finalize_turn_token_status()
                final_content, used_fallback_content = _resolve_final_content(
                    response.content,
                    self.conversation.get_messages()[turn_anchor:],
                )
                final_content = _strip_timestamp_prefix(final_content)
                if final_content and not used_fallback_content:
                    self.conversation.add("assistant", final_content)
                _output(final_content or None)
                self._apply_soft_prompt_compaction()
            except ContextLengthExceededError:
                _rollback_turn_memory_changes(
                    turn_memory_snapshot, console=self.console, debug=debug,
                )
                self.conversation._messages = self.conversation._messages[:pre_turn_anchor]
                self.console.print_error(
                    "Context still too large after emergency overflow compaction."
                )
            except Exception as e:
                _rollback_turn_memory_changes(
                    turn_memory_snapshot, console=self.console, debug=debug,
                )
                self.console.print_error(_sanitize_error_message(str(e)))
                _inject_brain_failure_record(
                    self.conversation, turn_anchor, e, memory_rolled_back=True,
                )
                if self.session_mgr is not None:
                    self.session_mgr.rewrite_messages(self.conversation.get_messages())

        except KeyboardInterrupt:
            # Preserve completed work; patch incomplete tool calls for API consistency
            _patch_interrupted_tool_calls(self.conversation, turn_anchor)
            self.session_mgr.rewrite_messages(self.conversation.get_messages())
            self.console.print_info("Interrupted.")
            return

        except Exception as e:
            _rollback_turn_memory_changes(
                turn_memory_snapshot,
                console=self.console,
                debug=debug,
            )
            self.console.print_error(_sanitize_error_message(str(e)))
            _inject_brain_failure_record(
                self.conversation, turn_anchor, e, memory_rolled_back=True,
            )
            if self.session_mgr is not None:
                self.session_mgr.rewrite_messages(self.conversation.get_messages())
            return

        finally:
            pass

    def graceful_exit(self) -> None:
        """Handle graceful exit.

        Keeps finalize + archive only; backup and session cleanup are
        handled by the daily maintenance window.
        """
        if self.session_mgr is not None:
            self.session_mgr.finalize("completed")

        if self.agent_os_dir and self.config:
            _run_memory_archive(
                self.agent_os_dir, self.config.maintenance.archive, self.console,
            )

        self.console.print_goodbye()

    def _perform_context_refresh(self, preserve_turns: int = 2) -> None:
        """Compact conversation, reload boot files, rotate session."""
        try:
            # 1. Compact conversation
            removed = self.conversation.compact(preserve_turns)

            # 2. Re-resolve system prompt with current date
            try:
                raw_prompt = self.workspace.get_system_prompt("brain")
                raw_prompt = raw_prompt.replace(
                    "{agent_os_dir}", str(self.agent_os_dir),
                )
                self.builder.update_system_prompt(raw_prompt)
            except FileNotFoundError:
                logger.warning("Context refresh: failed to reload system prompt")

            # 3. Reload boot files from disk
            self.builder.reload_boot_files()

            # 4. Session rotation
            if self.session_mgr is not None:
                self.session_mgr.finalize("refreshed")
                self.session_mgr.create(self.user_id, self.display_name)
                self.conversation._on_message = self.session_mgr.append_message
                # Persist kept messages to new session
                for entry in self.conversation.get_messages():
                    self.session_mgr.append_message(entry)

            self.console.print_info(
                f"Context refreshed: {removed} messages compacted, "
                f"boot files reloaded, new session started."
            )
        except Exception as e:
            logger.warning("Context refresh failed: %s", e)

    def _perform_maintenance(self) -> None:
        """Run daily maintenance: archive -> context_refresh -> backup -> session_file_cleanup."""
        cfg = self.config.maintenance if self.config else None
        if cfg is None or not cfg.enabled:
            return

        logger.info("Daily maintenance started")
        try:
            # 1. Archive
            _run_memory_archive(
                self.agent_os_dir, cfg.archive, self.console,
            )

            # 2. Context refresh (compact + reload + session rotate)
            self._perform_context_refresh(
                preserve_turns=cfg.context_refresh.preserve_turns,
            )

            # 3. Backup (force=True: maintenance always backs up regardless of interval)
            if cfg.backup.enabled and self.memory_backup_mgr:
                try:
                    self.memory_backup_mgr.check_and_backup(force=True)
                except Exception as e:
                    logger.warning("Maintenance backup failed: %s", e)

            # 4. Session file cleanup
            if cfg.session_file_cleanup.enabled and self.agent_os_dir:
                try:
                    from ..session.cleanup import cleanup_sessions
                    cleanup_sessions(
                        self.agent_os_dir / "session",
                        retention_days=cfg.session_file_cleanup.retention_days,
                    )
                except Exception as e:
                    logger.warning("Maintenance session file cleanup failed: %s", e)

            # Mark scheduler so it doesn't re-trigger today
            if self._maintenance_scheduler:
                self._maintenance_scheduler.mark_done()

            self.console.print_info("Daily maintenance completed.")
        except Exception as e:
            logger.warning("Daily maintenance failed: %s", e)

    def _schedule_next_heartbeat(self, msg: InboundMessage) -> None:
        """Create the next recurring heartbeat after a successful turn."""
        from .adapters.scheduler import make_heartbeat_message, random_delay

        recur_spec = msg.metadata.get("recur_spec", "2h-5h")
        try:
            delay = random_delay(recur_spec)
        except ValueError:
            logger.warning("Invalid recur_spec %r; using default 2h-5h", recur_spec)
            delay = random_delay("2h-5h")

        next_time_raw = tz_now() + delay
        next_time = self._apply_quiet_hours(next_time_raw)
        next_msg = make_heartbeat_message(
            not_before=next_time,
            interval_spec=recur_spec,
        )
        self._queue.put(next_msg)
        delay_min = (next_time - tz_now()).total_seconds() / 60
        if delay_min >= 120:
            logger.info("Next heartbeat in %.1fh", delay_min / 60)
        else:
            logger.info("Next heartbeat in %.0fm", delay_min)

        self._maybe_schedule_pre_sleep_sync(was_deferred=next_time > next_time_raw)

    def _defer_pending_heartbeat(self) -> None:
        """Push back pending heartbeat after a non-heartbeat turn.

        Resets the heartbeat timer using the same interval spec so the
        agent does not wake up immediately after real activity.
        """
        from .adapters.scheduler import make_heartbeat_message, random_delay

        was_deferred = False
        for filepath, msg in self._queue.scan_pending(channel="system"):
            if not msg.metadata.get("system") or not msg.metadata.get("recurring"):
                continue
            # Found the pending heartbeat; remove and re-create with fresh delay
            recur_spec = msg.metadata.get("recur_spec")
            if not recur_spec:
                adapter = self.adapters.get("system")
                recur_spec = getattr(adapter, "_interval", None) or "2h-5h"
            self._queue.remove_pending(filepath)
            delay = random_delay(recur_spec)
            next_time_raw = tz_now() + delay
            next_time = self._apply_quiet_hours(next_time_raw)
            next_msg = make_heartbeat_message(
                not_before=next_time,
                interval_spec=recur_spec,
                )
            self._queue.put(next_msg)
            was_deferred = next_time > next_time_raw
            delay_min = (next_time - tz_now()).total_seconds() / 60
            if delay_min >= 120:
                logger.info("Deferred heartbeat by %.1fh", delay_min / 60)
            else:
                logger.info("Deferred heartbeat by %.0fm", delay_min)
            break  # Only one heartbeat at a time

        self._maybe_schedule_pre_sleep_sync(was_deferred=was_deferred)

    def _apply_quiet_hours(self, dt: datetime) -> datetime:
        """Push *dt* past quiet hours if it falls within a blackout window."""
        from ..core.schema import is_in_quiet_hours, next_quiet_end

        windows = self.config.heartbeat.parsed_quiet_windows()
        if not windows:
            return dt
        tz = get_tz()
        if is_in_quiet_hours(dt, windows, tz):
            end = next_quiet_end(dt, windows, tz)
            logger.info("Heartbeat deferred past quiet hours to %s", end)
            return end
        return dt

    def _maybe_schedule_pre_sleep_sync(self, *, was_deferred: bool) -> None:
        """Schedule (or replace) a pre-sleep memory sync when heartbeat was
        deferred past quiet hours.  The sync fires while the prompt cache
        is still warm (within the 1h TTL) so the side-channel call is cheap.
        """
        if self._queue is None:
            return

        # Remove any existing pre-sleep sync message first (dedup)
        for filepath, msg in self._queue.scan_pending(channel="system"):
            if msg.metadata.get("pre_sleep_sync"):
                self._queue.remove_pending(filepath)
                break

        if not was_deferred:
            return

        from .adapters.scheduler import make_pre_sleep_sync_message

        sync_time = tz_now() + timedelta(minutes=30)
        self._queue.put(make_pre_sleep_sync_message(
            not_before=sync_time,
        ))
        logger.info("Scheduled pre-sleep sync at %s", sync_time.isoformat())

    def _handle_pre_sleep_sync(self, receipt: Path | None) -> None:
        """Run memory sync side-channel only.  No brain turn."""
        if self._turns_since_memory_sync <= 0:
            logger.info("Pre-sleep sync: nothing to sync (counter=0)")
            if self._queue is not None and receipt is not None:
                self._queue.ack(receipt)
            return

        from ..memory.tool_analysis import MEMORY_SYNC_TARGETS

        sync_client = getattr(self, "memory_sync_client", None) or self.client
        tools = self.registry.get_definitions()
        try:
            _run_memory_sync_side_channel(
                sync_client,
                self.conversation,
                self.builder,
                tools,
                self.registry,
                self.console,
                missing_targets=list(MEMORY_SYNC_TARGETS),
                turns_accumulated=self._turns_since_memory_sync,
                max_retries=self.config.tools.memory_sync.max_retries,
            )
            self._turns_since_memory_sync = 0
            self.console.print_info("Pre-sleep memory sync completed")
        except Exception:
            logger.warning("Pre-sleep sync failed", exc_info=True)

        if self._queue is not None and receipt is not None:
            self._queue.ack(receipt)

    # ------------------------------------------------------------------
    # Queue-based interface
    # ------------------------------------------------------------------

    def register_adapter(self, adapter: ChannelAdapter) -> None:
        """Register a channel adapter."""
        self.adapters[adapter.channel_name] = adapter

    def enqueue(self, msg: InboundMessage | ShutdownSentinel) -> None:
        """Push a message into the persistent queue (thread-safe)."""
        if self._queue is None:
            raise RuntimeError("No queue configured; call AgentCore with queue=...")
        if isinstance(msg, InboundMessage):
            shared_state_store = getattr(self, "shared_state_store", None)
            scope_resolver = getattr(self, "scope_resolver", None)
            if shared_state_store is not None and scope_resolver is not None:
                scope_id = scope_resolver.inbound(msg)
                if scope_id:
                    msg.metadata = dict(msg.metadata)
                    msg.metadata["scope_id"] = scope_id
                    msg.metadata["anchor_shared_rev"] = shared_state_store.get_current_rev(scope_id)
        self._queue.put(msg)

    def request_shutdown(self, *, graceful: bool = True) -> None:
        """Signal the agent to shut down via the queue."""
        self.enqueue(ShutdownSentinel(graceful=graceful))

    def run(self) -> None:
        """Queue-based main loop.  Blocks until shutdown.

        Starts all registered adapters, then pulls messages from the
        persistent priority queue.  Each message is processed through
        ``run_turn`` and the response is routed back to the originating
        adapter.
        """
        if self._queue is None:
            raise RuntimeError("No queue configured; call AgentCore with queue=...")

        for adapter in self.adapters.values():
            adapter.start(self)

        # Start daily maintenance scheduler
        maint_cfg = self.config.maintenance if self.config else None
        if maint_cfg and maint_cfg.enabled:
            self._maintenance_scheduler = _MaintenanceScheduler(
                self._queue, maint_cfg,
            )
            self._maintenance_scheduler.start()

        # Start delayed message promotion thread
        self._queue.start_promotion()

        try:
            while True:
                msg, receipt = self._queue.get()
                if isinstance(msg, ShutdownSentinel):
                    if msg.graceful:
                        self.graceful_exit()
                    break
                if isinstance(msg, MaintenanceSentinel):
                    if self._queue.pending_count() == 0:
                        self._perform_maintenance()
                    continue
                self._process_inbound(msg, receipt)
        except KeyboardInterrupt:
            self.graceful_exit()
        finally:
            self._queue.stop_promotion()
            if self._maintenance_scheduler:
                self._maintenance_scheduler.stop()
            for adapter in self.adapters.values():
                adapter.stop()

    def _process_inbound(self, msg: InboundMessage, receipt: Path | None) -> None:
        """Process one inbound message through the turn pipeline."""
        # Pre-sleep sync: memory sync only, no brain turn
        if msg.metadata.get("pre_sleep_sync"):
            self._handle_pre_sleep_sync(receipt)
            return

        # Update turn context before notifying adapters so channel-specific
        # adapters (e.g. Discord typing indicators) can inspect inbound metadata.
        if self.turn_context is not None:
            self.turn_context.set_inbound(msg.channel, msg.sender, msg.metadata)

        # Notify all adapters so terminal-owning ones (CLI) can suspend
        for a in self.adapters.values():
            a.on_turn_start(msg.channel)

        self.console.print_inbound(
            msg.channel, msg.sender, msg.content, ts=msg.timestamp,
        )
        self.console.print_processing(msg.channel, msg.sender)

        # Inner thoughts callback: display on console only, never sent.
        # Actual message delivery happens via the send_message tool.
        def _thoughts(content: str | None) -> None:
            self.console.print_inner_thoughts(msg.channel, msg.sender, content)

        completed = False
        pre_turn_len = len(self.conversation.get_messages())
        try:
            self.run_turn(
                msg.content, output_fn=_thoughts,
                channel=msg.channel, sender=msg.sender,
                timestamp=msg.timestamp,
            )
            completed = True
        finally:
            had_turn_context = self.turn_context is not None
            had_send_message = False
            if self.turn_context is not None:
                had_send_message = bool(self.turn_context.sent_hashes)
                self.turn_context.clear()

            turn_messages = self.conversation.get_messages()[pre_turn_len:]
            is_heartbeat_like = bool(msg.metadata.get("system"))
            is_scheduled = (
                msg.channel == "system"
                and "scheduled_reason" in msg.metadata
            )
            is_discord_review = (
                msg.channel == "discord"
                and msg.metadata.get("source") in {
                    "guild_review",
                    "guild_mention_review",
                }
            )

            should_evict = False
            evict_reason = ""
            if completed and had_turn_context:
                if is_heartbeat_like and not had_send_message:
                    should_evict = True
                    evict_reason = "silent heartbeat/startup"
                elif is_scheduled:
                    effects = analyze_turn_effects(
                        turn_messages,
                        had_send_message=had_send_message,
                    )
                    if effects.is_scheduled_noop:
                        should_evict = True
                        evict_reason = "noop scheduled turn"
                elif is_discord_review and not had_send_message:
                    effects = analyze_turn_effects(
                        turn_messages,
                        had_send_message=had_send_message,
                    )
                    if effects.is_scheduled_noop:
                        should_evict = True
                        evict_reason = "noop discord review turn"

            if should_evict:
                evicted = len(self.conversation._messages) - pre_turn_len
                self.conversation._messages = (
                    self.conversation._messages[:pre_turn_len]
                )
                logger.debug(
                    "Evicted %s (%d messages)", evict_reason, evicted,
                )
            if self._queue is not None and completed:
                self._queue.ack(receipt)
                # Auto-schedule next heartbeat for recurring messages
                if msg.metadata.get("recurring"):
                    self._schedule_next_heartbeat(msg)
                else:
                    self._defer_pending_heartbeat()
            for a in self.adapters.values():
                a.on_turn_complete()
