import logging
import os
import sys
import threading

from dotenv import dotenv_values

from ..agent import AgentCore, setup_tools
from ..agent.adapters.cli import CLIAdapter
from ..agent.contact_map import ContactMap
from ..agent.thread_registry import ThreadRegistry
from ..agent.queue import PersistentPriorityQueue
from ..agent.scope import DEFAULT_SCOPE_RESOLVER
from ..agent.shared_state import load_or_init as load_shared_state_cache
from ..agent.shared_state_replay import rebuild_shared_state_from_sessions
from ..context import ContextBuilder, Conversation
from ..core import load_config
from ..core.schema import CopilotConfig
from ..llm import create_client
from ..memory import (
    MemoryEditor,
    MemoryEditPlanner,
    SessionCommitLog,
    MemorySearchAgent,
)
from ..memory.backup import MemoryBackupManager
from ..workspace import WorkspaceManager, WorkspaceInitializer
from ..workspace.people import ensure_user_memory_file, resolve_user_selector
from ..tools import VisionAgent
from ..gui import (
    GUIManager,
    GUISessionStore,
    GUIWorker,
)

from .commands import CommandHandler
from ..session import SessionManager, pick_session
from ..tui import (
    ChatTextualApp,
    QueueUiSink,
    TextualController,
    TextualUiConsole,
    TurnCancelController,
)


class _RetryUiHandler(logging.Handler):
    """Route LLM retry logs to visible UI warnings."""

    def __init__(self, console):
        super().__init__()
        self._console = console

    def emit(self, record: logging.LogRecord) -> None:
        self._console.print_warning(f"LLM retry: {self.format(record)}", indent=2)


def _install_llm_retry_ui_handler(console) -> None:
    """Install one visible handler for chat_agent.llm.retry logs."""
    retry_logger = logging.getLogger("chat_agent.llm.retry")
    for handler in list(retry_logger.handlers):
        if isinstance(handler, _RetryUiHandler):
            retry_logger.removeHandler(handler)
    if not callable(getattr(console, "print_warning", None)):
        # Some test doubles / minimal consoles do not implement warning output.
        # Skip installing the UI retry handler instead of crashing on log emit.
        return
    retry_handler = _RetryUiHandler(console)
    retry_handler.setLevel(logging.DEBUG)
    retry_logger.addHandler(retry_handler)
    retry_logger.setLevel(logging.DEBUG)


