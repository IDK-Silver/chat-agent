"""Tests for macOS personal-app tool wiring in setup_tools."""

from pathlib import Path

from chat_agent.agent.core import setup_tools
from chat_agent.core.schema import ToolsConfig


class TestMacOSToolWiring:
    def _base_config(self) -> ToolsConfig:
        return ToolsConfig(allowed_paths=[])

    def test_registers_apple_app_tools_on_macos(
        self,
        tmp_path: Path,
        monkeypatch,
    ):
        """macOS should register the Apple app tools by default."""
        monkeypatch.setattr("chat_agent.agent.tool_setup.sys.platform", "darwin")
        registry, _, _ = setup_tools(self._base_config(), tmp_path)

        for name in ("calendar_tool", "reminders_tool", "notes_tool", "photos_tool"):
            assert registry.has_tool(name)
            assert registry.is_side_effect(name)

    def test_disabled_apple_apps_skip_registration(
        self,
        tmp_path: Path,
        monkeypatch,
    ):
        """Disabling apple_apps should skip registration."""
        monkeypatch.setattr("chat_agent.agent.tool_setup.sys.platform", "darwin")
        config = self._base_config().model_copy(
            update={
                "apple_apps": self._base_config().apple_apps.model_copy(
                    update={"enabled": False}
                )
            }
        )
        registry, _, _ = setup_tools(config, tmp_path)

        assert not registry.has_tool("calendar_tool")
        assert not registry.has_tool("reminders_tool")
        assert not registry.has_tool("notes_tool")
        assert not registry.has_tool("photos_tool")

    def test_non_macos_skips_registration(
        self,
        tmp_path: Path,
        monkeypatch,
    ):
        """Non-macOS platforms should not register the Apple app tools."""
        monkeypatch.setattr("chat_agent.agent.tool_setup.sys.platform", "linux")
        registry, _, _ = setup_tools(self._base_config(), tmp_path)

        assert not registry.has_tool("calendar_tool")
        assert not registry.has_tool("reminders_tool")
        assert not registry.has_tool("notes_tool")
        assert not registry.has_tool("photos_tool")
