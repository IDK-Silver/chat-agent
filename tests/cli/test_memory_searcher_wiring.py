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