def main(user: str, resume: str | None = None) -> None:
    """Main entry point for the CLI."""
    user_selector = user.strip()
    if not user_selector:
        raise ValueError("user is required")

    config = load_config()
    agent_os_dir = config.get_agent_os_dir()

    # Must be first: everything downstream may call tz_now()
    from ..timezone_utils import configure as configure_tz
    configure_tz(config.app.timezone)

    ui_sink = QueueUiSink()
    cancel_controller = TurnCancelController(ui_sink=ui_sink)
    controller = TextualController(ui_sink=ui_sink, cancel=cancel_controller)

    # Check workspace initialization
    workspace = WorkspaceManager(agent_os_dir)
    console = TextualUiConsole(ui_sink)

    if not workspace.is_initialized():
        console.print_error(f"Workspace not initialized at {agent_os_dir}")
        console.print_info("Run 'uv run python -m chat_agent init' first.")
        return

    # Auto-upgrade kernel if needed
    initializer = WorkspaceInitializer(workspace)
    migration_result = None
    if initializer.needs_upgrade():
        console.print_info("Upgrading kernel...")
        migration_result = initializer.upgrade_kernel()
        for v in migration_result.applied_versions:
            console.print_info(f"  Applied: v{v}")
        console.print_info("Kernel upgraded.")

    try:
        user_id, display_name = resolve_user_selector(workspace.memory_dir, user_selector)
        ensure_user_memory_file(workspace.memory_dir, user_id, display_name)
    except ValueError as e:
        console.print_error(str(e))
        return

    # Load bootloader prompt and resolve {agent_os_dir} placeholder
    try:
        system_prompt = workspace.get_system_prompt("brain")
        system_prompt = system_prompt.replace("{agent_os_dir}", str(agent_os_dir))
    except FileNotFoundError as e:
        console.print_error(f"Failed to load system prompt: {e}")
        return

    debug = config.tui.debug
    console.set_debug(debug)
    console.set_current_user(user_id)
    console.set_show_tool_use(config.tui.show_tool_use)
    # Surface LLM retry attempts in normal UI (not only debug mode).
    _install_llm_retry_ui_handler(console)

    agent_hint = config.features.copilot_agent_hint

    def _provider_kwargs(llm_config):
        """Build provider-specific kwargs for create_client.

        force_agent is a Copilot-only runtime hint for billing optimization.
        Only passed when agent_hint is enabled AND config is CopilotConfig.
        """
        if agent_hint and isinstance(llm_config, CopilotConfig):
            return {"force_agent": True}
        return {}

    brain_agent_config = config.agents["brain"]
    client = create_client(
        brain_agent_config.llm,
        transient_retries=brain_agent_config.llm_transient_retries,
        request_timeout=brain_agent_config.llm_request_timeout,
        rate_limit_retries=brain_agent_config.llm_rate_limit_retries,
        retry_label="brain",
    )
    memory_sync_client = None
    if getattr(brain_agent_config.llm, "provider", "") == "openrouter":
        memory_sync_client = create_client(
            brain_agent_config.llm,
            transient_retries=brain_agent_config.llm_transient_retries,
            request_timeout=brain_agent_config.llm_request_timeout,
            rate_limit_retries=brain_agent_config.llm_rate_limit_retries,
            retry_label="memory_sync",
        )

    if "memory_editor" not in config.agents:
        console.print_error("Missing required agent config: agents.memory_editor")
        return

    memory_editor_config = config.agents["memory_editor"]
    if not memory_editor_config.enabled:
        console.print_error("agents.memory_editor must be enabled.")
        return

    memory_editor_client = create_client(
        memory_editor_config.llm,
        transient_retries=memory_editor_config.llm_transient_retries,
        request_timeout=memory_editor_config.llm_request_timeout,
        rate_limit_retries=memory_editor_config.llm_rate_limit_retries,
        **_provider_kwargs(memory_editor_config.llm),
        retry_label="memory_editor",
    )

    try:
        memory_editor_prompt = workspace.get_system_prompt("memory_editor")
    except FileNotFoundError as e:
        console.print_error(f"Failed to load memory_editor prompt: {e}")
        return

    memory_editor_parse_retry: str | None = None
    try:
        memory_editor_parse_retry = workspace.get_agent_prompt(
            "memory_editor",
            "parse-retry",
            current_user=user_id,
        )
    except FileNotFoundError:
        pass

    memory_planner = MemoryEditPlanner(
        memory_editor_client,
        memory_editor_prompt,
        parse_retries=memory_editor_config.post_parse_retries,
        parse_retry_prompt=memory_editor_parse_retry,
    )
    memory_editor = MemoryEditor(
        commit_log=SessionCommitLog(),
        planner=memory_planner,
        warnings_config=config.tools.memory_edit.warnings,
    )

    timezone = config.app.timezone
    console.set_timezone(timezone)

    # Session persistence
    session_mgr = SessionManager(agent_os_dir / "session" / "brain")

    shared_state_store = None
    if config.context.common_ground.enabled:
        cache_path = agent_os_dir / "memory" / "cache" / "shared_state.json"
        load_result = load_shared_state_cache(cache_path)
        shared_state_store = load_result.store
        shared_state_store.persist_enabled = config.context.common_ground.persist_cache
        if not load_result.loaded:
            stats = rebuild_shared_state_from_sessions(
                agent_os_dir / "session" / "brain",
                store=shared_state_store,
                scope_resolver=DEFAULT_SCOPE_RESOLVER,
            )
            try:
                shared_state_store.save()
            except Exception as e:
                console.print_warning(f"shared_state cache save failed: {e}")
            if debug:
                console.print_debug(
                    "common-ground",
                    "replay rebuild "
                    f"sessions={stats.sessions_scanned} "
                    f"entries={stats.entries_scanned} "
                    f"sends={stats.send_message_successes_replayed}",
                )

    resume_id: str | None = None
    if resume is not None:
        # Resume flow
        if resume == "__continue__":
            sessions = session_mgr.list_recent(user_id=user_id, limit=1)
            if sessions:
                resume_id = sessions[0].session_id
        elif resume == "":
            sessions = session_mgr.list_recent(user_id=user_id)
            selected = pick_session(sessions)
            if not selected:
                return
            resume_id = selected.session_id
        else:
            resume_id = resume

    if resume_id is not None:
        messages = session_mgr.load(resume_id)
        conversation = Conversation(on_message=session_mgr.append_message)
        conversation.replace_messages(messages)
        console.print_info(
            f"Resumed session {resume_id} ({len(messages)} messages)"
        )
    else:
        session_mgr.create(user_id, display_name)
        conversation = Conversation(on_message=session_mgr.append_message)

    # Only enable prompt caching for providers that support cache_control
    # in OpenAI content-parts format. OpenRouter passes it through to Anthropic.
    # Native Anthropic adapter uses a separate system field (str), not content parts.
    _CACHE_PROVIDERS = {"openrouter"}
    brain_cache = brain_agent_config.cache
    cache_ttl = (
        brain_cache.ttl
        if brain_cache.enabled and brain_agent_config.llm.provider in _CACHE_PROVIDERS
        else None
    )
    builder = ContextBuilder(
        system_prompt=system_prompt,
        agent_os_dir=agent_os_dir,
        boot_files=config.context.boot_files,
        boot_files_as_tool=config.context.boot_files_as_tool,
        preserve_turns=config.context.preserve_turns,
        provider=brain_agent_config.llm.provider,
        cache_ttl=cache_ttl,
        format_reminders=config.features.format_reminders.model_dump(),
    )
    builder.reload_boot_files()
    # Optional memory search agent
    memory_search_agent = None
    if "memory_searcher" in config.agents and config.agents["memory_searcher"].enabled:
        ms_config = config.agents["memory_searcher"]
        ms_client = create_client(
            ms_config.llm,
            transient_retries=ms_config.llm_transient_retries,
            request_timeout=ms_config.llm_request_timeout,
            rate_limit_retries=ms_config.llm_rate_limit_retries,
            **_provider_kwargs(ms_config.llm),
            retry_label="memory_searcher",
        )
        try:
            ms_prompt = workspace.get_system_prompt("memory_searcher")
            ms_parse_retry: str | None = None
            try:
                ms_parse_retry = workspace.get_agent_prompt(
                    "memory_searcher", "parse-retry", current_user=user_id,
                )
            except FileNotFoundError:
                pass
            memory_search_agent = MemorySearchAgent(
                ms_client,
                ms_prompt,
                memory_dir=agent_os_dir / "memory",
                parse_retries=ms_config.pre_parse_retries,
                parse_retry_prompt=ms_parse_retry,
                context_bytes_limit=ms_config.context_bytes_limit,
                max_results=ms_config.max_results,
            )
        except FileNotFoundError:
            pass

    # BM25 search (default when memory_searcher agent is disabled)
    bm25_search_instance = None
    if memory_search_agent is None:
        from ..memory.bm25_search import BM25MemorySearch
        bm25_search_instance = BM25MemorySearch(
            memory_dir=agent_os_dir / "memory",
            config=config.tools.memory_search.bm25,
        )

    # Vision agent initialization
    brain_has_vision = brain_agent_config.llm.get_vision()
    _use_own_vision = brain_agent_config.use_own_vision_ability
    vision_agent_instance: VisionAgent | None = None
    if (not brain_has_vision or not _use_own_vision) and "vision" in config.agents and config.agents["vision"].enabled:
        vision_config = config.agents["vision"]
        vision_client = create_client(
            vision_config.llm,
            transient_retries=vision_config.llm_transient_retries,
            request_timeout=vision_config.llm_request_timeout,
            rate_limit_retries=vision_config.llm_rate_limit_retries,
            **_provider_kwargs(vision_config.llm),
            retry_label="vision",
        )
        try:
            vision_prompt = workspace.get_system_prompt("vision")
            vision_agent_instance = VisionAgent(vision_client, vision_prompt)
        except FileNotFoundError:
            pass

    # GUI automation agent initialization
    gui_manager_instance: GUIManager | None = None
    gui_worker_instance: GUIWorker | None = None
    if "gui_manager" in config.agents and config.agents["gui_manager"].enabled:
        gm_config = config.agents["gui_manager"]
        gm_client = create_client(
            gm_config.llm,
            transient_retries=gm_config.llm_transient_retries,
            request_timeout=gm_config.llm_request_timeout,
            rate_limit_retries=gm_config.llm_rate_limit_retries,
            **_provider_kwargs(gm_config.llm),
            retry_label="gui_manager",
        )
        gw_config = config.agents.get("gui_worker")
        if gw_config and gw_config.enabled:
            gw_client = create_client(
                gw_config.llm,
                transient_retries=gw_config.llm_transient_retries,
                request_timeout=gw_config.llm_request_timeout,
                rate_limit_retries=gw_config.llm_rate_limit_retries,
                **_provider_kwargs(gw_config.llm),
                retry_label="gui_worker",
            )
            try:
                gm_prompt = workspace.get_system_prompt("gui_manager")
                gw_prompt = workspace.get_system_prompt("gui_worker")
                gw_layout_prompt = workspace.get_agent_prompt("gui_worker", "layout")
                gw_describe_prompt = ""
                try:
                    gw_describe_prompt = workspace.get_agent_prompt("gui_worker", "describe")
                except FileNotFoundError:
                    pass
                worker = GUIWorker(
                    gw_client, gw_prompt,
                    screenshot_max_width=gm_config.screenshot_max_width,
                    screenshot_quality=gm_config.screenshot_quality,
                    layout_prompt=gw_layout_prompt,
                    describe_prompt=gw_describe_prompt,
                )
                gui_worker_instance = worker
                gui_session_store = GUISessionStore(agent_os_dir / "session" / "gui")

                def _gui_step_callback(
                    tool_call, result, step, max_steps,
                    elapsed_sec, total_elapsed_sec, worker_timing,
                ):
                    console.print_gui_step(
                        tool_call, result, step, max_steps,
                        elapsed_sec, total_elapsed_sec,
                        worker_timing=worker_timing,
                        instruction_max_chars=gm_config.gui_instruction_max_chars,
                        text_max_chars=gm_config.gui_text_max_chars,
                        worker_result_max_chars=gm_config.gui_worker_result_max_chars,
                        result_max_chars=gm_config.gui_result_max_chars,
                    )

                console.gui_intent_max_chars = gm_config.gui_intent_max_chars
                gui_manager_instance = GUIManager(
                    gm_client, worker, gm_prompt,
                    max_steps=gm_config.max_steps,
                    session_store=gui_session_store,
                    on_step=_gui_step_callback,
                    screenshot_max_width=gm_config.screenshot_max_width,
                    screenshot_quality=gm_config.screenshot_quality,
                    scroll_invert=config.tools.scroll.invert,
                    scroll_max_amount=config.tools.scroll.max_amount,
                    is_cancel_requested=cancel_controller.is_requested,
                    allow_direct_screenshot=gm_config.allow_direct_screenshot,
                    allow_wait_tool=gm_config.allow_wait_tool,
                    step_delay_min=gm_config.step_delay_min,
                    step_delay_max=gm_config.step_delay_max,
                )
            except FileNotFoundError:
                pass

    # Screenshot settings (from gui_manager config if available)
    _gm_cfg = config.agents.get("gui_manager")
    _ss_max_width = _gm_cfg.screenshot_max_width if _gm_cfg else 1280
    _ss_quality = _gm_cfg.screenshot_quality if _gm_cfg else 80

    gui_lock = threading.Lock() if gui_manager_instance is not None else None
    contact_map = ContactMap(agent_os_dir / "memory" / "cache")
    thread_registry = ThreadRegistry(agent_os_dir / "memory" / "cache")
    _env = dotenv_values()

    # === Gmail adapter (optional, requires OAuth credentials in .env) ===
    # Created before setup_tools so attachments_dir can be added to allowed_paths.
    gmail_adapter = None
    _gmail_cfg = config.channels.gmail
    if _gmail_cfg.enabled:
        _gmail_cid = _env.get("GMAIL_CLIENT_ID") or os.environ.get("GMAIL_CLIENT_ID")
        _gmail_sec = _env.get("GMAIL_CLIENT_SECRET") or os.environ.get("GMAIL_CLIENT_SECRET")
        _gmail_tok = _env.get("GMAIL_REFRESH_TOKEN") or os.environ.get("GMAIL_REFRESH_TOKEN")
        if _gmail_cid and _gmail_sec and _gmail_tok:
            from ..agent.adapters.gmail import GmailAdapter

            gmail_adapter = GmailAdapter(
                client_id=_gmail_cid,
                client_secret=_gmail_sec,
                refresh_token=_gmail_tok,
                contact_map=contact_map,
                thread_registry=thread_registry,
                thread_max_age_days=_gmail_cfg.thread_max_age_days,
                poll_interval=_gmail_cfg.poll_interval,
                max_age_minutes=_gmail_cfg.max_age_minutes,
                ignore_senders=_gmail_cfg.ignore_senders,
            )

    # === Discord adapter (optional, requires token) ===
    discord_adapter = None
    discord_history_store = None
    _discord_cfg = config.channels.discord
    if _discord_cfg.enabled:
        _discord_token = _env.get("DISCORD_TOKEN") or os.environ.get("DISCORD_TOKEN")
        if _discord_token:
            from ..agent.adapters.discord import DiscordAdapter
            from ..agent.discord_history import DiscordHistoryStore

            discord_history_store = DiscordHistoryStore(agent_os_dir / "memory" / "cache")
            discord_adapter = DiscordAdapter(
                token=_discord_token,
                contact_map=contact_map,
                thread_registry=thread_registry,
                config=_discord_cfg,
                history_store=discord_history_store,
            )

    extra_allowed_paths: list[str] = []
    if gmail_adapter is not None:
        extra_allowed_paths.append(gmail_adapter.attachments_dir)
    if discord_adapter is not None:
        extra_allowed_paths.extend(discord_adapter.history_store.allowed_paths)

    _on_shell_line = console.print_shell_stream_line
    registry, all_allowed_paths = setup_tools(
        config.tools,
        agent_os_dir,
        memory_editor=memory_editor,
        memory_search_agent=memory_search_agent,
        bm25_search=bm25_search_instance,
        brain_has_vision=brain_has_vision,
        use_own_vision_ability=_use_own_vision,
        vision_agent=vision_agent_instance,
        gui_manager=gui_manager_instance,
        gui_worker=gui_worker_instance,
        gui_lock=gui_lock,
        screenshot_max_width=_ss_max_width,
        screenshot_quality=_ss_quality,
        contact_map=contact_map,
        extra_allowed_paths=extra_allowed_paths,
        on_shell_stdout_line=_on_shell_line,
        is_shell_cancel_requested=cancel_controller.is_requested,
    )
    memory_edit_allow_failure = config.tools.memory_edit.allow_failure
    commands = CommandHandler(console)

    if resume is not None:
        console.print_resume_history(
            conversation.get_messages(),
            replay_turns=config.tui.replay_turns,
            show_tool_calls=config.tui.show_tool_calls,
        )

    # Periodic memory backup
    memory_backup_mgr = None
    if config.maintenance.backup.enabled:
        memory_backup_mgr = MemoryBackupManager(agent_os_dir, config.maintenance.backup)

    # === Persistent queue ===
    pqueue = PersistentPriorityQueue(
        agent_os_dir / "queue",
        discard_channels={"cli"},
    )

    # === Build AgentCore ===
    agent = AgentCore(
        client=client,
        conversation=conversation,
        builder=builder,
        registry=registry,
        ui_sink=ui_sink,
        workspace=workspace,
        config=config,
        agent_os_dir=agent_os_dir,
        user_id=user_id,
        session_mgr=session_mgr,
        display_name=display_name,
        memory_edit_allow_failure=memory_edit_allow_failure,
        memory_backup_mgr=memory_backup_mgr,
        queue=pqueue,
        turn_cancel=cancel_controller,
        shared_state_store=shared_state_store,
        scope_resolver=DEFAULT_SCOPE_RESOLVER,
        memory_sync_client=memory_sync_client,
        ui_debug=debug,
        ui_show_tool_use=config.tui.show_tool_use,
        ui_timezone=timezone,
        ui_gui_intent_max_chars=getattr(console, "gui_intent_max_chars", None),
    )

    def _token_status_text() -> str:
        return agent.get_token_status_text()

    if hasattr(console, "set_ctx_status_provider"):
        console.set_ctx_status_provider(_token_status_text)
    controller.ctx_provider = _token_status_text

    # === CLI adapter ===
    cli_adapter = CLIAdapter(
        ui_sink=ui_sink,
        commands=commands,
        session_mgr=session_mgr,
        conversation=conversation,
        builder=builder,
        workspace=workspace,
        agent_os_dir=agent_os_dir,
        user_id=user_id,
        display_name=display_name,
        cancel_controller=cancel_controller,
    )
    agent.register_adapter(cli_adapter)

    if gmail_adapter is not None:
        agent.register_adapter(gmail_adapter)
        if debug:
            console.print_debug("gmail", "Gmail adapter registered")

    if discord_adapter is not None:
        agent.register_adapter(discord_adapter)
        if debug:
            console.print_debug("discord", "Discord adapter registered")

    # === LINE crack adapter (optional, macOS only) ===
    _lc_cfg = config.channels.line_crack
    if _lc_cfg.enabled and sys.platform == "darwin":
        _lc_vision_cfg = config.agents.get("vision")
        if _lc_vision_cfg and _lc_vision_cfg.enabled:
            _lc_vision_client = create_client(
                _lc_vision_cfg.llm,
                transient_retries=_lc_vision_cfg.llm_transient_retries,
                request_timeout=_lc_vision_cfg.llm_request_timeout,
                rate_limit_retries=_lc_vision_cfg.llm_rate_limit_retries,
                **_provider_kwargs(_lc_vision_cfg.llm),
                retry_label="line_crack_vision",
            )
            _lc_lock = gui_lock or threading.Lock()
            from ..agent.adapters.line_crack import LineCrackAdapter

            _lc_adapter = LineCrackAdapter(
                gui_lock=_lc_lock,
                vision_client=_lc_vision_client,
                contact_map=contact_map,
                poll_interval=_lc_cfg.poll_interval,
                screenshot_max_width=_lc_cfg.screenshot_max_width,
                screenshot_quality=_lc_cfg.screenshot_quality,
                scroll_similarity_threshold=_lc_cfg.scroll_similarity_threshold,
                max_scroll_captures=_lc_cfg.max_scroll_captures,
            )
            agent.register_adapter(_lc_adapter)
            if debug:
                console.print_debug("line", "LINE crack adapter registered")

    # === Scheduler adapter (heartbeat, optional) ===
    if config.heartbeat.enabled:
        from ..agent.adapters.scheduler import SchedulerAdapter

        upgrade_msg = migration_result.format_startup_message() if migration_result else ""
        scheduler_adapter = SchedulerAdapter(
            interval=config.heartbeat.interval,
            enqueue_startup=config.heartbeat.enqueue_startup,
            upgrade_message=upgrade_msg,
            quiet_windows=config.heartbeat.parsed_quiet_windows(),
        )
        agent.register_adapter(scheduler_adapter)
        if debug:
            console.print_debug("scheduler", "Scheduler adapter registered")

    # === send_message tool (registered after adapters are available) ===
    from ..agent.turn_context import TurnContext
    from ..tools.builtin.send_message import (
        SEND_MESSAGE_DEFINITION,
        create_send_message,
    )

    turn_context = TurnContext()
    registry.register(
        "send_message",
        create_send_message(
            adapters=agent.adapters,
            turn_context=turn_context,
            contact_map=contact_map,
            allowed_paths=all_allowed_paths,
            agent_os_dir=agent_os_dir,
            shared_state_store=shared_state_store,
            scope_resolver=DEFAULT_SCOPE_RESOLVER,
        ),
        SEND_MESSAGE_DEFINITION,
    )
    agent.turn_context = turn_context

    if discord_history_store is not None:
        from ..tools.builtin.get_channel_history import (
            GET_CHANNEL_HISTORY_DEFINITION,
            create_get_channel_history,
        )

        registry.register(
            "get_channel_history",
            create_get_channel_history(
                discord_history_store,
                contact_map,
                turn_context,
            ),
            GET_CHANNEL_HISTORY_DEFINITION,
        )

    # === gui_task tool (registered after queue for background execution) ===
    if gui_manager_instance is not None:
        from ..gui.tool_adapter import GUI_TASK_DEFINITION, create_gui_task

        registry.register(
            "gui_task",
            create_gui_task(
                gui_manager_instance,
                gui_lock=gui_lock,
                agent_os_dir=agent_os_dir,
                queue=pqueue,
            ),
            GUI_TASK_DEFINITION,
        )

    # === schedule_action tool (always available when queue exists) ===
    from ..tools.builtin.schedule_action import (
        SCHEDULE_ACTION_DEFINITION,
        create_schedule_action,
    )

    registry.register(
        "schedule_action",
        create_schedule_action(pqueue),
        SCHEDULE_ACTION_DEFINITION,
    )

    app = ChatTextualApp(controller=controller, event_sink=ui_sink)

    # Control API (optional, for supervisor integration)
    if config.app.control.enabled:
        from ..control import ControlServer

        def _shutdown_from_control() -> None:
            # /shutdown must terminate the full chat-cli process, not just the agent thread.
            agent.request_shutdown()
            try:
                app.call_from_thread(app.exit)
            except RuntimeError:
                # If Textual isn't running yet, the queued shutdown sentinel still stops AgentCore.
                pass

        control_server = ControlServer(
            host=config.app.control.host,
            port=config.app.control.port,
            shutdown_fn=_shutdown_from_control,
        )
        control_server.start()

    if resume is None:
        console.print_welcome()

    controller.on_submit = cli_adapter.submit_input
    controller.on_history_request = cli_adapter.select_recent_input
    controller.on_history_options = cli_adapter.list_recent_inputs
    controller.on_history_select = cli_adapter.select_recent_input_by_index
    controller.on_exit_request = lambda: agent.request_shutdown(graceful=False)

    ui_sink.set_on_emit(app.wake_ui_event_drain)
    controller.refresh_ctx_status()
    app.drain_ui_events()

    agent_thread = threading.Thread(target=agent.run, name="agent-core", daemon=True)
    agent_thread.start()
    try:
        app.run()
    finally:
        if agent_thread.is_alive():
            agent.request_shutdown(graceful=False)
            agent_thread.join(timeout=5)
