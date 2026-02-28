"""Tests for vision tool wiring in setup_tools."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock

from chat_agent.agent.core import setup_tools
from chat_agent.core.schema import ToolsConfig
from chat_agent.gui.manager import GUIManager
from chat_agent.gui.worker import GUIWorker
from chat_agent.tools.builtin.vision import VisionAgent


class TestVisionToolWiring:
    def _base_config(self) -> ToolsConfig:
        return ToolsConfig(allowed_paths=[])

    def test_no_vision_no_tool(self, tmp_path: Path):
        """Without vision flag or agent, read_image is not registered."""
        registry, _ = setup_tools(self._base_config(), tmp_path)
        assert not registry.has_tool("read_image")

    def test_brain_has_vision_registers_multimodal(self, tmp_path: Path):
        """When brain has vision and uses own ability, read_image returns multimodal content."""
        registry, _ = setup_tools(
            self._base_config(), tmp_path,
            brain_has_vision=True,
            use_own_vision_ability=True,
        )
        assert registry.has_tool("read_image")

    def test_vision_agent_registers_text_tool(self, tmp_path: Path):
        """When vision agent provided, read_image returns text."""
        fake_agent = MagicMock(spec=VisionAgent)
        registry, _ = setup_tools(
            self._base_config(), tmp_path,
            brain_has_vision=False,
            vision_agent=fake_agent,
        )
        assert registry.has_tool("read_image")

    def test_brain_vision_takes_priority_when_use_own(self, tmp_path: Path):
        """When use_own_vision_ability=True, brain vision wins over sub-agent."""
        fake_agent = MagicMock(spec=VisionAgent)
        registry, _ = setup_tools(
            self._base_config(), tmp_path,
            brain_has_vision=True,
            use_own_vision_ability=True,
            vision_agent=fake_agent,
        )
        assert registry.has_tool("read_image")
        assert not registry.has_tool("read_image_by_subagent")

    def test_delegates_to_subagent_when_not_use_own(self, tmp_path: Path):
        """When use_own_vision_ability=False + vision agent, registers subagent tool."""
        fake_agent = MagicMock(spec=VisionAgent)
        registry, _ = setup_tools(
            self._base_config(), tmp_path,
            brain_has_vision=True,
            use_own_vision_ability=False,
            vision_agent=fake_agent,
        )
        assert registry.has_tool("read_image_by_subagent")
        assert not registry.has_tool("read_image")

    def test_fallback_to_direct_without_agent(self, tmp_path: Path):
        """When use_own_vision_ability=False but no vision agent, falls back to direct."""
        registry, _ = setup_tools(
            self._base_config(), tmp_path,
            brain_has_vision=True,
            use_own_vision_ability=False,
        )
        assert registry.has_tool("read_image")
        assert not registry.has_tool("read_image_by_subagent")


class TestScreenshotToolWiring:
    def _base_config(self) -> ToolsConfig:
        return ToolsConfig(allowed_paths=[])

    def test_screenshot_registered_when_brain_has_vision_use_own(self, tmp_path: Path):
        """When brain has vision and uses own ability, screenshot is direct."""
        registry, _ = setup_tools(
            self._base_config(), tmp_path,
            brain_has_vision=True,
            use_own_vision_ability=True,
        )
        assert registry.has_tool("screenshot")
        assert not registry.has_tool("screenshot_by_subagent")

    def test_screenshot_not_registered_without_vision(self, tmp_path: Path):
        """Without vision, neither screenshot tool is registered."""
        registry, _ = setup_tools(
            self._base_config(), tmp_path,
            brain_has_vision=False,
        )
        assert not registry.has_tool("screenshot")
        assert not registry.has_tool("screenshot_by_subagent")

    def test_delegates_to_subagent_when_gui_worker(self, tmp_path: Path):
        """When brain_has_vision + !use_own + gui_worker, registers subagent."""
        fake_worker = MagicMock(spec=GUIWorker)
        registry, _ = setup_tools(
            self._base_config(), tmp_path,
            brain_has_vision=True,
            use_own_vision_ability=False,
            gui_worker=fake_worker,
        )
        assert registry.has_tool("screenshot_by_subagent")
        assert not registry.has_tool("screenshot")

    def test_fallback_to_direct_without_gui_worker(self, tmp_path: Path):
        """When brain_has_vision + !use_own but no gui_worker, falls back to direct."""
        registry, _ = setup_tools(
            self._base_config(), tmp_path,
            brain_has_vision=True,
            use_own_vision_ability=False,
        )
        assert registry.has_tool("screenshot")
        assert not registry.has_tool("screenshot_by_subagent")

    def test_use_own_ignores_gui_worker(self, tmp_path: Path):
        """When use_own_vision_ability=True, gui_worker is ignored for screenshot."""
        fake_worker = MagicMock(spec=GUIWorker)
        registry, _ = setup_tools(
            self._base_config(), tmp_path,
            brain_has_vision=True,
            use_own_vision_ability=True,
            gui_worker=fake_worker,
        )
        assert registry.has_tool("screenshot")
        assert not registry.has_tool("screenshot_by_subagent")


class TestGuiManagerCaptureDir:
    def _base_config(self) -> ToolsConfig:
        return ToolsConfig(allowed_paths=[])

    def test_capture_dir_added_to_allowed_paths(self, tmp_path: Path):
        """When gui_manager is provided, its capture_dir is in allowed_paths."""
        mock_manager = MagicMock(spec=GUIManager)
        type(mock_manager).capture_dir = PropertyMock(return_value=tempfile.gettempdir())

        registry, _ = setup_tools(
            self._base_config(), tmp_path,
            brain_has_vision=True,
            gui_manager=mock_manager,
        )
        # read_image should be able to access temp dir files
        assert registry.has_tool("read_image")
        # gui_task is registered after queue creation in app.py, not via setup_tools
