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

    class _DummyConsole:
        def set_debug(self, debug: bool) -> None:
            self.debug = debug

        def set_show_tool_use(self, show: bool) -> None:
            self.show_tool_use = show

        def set_current_user(self, user_id: str) -> None:
            pass

        def set_timezone(self, timezone: str) -> None:
            pass

        def print_welcome(self) -> None:
            pass

        def print_goodbye(self) -> None:
            pass

        def print_error(self, _message: str) -> None:
            pass

        def print_info(self, _message: str) -> None:
            pass

        def print_shell_stream_line(self, _line: str) -> None:
            pass

        def set_ctx_status_provider(self, _provider) -> None:
            pass

    class _DummyTextualApp:
        def __init__(self, *args, **kwargs):
            pass

        def post_ui_event(self, _event) -> None:
            pass

        def run(self) -> None:
            pass

    monkeypatch.setattr(app_module, "load_config", lambda: _make_app_config(tmp_path))
    monkeypatch.setattr(app_module, "WorkspaceManager", _DummyWorkspace)
    monkeypatch.setattr(app_module, "WorkspaceInitializer", _DummyInitializer)
    monkeypatch.setattr(app_module, "TextualUiConsole", lambda *a, **kw: _DummyConsole())
    monkeypatch.setattr(app_module, "ChatTextualApp", _DummyTextualApp)
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

    # Mock components added after MQ Phase 2 to prevent blocking.
    class _DummyRegistry:
        def register(self, *a, **kw):
            pass

    class _DummyAgent:
        adapters = {}
        turn_context = None
        def __init__(self, **kwargs):
            pass
        def register_adapter(self, adapter):
            pass
        def run(self):
            pass
        def request_shutdown(self, graceful=False):
            pass

    monkeypatch.setattr(app_module, "AgentCore", _DummyAgent)
    monkeypatch.setattr(app_module, "setup_tools", lambda *a, **kw: (_DummyRegistry(), []))
    class _DummyCliAdapter:
        channel_name = "cli"
        priority = 0
        def __init__(self, **kw):
            pass
        def start(self, agent):
            pass
        def send(self, message):
            pass
        def on_turn_start(self, channel):
            pass
        def on_turn_complete(self):
            pass
        def stop(self):
            pass
        def submit_input(self, text: str) -> bool:
            return False
        def select_recent_input(self):
            return None
        def list_recent_inputs(self, limit: int = 10):
            return []
        def select_recent_input_by_index(self, choice: int, limit: int = 10):
            return None

    monkeypatch.setattr(app_module, "CLIAdapter", _DummyCliAdapter)
    monkeypatch.setattr(app_module, "PersistentPriorityQueue", lambda *a, **kw: None)
    monkeypatch.setattr(app_module, "ContactMap", lambda *a, **kw: None)
    monkeypatch.setattr(app_module, "CommandHandler", lambda *a, **kw: None)

    app_module.main("yufeng")

    assert captured["context_bytes_limit"] == 1234
    assert captured["max_results"] == 7
