"""Tests for vision tool wiring in setup_tools."""

from pathlib import Path
from unittest.mock import MagicMock

from chat_agent.cli.app import setup_tools
from chat_agent.core.schema import ToolsConfig
from chat_agent.tools.builtin.vision import VisionAgent


class TestVisionToolWiring:
    def _base_config(self) -> ToolsConfig:
        return ToolsConfig(allowed_paths=[])

    def test_no_vision_no_tool(self, tmp_path: Path):
        """Without vision flag or agent, read_image is not registered."""
        registry = setup_tools(self._base_config(), tmp_path)
        assert not registry.has_tool("read_image")

    def test_brain_has_vision_registers_multimodal(self, tmp_path: Path):
        """When brain has vision, read_image returns multimodal content."""
        registry = setup_tools(
            self._base_config(), tmp_path,
            brain_has_vision=True,
        )
        assert registry.has_tool("read_image")

    def test_vision_agent_registers_text_tool(self, tmp_path: Path):
        """When vision agent provided, read_image returns text."""
        fake_agent = MagicMock(spec=VisionAgent)
        registry = setup_tools(
            self._base_config(), tmp_path,
            brain_has_vision=False,
            vision_agent=fake_agent,
        )
        assert registry.has_tool("read_image")

    def test_brain_vision_takes_priority(self, tmp_path: Path):
        """When both brain_has_vision and vision_agent, brain vision wins."""
        fake_agent = MagicMock(spec=VisionAgent)
        registry = setup_tools(
            self._base_config(), tmp_path,
            brain_has_vision=True,
            vision_agent=fake_agent,
        )
        assert registry.has_tool("read_image")


class TestScreenshotToolWiring:
    def _base_config(self) -> ToolsConfig:
        return ToolsConfig(allowed_paths=[])

    def test_screenshot_registered_when_brain_has_vision(self, tmp_path: Path):
        """When brain has vision, screenshot tool is registered."""
        registry = setup_tools(
            self._base_config(), tmp_path,
            brain_has_vision=True,
        )
        assert registry.has_tool("screenshot")

    def test_screenshot_not_registered_without_vision(self, tmp_path: Path):
        """Without vision, screenshot tool is not registered."""
        registry = setup_tools(
            self._base_config(), tmp_path,
            brain_has_vision=False,
        )
        assert not registry.has_tool("screenshot")
