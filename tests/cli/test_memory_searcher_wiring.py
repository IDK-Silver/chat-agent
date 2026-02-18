"""Tests for memory_searcher config wiring in CLI app."""

from pathlib import Path

from chat_agent.core.schema import AppConfig


def _make_app_config(agent_os_dir: Path) -> AppConfig:
    return AppConfig.model_validate({
        "agent_os_dir": str(agent_os_dir),
        "debug": False,
        "show_tool_use": False,
        "warn_on_failure": False,
        "tools": {
            "allowed_paths": [],
            "shell": {"blacklist": [], "timeout": 30},
        },
        "agents": {
            "brain": {
                "enabled": True,
                "llm": {"provider": "openrouter", "model": "dummy"},
            },
            "memory_editor": {
                "enabled": True,
                "llm": {"provider": "openrouter", "model": "dummy"},
                "post_parse_retries": 0,
            },
            "memory_searcher": {
                "enabled": True,
                "llm": {"provider": "openrouter", "model": "dummy"},
                "pre_parse_retries": 1,
                "context_bytes_limit": 1234,
                "max_results": 7,
            },
            "post_reviewer": {
                "enabled": False,
                "llm": {"provider": "openrouter", "model": "dummy"},
            },
            "shutdown_reviewer": {
                "enabled": False,
                "llm": {"provider": "openrouter", "model": "dummy"},
            },
            "progress_reviewer": {
                "enabled": False,
                "llm": {"provider": "openrouter", "model": "dummy"},
            },
        },
    })


def _make_app_config_with_post_reviewer(
    agent_os_dir: Path,
    *,
    post_reviewer_enabled: bool,
    progress_reviewer_enabled: bool = False,
    warn_on_failure: bool = False,
) -> AppConfig:
    return AppConfig.model_validate({
        "agent_os_dir": str(agent_os_dir),
        "debug": False,
        "show_tool_use": False,
        "warn_on_failure": warn_on_failure,
        "tools": {
            "allowed_paths": [],
            "shell": {"blacklist": [], "timeout": 30},
        },
        "agents": {
            "brain": {
                "enabled": True,
                "llm": {"provider": "openrouter", "model": "dummy"},
            },
            "memory_editor": {
                "enabled": True,
                "llm": {"provider": "openrouter", "model": "dummy"},
                "post_parse_retries": 0,
            },
            "post_reviewer": {
                "enabled": post_reviewer_enabled,
                "llm": {"provider": "openrouter", "model": "dummy"},
                "post_parse_retries": 0,
            },
            "progress_reviewer": {
                "enabled": progress_reviewer_enabled,
                "llm": {"provider": "openrouter", "model": "dummy"},
                "post_parse_retries": 0,
            },
            "shutdown_reviewer": {
                "enabled": False,
                "llm": {"provider": "openrouter", "model": "dummy"},
            },
        },
    })


