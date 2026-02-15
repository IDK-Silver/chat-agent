"""Tests for gui/actions.py: coordinate conversion and desktop primitives."""

import sys
from unittest.mock import MagicMock, patch

import pytest

from chat_agent.gui.actions import bbox_to_center_pixels
from chat_agent.llm.schema import ContentPart


@pytest.fixture()
def mock_pyautogui():
    """Inject a mock pyautogui module so lazy imports resolve."""
    mock = MagicMock()
    with patch.dict(sys.modules, {"pyautogui": mock}):
        yield mock


class TestBboxToCenterPixels:
    def test_origin(self):
        cx, cy = bbox_to_center_pixels([0, 0, 0, 0], 1920, 1080)
        assert cx == 0.0
        assert cy == 0.0

    def test_center_of_screen(self):
        cx, cy = bbox_to_center_pixels([0, 0, 1000, 1000], 1920, 1080)
        assert cx == 960.0
        assert cy == 540.0

    def test_bottom_right(self):
        cx, cy = bbox_to_center_pixels([1000, 1000, 1000, 1000], 1920, 1080)
        assert cx == 1920.0
        assert cy == 1080.0

    def test_quarter_box(self):
        cx, cy = bbox_to_center_pixels([0, 0, 500, 500], 1000, 1000)
        assert cx == 250.0
        assert cy == 250.0

    def test_asymmetric_screen(self):
        cx, cy = bbox_to_center_pixels([100, 200, 300, 400], 2000, 1000)
        assert cx == 600.0
        assert cy == 200.0


class TestTakeScreenshot:
    def test_returns_content_part(self, mock_pyautogui):
        from PIL import Image

        from chat_agent.gui.actions import take_screenshot

        img = Image.new("RGB", (100, 50), color="red")
        mock_pyautogui.screenshot.return_value = img

        result = take_screenshot()
        assert isinstance(result, ContentPart)
        assert result.type == "image"
        assert result.media_type == "image/png"
        assert result.data is not None
        assert result.width == 100
        assert result.height == 50


class TestClickAtBbox:
    def test_clicks_at_center(self, mock_pyautogui):
        from chat_agent.gui.actions import click_at_bbox

        mock_pyautogui.size.return_value = (1920, 1080)
        result = click_at_bbox([0, 0, 1000, 1000])
        mock_pyautogui.click.assert_called_once_with(960.0, 540.0)
        assert "960" in result
        assert "540" in result


class TestTypeText:
    def test_ascii_uses_typewrite(self, mock_pyautogui):
        from chat_agent.gui.actions import type_text

        result = type_text("hello")
        mock_pyautogui.typewrite.assert_called_once_with("hello", interval=0.02)
        assert "hello" in result

    @patch("subprocess.run")
    def test_unicode_uses_clipboard(self, mock_run, mock_pyautogui):
        from chat_agent.gui.actions import type_text

        type_text("\u4f60\u597d")
        mock_run.assert_called_once()
        mock_pyautogui.hotkey.assert_called_once_with("command", "v")


class TestPressKey:
    def test_single_key(self, mock_pyautogui):
        from chat_agent.gui.actions import press_key

        result = press_key("enter")
        mock_pyautogui.press.assert_called_once_with("enter")
        assert "enter" in result

    def test_combo_key(self, mock_pyautogui):
        from chat_agent.gui.actions import press_key

        result = press_key("command+a")
        mock_pyautogui.hotkey.assert_called_once_with("command", "a")
        assert "command+a" in result
