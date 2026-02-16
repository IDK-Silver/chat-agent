"""Tests for vision tool wiring in setup_tools."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock

from chat_agent.cli.app import setup_tools
from chat_agent.core.schema import ToolsConfig
from chat_agent.gui.manager import GUIManager
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


class TestGuiManagerCaptureDir:
    def _base_config(self) -> ToolsConfig:
        return ToolsConfig(allowed_paths=[])

    def test_capture_dir_added_to_allowed_paths(self, tmp_path: Path):
        """When gui_manager is provided, its capture_dir is in allowed_paths."""
        mock_manager = MagicMock(spec=GUIManager)
        type(mock_manager).capture_dir = PropertyMock(return_value=tempfile.gettempdir())

        registry = setup_tools(
            self._base_config(), tmp_path,
            brain_has_vision=True,
            gui_manager=mock_manager,
        )
        # read_image should be able to access temp dir files
        assert registry.has_tool("read_image")
        assert registry.has_tool("gui_task")