def test_main_wires_memory_searcher_limits(monkeypatch, tmp_path: Path):
    from chat_agent.cli import app as app_module

    captured: dict[str, object] = {}

    class _DummyMemorySearchAgent:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)

    class _DummyWorkspace:
        def __init__(self, agent_os_dir: Path):
            self.agent_os_dir = agent_os_dir
            self.memory_dir = agent_os_dir / "memory"

        def is_initialized(self) -> bool:
            return True

        def get_system_prompt(self, _agent: str) -> str:
            return "prompt"

        def get_agent_prompt(self, *args, **kwargs) -> str:
            return "parse-retry"

        def get_timezone(self) -> str:
            return "Asia/Taipei"

    class _DummyInitializer:
        def __init__(self, workspace):
            self.workspace = workspace

        def needs_upgrade(self) -> bool:
            return False

        def upgrade_kernel(self):
            return []

    class _DummyInput:
        def __init__(self, timezone: str, bottom_toolbar=None):
            self.timezone = timezone

        def get_input(self):
            return None

    class _DummyConsole:
        def set_debug(self, debug: bool) -> None:
            self.debug = debug

        def set_show_tool_use(self, show: bool) -> None:
            self.show_tool_use = show

        def print_welcome(self) -> None:
            pass

        def print_goodbye(self) -> None:
            pass

        def print_error(self, _message: str) -> None:
            pass

        def print_info(self, _message: str) -> None:
            pass

    monkeypatch.setattr(app_module, "load_config", lambda: _make_app_config(tmp_path))
    monkeypatch.setattr(app_module, "WorkspaceManager", _DummyWorkspace)
    monkeypatch.setattr(app_module, "WorkspaceInitializer", _DummyInitializer)
    monkeypatch.setattr(app_module, "ChatInput", _DummyInput)
    monkeypatch.setattr(app_module, "ChatConsole", _DummyConsole)
    monkeypatch.setattr(
        app_module,
        "create_client",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(app_module, "MemorySearchAgent", _DummyMemorySearchAgent)
    monkeypatch.setattr(
        app_module,
        "resolve_user_selector",
        lambda memory_dir, user_selector: ("yufeng", "Yufeng"),
    )
    monkeypatch.setattr(
        app_module,
        "ensure_user_memory_file",
        lambda memory_dir, user_id, display_name: memory_dir / f"user-{user_id}.md",
    )

    app_module.main("yufeng")

    assert captured["context_bytes_limit"] == 1234
    assert captured["max_results"] == 7


def test_main_post_review_parse_failure_allows_output(
    monkeypatch,
    tmp_path: Path,
):
    from chat_agent.cli import app as app_module
    from chat_agent.llm.schema import LLMResponse

    class _DummyWorkspace:
        def __init__(self, agent_os_dir: Path):
            self.agent_os_dir = agent_os_dir
            self.memory_dir = agent_os_dir / "memory"

        def is_initialized(self) -> bool:
            return True

        def get_system_prompt(self, _agent: str) -> str:
            return "prompt"

        def get_agent_prompt(self, *args, **kwargs) -> str:
            return "parse-retry"

        def get_timezone(self) -> str:
            return "Asia/Taipei"

    class _DummyInitializer:
        def __init__(self, workspace):
            self.workspace = workspace

        def needs_upgrade(self) -> bool:
            return False

        def upgrade_kernel(self):
            return []

    class _DummyInput:
        def __init__(self, timezone: str, bottom_toolbar=None):
            self.timezone = timezone
            self._calls = 0
            self.wants_history_select = False

        def get_input(self):
            self._calls += 1
            if self._calls == 1:
                return "hello"
            return None

    class _DummyConsole:
        def __init__(self):
            self.errors: list[str] = []
            self.warnings: list[str] = []
            self.assistant_outputs: list[str] = []

        def set_debug(self, debug: bool) -> None:
            self.debug = debug

        def set_show_tool_use(self, show: bool) -> None:
            self.show_tool_use = show

        def print_welcome(self) -> None:
            pass

        def print_goodbye(self) -> None:
            pass

        def print_error(self, message: str) -> None:
            self.errors.append(message)

        def print_info(self, _message: str) -> None:
            pass

        def print_warning(self, message: str, *, indent: int = 0) -> None:  # noqa: ARG002
            self.warnings.append(message)

        def print_assistant(self, content: str | None) -> None:
            if content:
                self.assistant_outputs.append(content)

        def print_debug(self, label: str, message: str) -> None:  # noqa: ARG002
            pass

        def print_debug_block(self, label: str, content: str) -> None:  # noqa: ARG002
            pass

        def spinner(self, text: str = "Thinking..."):  # noqa: ARG002
            class _Ctx:
                def __enter__(self_inner):
                    return None

                def __exit__(self_inner, exc_type, exc, tb):
                    return False

            return _Ctx()

    class _DummyPostReviewer:
        def __init__(self, *args, **kwargs):
            self.last_raw_response = "not-json"
            self.last_error = "parse error"

        def review(self, messages, review_packet=None):  # noqa: ANN001
            return None

    consoles: list[_DummyConsole] = []

    def _console_factory():
        c = _DummyConsole()
        consoles.append(c)
        return c

    monkeypatch.setattr(
        app_module,
        "load_config",
        lambda: _make_app_config_with_post_reviewer(
            tmp_path,
            post_reviewer_enabled=True,
            progress_reviewer_enabled=False,
            warn_on_failure=True,
        ),
    )
    monkeypatch.setattr(app_module, "WorkspaceManager", _DummyWorkspace)
    monkeypatch.setattr(app_module, "WorkspaceInitializer", _DummyInitializer)
    monkeypatch.setattr(app_module, "ChatInput", _DummyInput)
    monkeypatch.setattr(app_module, "ChatConsole", _console_factory)
    monkeypatch.setattr(
        app_module,
        "create_client",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(app_module, "PostReviewer", _DummyPostReviewer)
    from chat_agent.agent import core as core_module
    monkeypatch.setattr(
        core_module,
        "_run_responder",
        lambda *args, **kwargs: LLMResponse(content="final answer", tool_calls=[]),
    )
    monkeypatch.setattr(
        core_module.AgentCore,
        "graceful_exit",
        lambda self: None,
    )
    monkeypatch.setattr(
        app_module,
        "resolve_user_selector",
        lambda memory_dir, user_selector: ("yufeng", "Yufeng"),
    )
    monkeypatch.setattr(
        app_module,
        "ensure_user_memory_file",
        lambda memory_dir, user_id, display_name: memory_dir / f"user-{user_id}.md",
    )

    app_module.main("yufeng")

    assert consoles
    assert consoles[0].errors == []
    assert consoles[0].assistant_outputs == ["final answer"]
    assert consoles[0].warnings


def test_main_post_reviewer_disabled_outputs_normally(
    monkeypatch,
    tmp_path: Path,
):
    from chat_agent.cli import app as app_module
    from chat_agent.llm.schema import LLMResponse

    class _DummyWorkspace:
        def __init__(self, agent_os_dir: Path):
            self.agent_os_dir = agent_os_dir
            self.memory_dir = agent_os_dir / "memory"

        def is_initialized(self) -> bool:
            return True

        def get_system_prompt(self, _agent: str) -> str:
            return "prompt"

        def get_agent_prompt(self, *args, **kwargs) -> str:
            return "parse-retry"

        def get_timezone(self) -> str:
            return "Asia/Taipei"

    class _DummyInitializer:
        def __init__(self, workspace):
            self.workspace = workspace

        def needs_upgrade(self) -> bool:
            return False

        def upgrade_kernel(self):
            return []

    class _DummyInput:
        def __init__(self, timezone: str, bottom_toolbar=None):
            self.timezone = timezone
            self._calls = 0
            self.wants_history_select = False

        def get_input(self):
            self._calls += 1
            if self._calls == 1:
                return "hello"
            return None

    class _DummyConsole:
        def __init__(self):
            self.errors: list[str] = []
            self.assistant_outputs: list[str] = []

        def set_debug(self, debug: bool) -> None:
            self.debug = debug

        def set_show_tool_use(self, show: bool) -> None:
            self.show_tool_use = show

        def print_welcome(self) -> None:
            pass

        def print_goodbye(self) -> None:
            pass

        def print_error(self, message: str) -> None:
            self.errors.append(message)

        def print_info(self, _message: str) -> None:
            pass

        def print_warning(self, _message: str, *, indent: int = 0) -> None:
            pass

        def print_assistant(self, content: str | None) -> None:
            if content:
                self.assistant_outputs.append(content)

        def print_debug(self, label: str, message: str) -> None:  # noqa: ARG002
            pass

        def print_debug_block(self, label: str, content: str) -> None:  # noqa: ARG002
            pass

        def spinner(self, text: str = "Thinking..."):  # noqa: ARG002
            class _Ctx:
                def __enter__(self_inner):
                    return None

                def __exit__(self_inner, exc_type, exc, tb):
                    return False

            return _Ctx()

    consoles: list[_DummyConsole] = []

    def _console_factory():
        c = _DummyConsole()
        consoles.append(c)
        return c

    monkeypatch.setattr(
        app_module,
        "load_config",
        lambda: _make_app_config_with_post_reviewer(
            tmp_path,
            post_reviewer_enabled=False,
            progress_reviewer_enabled=False,
            warn_on_failure=True,
        ),
    )
    monkeypatch.setattr(app_module, "WorkspaceManager", _DummyWorkspace)
    monkeypatch.setattr(app_module, "WorkspaceInitializer", _DummyInitializer)
    monkeypatch.setattr(app_module, "ChatInput", _DummyInput)
    monkeypatch.setattr(app_module, "ChatConsole", _console_factory)
    monkeypatch.setattr(
        app_module,
        "create_client",
        lambda *args, **kwargs: object(),
    )
    from chat_agent.agent import core as core_module
    monkeypatch.setattr(
        core_module,
        "_run_responder",
        lambda *args, **kwargs: LLMResponse(content="final without post", tool_calls=[]),
    )
    monkeypatch.setattr(
        core_module.AgentCore,
        "graceful_exit",
        lambda self: None,
    )
    monkeypatch.setattr(
        app_module,
        "resolve_user_selector",
        lambda memory_dir, user_selector: ("yufeng", "Yufeng"),
    )
    monkeypatch.setattr(
        app_module,
        "ensure_user_memory_file",
        lambda memory_dir, user_id, display_name: memory_dir / f"user-{user_id}.md",
    )

    app_module.main("yufeng")

    assert consoles
    assert consoles[0].errors == []
    assert consoles[0].assistant_outputs == ["final without post"]


def test_main_skips_post_review_when_intermediate_visible(
    monkeypatch,
    tmp_path: Path,
):
    from chat_agent.cli import app as app_module
    from chat_agent.llm.schema import LLMResponse, ToolCall

    class _DummyWorkspace:
        def __init__(self, agent_os_dir: Path):
            self.agent_os_dir = agent_os_dir
            self.memory_dir = agent_os_dir / "memory"

        def is_initialized(self) -> bool:
            return True

        def get_system_prompt(self, _agent: str) -> str:
            return "prompt"

        def get_agent_prompt(self, *args, **kwargs) -> str:
            return "parse-retry"

        def get_timezone(self) -> str:
            return "Asia/Taipei"

    class _DummyInitializer:
        def __init__(self, workspace):
            self.workspace = workspace

        def needs_upgrade(self) -> bool:
            return False

        def upgrade_kernel(self):
            return []

    class _DummyInput:
        def __init__(self, timezone: str, bottom_toolbar=None):
            self.timezone = timezone
            self._calls = 0
            self.wants_history_select = False

        def get_input(self):
            self._calls += 1
            if self._calls == 1:
                return "hello"
            return None

    class _DummyConsole:
        def __init__(self):
            self.errors: list[str] = []
            self.assistant_outputs: list[str] = []

        def set_debug(self, debug: bool) -> None:
            self.debug = debug

        def set_show_tool_use(self, show: bool) -> None:
            self.show_tool_use = show

        def print_welcome(self) -> None:
            pass

        def print_goodbye(self) -> None:
            pass

        def print_error(self, message: str) -> None:
            self.errors.append(message)

        def print_info(self, _message: str) -> None:
            pass

        def print_warning(self, _message: str, *, indent: int = 0) -> None:
            pass

        def print_assistant(self, content: str | None) -> None:
            if content:
                self.assistant_outputs.append(content)

        def print_debug(self, label: str, message: str) -> None:  # noqa: ARG002
            pass

        def print_debug_block(self, label: str, content: str) -> None:  # noqa: ARG002
            pass

        def spinner(self, text: str = "Thinking..."):  # noqa: ARG002
            class _Ctx:
                def __enter__(self_inner):
                    return None

                def __exit__(self_inner, exc_type, exc, tb):
                    return False

            return _Ctx()

    class _DummyPostReviewer:
        instances: list["_DummyPostReviewer"] = []

        def __init__(self, *args, **kwargs):
            self.last_raw_response = None
            self.last_error = None
            self.review_calls = 0
            self.__class__.instances.append(self)

        def review(self, messages, review_packet=None):  # noqa: ANN001
            from chat_agent.reviewer.schema import PostReviewResult
            self.review_calls += 1
            return PostReviewResult(passed=True)

    consoles: list[_DummyConsole] = []
    run_calls = {"count": 0}

    def _console_factory():
        c = _DummyConsole()
        consoles.append(c)
        return c

    def _run_responder_stub(
        client, messages, tools, conversation, builder, registry, console, **kwargs  # noqa: ANN001
    ):
        run_calls["count"] += 1
        console.print_assistant("intermediate already shown")
        tc_shell = ToolCall(id="tc1", name="execute_shell", arguments={"command": "echo hi"})
        tc_mem = ToolCall(id="tc2", name="memory_edit", arguments={
            "requests": [
                {"target_path": "memory/agent/short-term.md", "instruction": "test"},
                {"target_path": "memory/agent/inner-state.md", "instruction": "test"},
            ],
        })
        conversation.add_assistant_with_tools("intermediate already shown", [tc_shell, tc_mem])
        conversation.add_tool_result("tc1", "execute_shell", "ok")
        conversation.add_tool_result(
            "tc2", "memory_edit",
            '{"status":"ok","applied":['
            '{"request_id":"r1","status":"applied","path":"memory/agent/short-term.md"},'
            '{"request_id":"r2","status":"applied","path":"memory/agent/inner-state.md"}'
            '],"errors":[]}',
        )
        return LLMResponse(content="", tool_calls=[])

    monkeypatch.setattr(
        app_module,
        "load_config",
        lambda: _make_app_config_with_post_reviewer(
            tmp_path,
            post_reviewer_enabled=True,
            progress_reviewer_enabled=False,
            warn_on_failure=True,
        ),
    )
    monkeypatch.setattr(app_module, "WorkspaceManager", _DummyWorkspace)
    monkeypatch.setattr(app_module, "WorkspaceInitializer", _DummyInitializer)
    monkeypatch.setattr(app_module, "ChatInput", _DummyInput)
    monkeypatch.setattr(app_module, "ChatConsole", _console_factory)
    monkeypatch.setattr(app_module, "create_client", lambda *args, **kwargs: object())
    monkeypatch.setattr(app_module, "PostReviewer", _DummyPostReviewer)
    from chat_agent.agent import core as core_module
    monkeypatch.setattr(core_module, "_run_responder", _run_responder_stub)
    monkeypatch.setattr(core_module.AgentCore, "graceful_exit", lambda self: None)
    monkeypatch.setattr(
        app_module,
        "resolve_user_selector",
        lambda memory_dir, user_selector: ("yufeng", "Yufeng"),
    )
    monkeypatch.setattr(
        app_module,
        "ensure_user_memory_file",
        lambda memory_dir, user_id, display_name: memory_dir / f"user-{user_id}.md",
    )

    app_module.main("yufeng")

    assert run_calls["count"] == 1
    assert consoles
    assert consoles[0].errors == []
    # Intermediate text is shown live
    assert "intermediate already shown" in consoles[0].assistant_outputs
    assert _DummyPostReviewer.instances
    # Post-review is skipped when only intermediate text exists (no final content)
    assert _DummyPostReviewer.instances[0].review_calls == 0


def test_main_retries_when_turn_has_no_visible_reply(
    monkeypatch,
    tmp_path: Path,
):
    from chat_agent.cli import app as app_module
    from chat_agent.llm.schema import LLMResponse

    class _DummyWorkspace:
        def __init__(self, agent_os_dir: Path):
            self.agent_os_dir = agent_os_dir
            self.memory_dir = agent_os_dir / "memory"

        def is_initialized(self) -> bool:
            return True

        def get_system_prompt(self, _agent: str) -> str:
            return "prompt"

        def get_agent_prompt(self, *args, **kwargs) -> str:
            return "parse-retry"

        def get_timezone(self) -> str:
            return "Asia/Taipei"

    class _DummyInitializer:
        def __init__(self, workspace):
            self.workspace = workspace

        def needs_upgrade(self) -> bool:
            return False

        def upgrade_kernel(self):
            return []

    class _DummyInput:
        def __init__(self, timezone: str, bottom_toolbar=None):
            self.timezone = timezone
            self._calls = 0
            self.wants_history_select = False

        def get_input(self):
            self._calls += 1
            if self._calls == 1:
                return "hello"
            return None

    class _DummyConsole:
        def __init__(self):
            self.errors: list[str] = []
            self.assistant_outputs: list[str] = []

        def set_debug(self, debug: bool) -> None:
            self.debug = debug

        def set_show_tool_use(self, show: bool) -> None:
            self.show_tool_use = show

        def print_welcome(self) -> None:
            pass

        def print_goodbye(self) -> None:
            pass

        def print_error(self, message: str) -> None:
            self.errors.append(message)

        def print_info(self, _message: str) -> None:
            pass

        def print_warning(self, _message: str, *, indent: int = 0) -> None:
            pass

        def print_assistant(self, content: str | None) -> None:
            if content:
                self.assistant_outputs.append(content)

        def print_debug(self, label: str, message: str) -> None:  # noqa: ARG002
            pass

        def print_debug_block(self, label: str, content: str) -> None:  # noqa: ARG002
            pass

        def spinner(self, text: str = "Thinking..."):  # noqa: ARG002
            class _Ctx:
                def __enter__(self_inner):
                    return None

                def __exit__(self_inner, exc_type, exc, tb):
                    return False

            return _Ctx()

    class _DummyPostReviewer:
        instances: list["_DummyPostReviewer"] = []

        def __init__(self, *args, **kwargs):
            self.last_raw_response = None
            self.last_error = None
            self.review_calls = 0
            self.__class__.instances.append(self)

        def review(self, messages, review_packet=None):  # noqa: ANN001
            self.review_calls += 1
            return None

    consoles: list[_DummyConsole] = []
    run_calls = {"count": 0}

    def _console_factory():
        c = _DummyConsole()
        consoles.append(c)
        return c

    def _run_responder_stub(
        client, messages, tools, conversation, builder, registry, console, **kwargs  # noqa: ANN001
    ):
        run_calls["count"] += 1
        if run_calls["count"] == 1:
            return LLMResponse(content="", tool_calls=[])
        return LLMResponse(content="retry final reply", tool_calls=[])

    monkeypatch.setattr(
        app_module,
        "load_config",
        lambda: _make_app_config_with_post_reviewer(
            tmp_path,
            post_reviewer_enabled=True,
            progress_reviewer_enabled=False,
            warn_on_failure=True,
        ),
    )
    monkeypatch.setattr(app_module, "WorkspaceManager", _DummyWorkspace)
    monkeypatch.setattr(app_module, "WorkspaceInitializer", _DummyInitializer)
    monkeypatch.setattr(app_module, "ChatInput", _DummyInput)
    monkeypatch.setattr(app_module, "ChatConsole", _console_factory)
    monkeypatch.setattr(app_module, "create_client", lambda *args, **kwargs: object())
    monkeypatch.setattr(app_module, "PostReviewer", _DummyPostReviewer)
    from chat_agent.agent import core as core_module
    monkeypatch.setattr(core_module, "_run_responder", _run_responder_stub)
    monkeypatch.setattr(core_module.AgentCore, "graceful_exit", lambda self: None)
    # Disable memory sync so it doesn't inject an extra re-run.
    monkeypatch.setattr(core_module, "find_missing_memory_sync_targets", lambda _msgs: [])
    monkeypatch.setattr(
        app_module,
        "resolve_user_selector",
        lambda memory_dir, user_selector: ("yufeng", "Yufeng"),
    )
    monkeypatch.setattr(
        app_module,
        "ensure_user_memory_file",
        lambda memory_dir, user_id, display_name: memory_dir / f"user-{user_id}.md",
    )

    app_module.main("yufeng")

    assert run_calls["count"] == 2
    assert consoles
    assert consoles[0].errors == []
    assert consoles[0].assistant_outputs == ["retry final reply"]
    assert _DummyPostReviewer.instances
    assert _DummyPostReviewer.instances[0].review_calls == 1
