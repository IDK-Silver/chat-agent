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
from ..context import ContextBuilder, Conversation
from ..core import load_config
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
from prompt_toolkit.formatted_text import HTML

from .console import ChatConsole
from .input import ChatInput
from .picker import pick_one
from .commands import CommandHandler
from ..session import SessionManager, pick_session


class _DebugConsoleHandler(logging.Handler):
    """Route log records to ChatConsole.print_debug."""

    def __init__(self, console: "ChatConsole"):
        super().__init__()
        self._console = console

    def emit(self, record: logging.LogRecord) -> None:
        self._console.print_debug("llm-retry", self.format(record))


def main(user: str, resume: str | None = None) -> None:
    """Main entry point for the CLI."""
    user_selector = user.strip()
    if not user_selector:
        raise ValueError("user is required")

    config = load_config()
    agent_os_dir = config.get_agent_os_dir()

    # Check workspace initialization
    workspace = WorkspaceManager(agent_os_dir)
    console = ChatConsole()

    if not workspace.is_initialized():
        console.print_error(f"Workspace not initialized at {agent_os_dir}")
        console.print_info("Run 'uv run python -m chat_agent init' first.")
        return

    # Auto-upgrade kernel if needed
    initializer = WorkspaceInitializer(workspace)
    if initializer.needs_upgrade():
        console.print_info("Upgrading kernel...")
        applied = initializer.upgrade_kernel()
        for v in applied:
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

    debug = config.debug
    console.set_debug(debug)
    console.set_current_user(user_id)
    console.set_show_tool_use(config.show_tool_use)
    # Bridge retry logger to debug console output.
    if debug:
        _retry_logger = logging.getLogger("chat_agent.llm.retry")
        _retry_handler = _DebugConsoleHandler(console)
        _retry_handler.setLevel(logging.DEBUG)
        _retry_logger.addHandler(_retry_handler)
        _retry_logger.setLevel(logging.DEBUG)

    agent_hint = config.features.copilot_agent_hint

    brain_agent_config = config.agents["brain"]
    client = create_client(
        brain_agent_config.llm,
        timeout_retries=brain_agent_config.llm_timeout_retries,
        request_timeout=brain_agent_config.llm_request_timeout,
        rate_limit_retries=brain_agent_config.llm_429_retries,
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
        timeout_retries=memory_editor_config.llm_timeout_retries,
        request_timeout=memory_editor_config.llm_request_timeout,
        rate_limit_retries=memory_editor_config.llm_429_retries,
        force_agent=agent_hint,
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

    timezone = workspace.get_timezone()
    console.set_timezone(timezone)

    # Session persistence
    session_mgr = SessionManager(agent_os_dir / "session" / "brain")

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
        conversation._messages = messages  # Restore without triggering callback
        console.print_info(
            f"Resumed session {resume_id} ({len(messages)} messages)"
        )
    else:
        session_mgr.create(user_id, display_name)
        conversation = Conversation(on_message=session_mgr.append_message)

    builder = ContextBuilder(
        system_prompt=system_prompt,
        timezone=timezone,
        agent_os_dir=agent_os_dir,
        boot_files=config.context.boot_files,
        boot_files_as_tool=config.context.boot_files_as_tool,
        max_chars=config.context.max_chars,
        preserve_turns=config.context.preserve_turns,
        provider=brain_agent_config.llm.provider,
    )
    builder.reload_boot_files()
    builder.estimate_chars(conversation)

    def _context_toolbar():
        chars = builder.last_total_chars
        limit = builder.max_chars
        pct = (chars / limit * 100) if limit else 0
        return HTML(
            f"<style fg='#888888'>ctx: {chars:,} / {limit:,} ({pct:.1f}%)</style>"
        )

    chat_input = ChatInput(timezone=timezone, bottom_toolbar=_context_toolbar)
    # Optional memory search agent
    memory_search_agent = None
    if "memory_searcher" in config.agents and config.agents["memory_searcher"].enabled:
        ms_config = config.agents["memory_searcher"]
        ms_client = create_client(
            ms_config.llm,
            timeout_retries=ms_config.llm_timeout_retries,
            request_timeout=ms_config.llm_request_timeout,
            rate_limit_retries=ms_config.llm_429_retries,
            force_agent=agent_hint,
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
    brain_has_vision = bool(
        brain_agent_config.llm.capabilities
        and brain_agent_config.llm.capabilities.vision
    )
    _use_own_vision = brain_agent_config.use_own_vision_ability
    vision_agent_instance: VisionAgent | None = None
    if (not brain_has_vision or not _use_own_vision) and "vision" in config.agents and config.agents["vision"].enabled:
        vision_config = config.agents["vision"]
        vision_client = create_client(
            vision_config.llm,
            timeout_retries=vision_config.llm_timeout_retries,
            request_timeout=vision_config.llm_request_timeout,
            rate_limit_retries=vision_config.llm_429_retries,
            force_agent=agent_hint,
        )
        try:
            vision_prompt = workspace.get_system_prompt("vision")
            vision_agent_instance = VisionAgent(vision_client, vision_prompt)
        except FileNotFoundError:
            pass

    # GUI automation agent initialization
    gui_manager_instance: GUIManager | None = None
    if "gui_manager" in config.agents and config.agents["gui_manager"].enabled:
        gm_config = config.agents["gui_manager"]
        gm_client = create_client(
            gm_config.llm,
            timeout_retries=gm_config.llm_timeout_retries,
            request_timeout=gm_config.llm_request_timeout,
            rate_limit_retries=gm_config.llm_429_retries,
            force_agent=agent_hint,
        )
        gw_config = config.agents.get("gui_worker")
        if gw_config and gw_config.enabled:
            gw_client = create_client(
                gw_config.llm,
                timeout_retries=gw_config.llm_timeout_retries,
                request_timeout=gw_config.llm_request_timeout,
                rate_limit_retries=gw_config.llm_429_retries,
                force_agent=agent_hint,
            )
            try:
                gm_prompt = workspace.get_system_prompt("gui_manager")
                gw_prompt = workspace.get_system_prompt("gui_worker")
                gw_layout_prompt = workspace.get_agent_prompt("gui_worker", "layout")
                worker = GUIWorker(
                    gw_client, gw_prompt,
                    screenshot_max_width=gm_config.screenshot_max_width,
                    screenshot_quality=gm_config.screenshot_quality,
                    layout_prompt=gw_layout_prompt,
                )
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

    # === Gmail adapter (optional, requires OAuth credentials in .env) ===
    # Created before setup_tools so attachments_dir can be added to allowed_paths.
    gmail_adapter = None
    _gmail_cfg = config.channels.gmail
    if _gmail_cfg.enabled:
        _env = dotenv_values()
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

    extra_allowed_paths: list[str] = []
    if gmail_adapter is not None:
        extra_allowed_paths.append(gmail_adapter.attachments_dir)

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
        gui_lock=gui_lock,
        screenshot_max_width=_ss_max_width,
        screenshot_quality=_ss_quality,
        contact_map=contact_map,
        extra_allowed_paths=extra_allowed_paths,
    )
    memory_edit_allow_failure = config.tools.memory_edit.allow_failure
    commands = CommandHandler(console)

    if resume is not None:
        console.print_resume_history(
            conversation.get_messages(),
            replay_turns=config.session.replay_turns,
            show_tool_calls=config.session.show_tool_calls,
        )
        # Warm up builder so ctx counter in toolbar is accurate.
        builder.build(conversation)

    # Periodic memory backup
    memory_backup_mgr = None
    if config.hooks.memory_backup.enabled:
        memory_backup_mgr = MemoryBackupManager(agent_os_dir, config.hooks.memory_backup)

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
        console=console,
        workspace=workspace,
        config=config,
        agent_os_dir=agent_os_dir,
        user_id=user_id,
        session_mgr=session_mgr,
        display_name=display_name,
        memory_edit_allow_failure=memory_edit_allow_failure,
        memory_backup_mgr=memory_backup_mgr,
        queue=pqueue,
        context_refresh_config=config.hooks.context_refresh,
    )

    # === CLI adapter ===
    cli_adapter = CLIAdapter(
        chat_input=chat_input,
        console=console,
        commands=commands,
        session_mgr=session_mgr,
        conversation=conversation,
        builder=builder,
        workspace=workspace,
        agent_os_dir=agent_os_dir,
        user_id=user_id,
        display_name=display_name,
        picker_fn=pick_one,
    )
    agent.register_adapter(cli_adapter)

    if gmail_adapter is not None:
        agent.register_adapter(gmail_adapter)
        if debug:
            console.print_debug("gmail", "Gmail adapter registered")

    # === LINE crack adapter (optional, macOS only) ===
    _lc_cfg = config.channels.line_crack
    if _lc_cfg.enabled and sys.platform == "darwin":
        _lc_vision_cfg = config.agents.get("vision")
        if _lc_vision_cfg and _lc_vision_cfg.enabled:
            _lc_vision_client = create_client(
                _lc_vision_cfg.llm,
                timeout_retries=_lc_vision_cfg.llm_timeout_retries,
                request_timeout=_lc_vision_cfg.llm_request_timeout,
                rate_limit_retries=_lc_vision_cfg.llm_429_retries,
                force_agent=agent_hint,
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

        scheduler_adapter = SchedulerAdapter(interval=config.heartbeat.interval)
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
        ),
        SEND_MESSAGE_DEFINITION,
    )
    agent.turn_context = turn_context

    # === schedule_action tool (registered after queue is available) ===
    if config.heartbeat.enabled:
        from ..tools.builtin.schedule_action import (
            SCHEDULE_ACTION_DEFINITION,
            create_schedule_action,
        )

        _tz_name = workspace.get_timezone()
        registry.register(
            "schedule_action",
            create_schedule_action(pqueue, timezone_name=_tz_name),
            SCHEDULE_ACTION_DEFINITION,
        )

    # Control API (optional, for supervisor integration)
    if config.control.enabled:
        from ..control import ControlServer

        control_server = ControlServer(
            host=config.control.host,
            port=config.control.port,
            shutdown_fn=agent.request_shutdown,
        )
        control_server.start()

    if resume is None:
        console.print_welcome()

    agent.run()
