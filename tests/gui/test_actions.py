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
        assert result.media_type == "image/jpeg"
        assert result.data is not None
        assert result.width == 100
        assert result.height == 50

    def test_resize_when_wider_than_max(self, mock_pyautogui):
        from PIL import Image

        from chat_agent.gui.actions import take_screenshot

        img = Image.new("RGB", (2000, 1000), color="blue")
        mock_pyautogui.screenshot.return_value = img

        result = take_screenshot(max_width=1000, quality=85)
        assert result.width == 1000
        assert result.height == 500
        assert result.media_type == "image/jpeg"

    def test_no_resize_when_within_max(self, mock_pyautogui):
        from PIL import Image

        from chat_agent.gui.actions import take_screenshot

        img = Image.new("RGB", (800, 600), color="green")
        mock_pyautogui.screenshot.return_value = img

        result = take_screenshot(max_width=1280)
        assert result.width == 800
        assert result.height == 600


class TestClickAtBbox:
    def test_clicks_at_center(self, mock_pyautogui):
        from chat_agent.gui.actions import click_at_bbox

        mock_pyautogui.size.return_value = (1920, 1080)
        result = click_at_bbox([0, 0, 1000, 1000])
        mock_pyautogui.click.assert_called_once_with(960.0, 540.0)
        assert "960" in result
        assert "540" in result


class TestTypeText:
    @patch("subprocess.run")
    def test_always_uses_clipboard(self, mock_run, mock_pyautogui):
        from chat_agent.gui.actions import type_text

        result = type_text("hello")
        mock_run.assert_called_once()
        mock_pyautogui.hotkey.assert_called_once_with("command", "v")
        assert "hello" in result

    @patch("subprocess.run")
    def test_unicode_uses_clipboard(self, mock_run, mock_pyautogui):
        from chat_agent.gui.actions import type_text

        type_text("\u4f60\u597d")
        mock_run.assert_called_once()
        mock_pyautogui.hotkey.assert_called_once_with("command", "v")


class TestCaptureScreenshot:
    @patch("subprocess.run")
    def test_captures_to_temp_file(self, mock_run, mock_pyautogui):
        from chat_agent.gui.actions import capture_screenshot_to_temp

        result = capture_screenshot_to_temp("/tmp/test.png")
        mock_run.assert_called_once_with(
            ["screencapture", "-x", "/tmp/test.png"], check=True,
        )
        assert "captured" in result.lower()


class TestPasteScreenshot:
    @patch("subprocess.run")
    def test_copies_temp_to_clipboard(self, mock_run, mock_pyautogui, tmp_path):
        from chat_agent.gui.actions import paste_screenshot_from_temp

        temp_file = tmp_path / "screenshot.png"
        temp_file.write_bytes(b"fake png")
        result = paste_screenshot_from_temp(str(temp_file))
        mock_run.assert_called_once()
        assert "clipboard" in result.lower()

    def test_error_when_no_file(self, mock_pyautogui):
        from chat_agent.gui.actions import paste_screenshot_from_temp

        result = paste_screenshot_from_temp("/tmp/nonexistent.png")
        assert "error" in result.lower()


class TestActivateApp:
    @patch("subprocess.run")
    def test_macos_single_match(self, mock_run, mock_pyautogui):
        from chat_agent.gui.actions import activate_app

        mock_run.side_effect = [
            # mdfind returns one match
            MagicMock(stdout="/Applications/Utilities/Terminal.app\n", returncode=0),
            # open the app
            MagicMock(returncode=0),
        ]
        result = activate_app("Terminal")
        assert "Terminal.app" in result
        assert mock_run.call_count == 2
        # Verify mdfind uses query expression, not -name flag
        mdfind_call = mock_run.call_args_list[0]
        assert "mdfind" in mdfind_call.args[0]
        assert any("kMDItemFSName" in a for a in mdfind_call.args[0])

    @patch("subprocess.run")
    def test_macos_exact_match_filters_substring(self, mock_run, mock_pyautogui):
        """Exact name match wins over substring matches (e.g. LINE vs Trampoline)."""
        from chat_agent.gui.actions import activate_app

        mock_run.side_effect = [
            MagicMock(
                stdout=(
                    "/System/Library/GameTrampoline.app\n"
                    "/Applications/LINE.app\n"
                    "/System/Library/MDMMigrationTrampoline.app\n"
                ),
                returncode=0,
            ),
            MagicMock(returncode=0),  # open
        ]
        result = activate_app("LINE")
        assert "Activated" in result
        assert "LINE.app" in result
        assert mock_run.call_count == 2

    @patch("subprocess.run")
    def test_macos_multiple_matches_no_exact(self, mock_run, mock_pyautogui):
        from chat_agent.gui.actions import activate_app

        mock_run.return_value = MagicMock(
            stdout="/Applications/TermHere.app\n/Applications/TerminalPlus.app\n",
            returncode=0,
        )
        result = activate_app("Term")
        assert "Multiple" in result
        assert "TermHere.app" in result
        assert "TerminalPlus.app" in result

    @patch("subprocess.run")
    def test_macos_no_match(self, mock_run, mock_pyautogui):
        from chat_agent.gui.actions import activate_app

        mock_run.return_value = MagicMock(stdout="", returncode=0)
        result = activate_app("NonExistentApp")
        assert "No application" in result


class TestGetActiveApp:
    @patch("subprocess.run")
    def test_macos_returns_app_name(self, mock_run, mock_pyautogui):
        from chat_agent.gui.actions import get_active_app

        mock_run.return_value = MagicMock(stdout="Terminal\n", returncode=0)
        result = get_active_app()
        assert result == "Terminal"
        mock_run.assert_called_once()


class TestWait:
    def test_wait_clamps_and_sleeps(self):
        from chat_agent.gui.actions import wait

        with patch("time.sleep") as mock_sleep:
            result = wait(2.0)
            mock_sleep.assert_called_once_with(2.0)
            assert "2.0s" in result

    def test_wait_clamps_minimum(self):
        from chat_agent.gui.actions import wait

        with patch("time.sleep") as mock_sleep:
            wait(0.01)
            mock_sleep.assert_called_once_with(0.1)

    def test_wait_clamps_maximum(self):
        from chat_agent.gui.actions import wait

        with patch("time.sleep") as mock_sleep:
            wait(99.0)
            mock_sleep.assert_called_once_with(10.0)


class TestPressKey:
    @pytest.fixture(autouse=True)
    def _set_keyboard_keys(self, mock_pyautogui):
        mock_pyautogui.KEYBOARD_KEYS = [
            "enter", "tab", "escape", "space", "command", "a",
            "pagedown", "pageup", "home", "end",
        ]

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

    def test_normalize_underscore(self, mock_pyautogui):
        from chat_agent.gui.actions import press_key

        result = press_key("page_down")
        mock_pyautogui.press.assert_called_once_with("pagedown")
        assert "page_down" in result

    def test_normalize_caps(self, mock_pyautogui):
        from chat_agent.gui.actions import press_key

        result = press_key("End")
        mock_pyautogui.press.assert_called_once_with("end")
        assert "End" in result

    def test_invalid_key_returns_error(self, mock_pyautogui):
        from chat_agent.gui.actions import press_key

        result = press_key("nosuchkey")
        assert result.startswith("Error:")
        mock_pyautogui.press.assert_not_called()

    def test_invalid_combo_key_returns_error(self, mock_pyautogui):
        from chat_agent.gui.actions import press_key

        result = press_key("command+nosuchkey")
        assert result.startswith("Error:")
        mock_pyautogui.hotkey.assert_not_called()
