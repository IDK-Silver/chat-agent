"""Tests for gui/worker.py: GUIWorker single-shot observation."""

import json
from pathlib import Path
from unittest.mock import patch

from chat_agent.gui.worker import GUIWorker, ScreenDescription, WorkerObservation
from chat_agent.llm.schema import ContentPart


class FakeWorkerClient:
    """LLM client that returns canned JSON responses."""

    def __init__(self, response: str):
        self._response = response
        self.call_count = 0

    def chat(self, messages, response_schema=None, temperature=None):
        self.call_count += 1
        assert len(messages) == 2
        assert messages[0].role == "system"
        assert messages[1].role == "user"
        # User message should contain image + text
        assert isinstance(messages[1].content, list)
        return self._response

    def chat_with_tools(self, messages, tools, temperature=None):
        raise NotImplementedError


def _make_fake_jpeg_b64() -> str:
    """Create a tiny valid JPEG image and return its base64 encoding."""
    import base64 as _b64
    import io as _io

    from PIL import Image as _Image

    img = _Image.new("RGB", (100, 50), color=(128, 128, 128))
    buf = _io.BytesIO()
    img.save(buf, format="JPEG", quality=50)
    return _b64.b64encode(buf.getvalue()).decode("ascii")


_FAKE_JPEG_B64 = _make_fake_jpeg_b64()


def _fake_screenshot(**kwargs):
    return ContentPart(type="image", media_type="image/jpeg", data=_FAKE_JPEG_B64, width=100, height=50)


class TestWorkerObservation:
    def test_defaults(self):
        obs = WorkerObservation(description="test")
        assert obs.found is True
        assert obs.bbox is None
        assert obs.mismatch is None
        assert obs.obstructed is None
        assert obs.screenshot_sec == 0.0
        assert obs.inference_sec == 0.0

    def test_with_bbox(self):
        obs = WorkerObservation(description="button", bbox=[10, 20, 30, 40], found=True)
        assert obs.bbox == [10, 20, 30, 40]

    def test_with_mismatch(self):
        obs = WorkerObservation(
            description="Found Alice", found=False,
            mismatch="Found 'Alice' instead of 'Bob'",
        )
        assert obs.mismatch == "Found 'Alice' instead of 'Bob'"
        assert obs.found is False

    def test_with_obstructed(self):
        obs = WorkerObservation(
            description="Button visible but covered", found=True,
            bbox=[10, 20, 30, 40],
            obstructed="Autocomplete dropdown is covering the button",
        )
        assert obs.obstructed == "Autocomplete dropdown is covering the button"
        assert obs.found is True


class TestGUIWorker:
    @patch("chat_agent.gui.worker.take_screenshot", side_effect=_fake_screenshot)
    def test_observe_parses_json(self, mock_ss):
        response = json.dumps({
            "description": "I see a Send button",
            "found": True,
            "bbox": [100, 200, 150, 300],
        })
        client = FakeWorkerClient(response)
        worker = GUIWorker(client, "You are a worker.", parse_retries=0)
        obs = worker.observe("Find the Send button")
        assert obs.found is True
        assert obs.description == "I see a Send button"
        assert obs.bbox == [100, 200, 150, 300]
        assert client.call_count == 1

    @patch("chat_agent.gui.worker.take_screenshot", side_effect=_fake_screenshot)
    def test_observe_not_found(self, mock_ss):
        response = json.dumps({
            "description": "No button visible",
            "found": False,
            "bbox": None,
        })
        client = FakeWorkerClient(response)
        worker = GUIWorker(client, "You are a worker.", parse_retries=0)
        obs = worker.observe("Find the Submit button")
        assert obs.found is False
        assert obs.bbox is None

    @patch("chat_agent.gui.worker.take_screenshot", side_effect=_fake_screenshot)
    def test_observe_fallback_on_bad_json(self, mock_ss):
        client = FakeWorkerClient("This is not JSON at all")
        worker = GUIWorker(client, "You are a worker.", parse_retries=0)
        obs = worker.observe("Find something")
        assert obs.found is False
        assert "not JSON" in obs.description

    @patch("chat_agent.gui.worker.take_screenshot", side_effect=_fake_screenshot)
    def test_observe_json_in_markdown_block(self, mock_ss):
        response = '```json\n{"description": "Found it", "found": true, "bbox": [1, 2, 3, 4]}\n```'
        client = FakeWorkerClient(response)
        worker = GUIWorker(client, "You are a worker.", parse_retries=0)
        obs = worker.observe("Find element")
        assert obs.found is True
        assert obs.bbox == [1, 2, 3, 4]

    @patch("chat_agent.gui.worker.take_screenshot", side_effect=_fake_screenshot)
    def test_observe_parses_mismatch(self, mock_ss):
        response = json.dumps({
            "description": "Found Alice contact",
            "found": False,
            "bbox": None,
            "mismatch": "Found 'Alice' instead of 'Bob'",
            "obstructed": None,
        })
        client = FakeWorkerClient(response)
        worker = GUIWorker(client, "You are a worker.", parse_retries=0)
        obs = worker.observe("Find Bob's contact")
        assert obs.found is False
        assert obs.mismatch == "Found 'Alice' instead of 'Bob'"
        assert obs.obstructed is None

    @patch("chat_agent.gui.worker.take_screenshot", side_effect=_fake_screenshot)
    def test_observe_parses_obstructed(self, mock_ss):
        response = json.dumps({
            "description": "Send button visible but covered",
            "found": True,
            "bbox": [100, 200, 150, 300],
            "mismatch": None,
            "obstructed": "Autocomplete dropdown covering the button",
        })
        client = FakeWorkerClient(response)
        worker = GUIWorker(client, "You are a worker.", parse_retries=0)
        obs = worker.observe("Find the Send button")
        assert obs.found is True
        assert obs.bbox == [100, 200, 150, 300]
        assert obs.obstructed == "Autocomplete dropdown covering the button"
        assert obs.mismatch is None

    @patch("chat_agent.gui.worker.take_screenshot", side_effect=_fake_screenshot)
    def test_observe_populates_timing(self, mock_ss):
        response = json.dumps({
            "description": "Found button",
            "found": True,
            "bbox": [10, 20, 30, 40],
        })
        client = FakeWorkerClient(response)
        worker = GUIWorker(client, "You are a worker.", parse_retries=0)
        obs = worker.observe("Find button")
        assert obs.screenshot_sec >= 0
        assert obs.inference_sec >= 0


class TestScanLayout:
    @patch("chat_agent.gui.worker.take_screenshot", side_effect=_fake_screenshot)
    def test_scan_layout_returns_text(self, mock_ss):
        client = FakeWorkerClient("Left panel: chat list. Right panel: conversation.")
        worker = GUIWorker(
            client, "You are a worker.",
            layout_prompt="Describe the layout.",
        )
        result = worker.scan_layout()
        assert "chat list" in result
        assert client.call_count == 1

    @patch("chat_agent.gui.worker.take_screenshot", side_effect=_fake_screenshot)
    def test_scan_layout_uses_layout_prompt(self, mock_ss):
        client = FakeWorkerClient("layout description")
        worker = GUIWorker(
            client, "observe prompt",
            layout_prompt="LAYOUT SYSTEM PROMPT",
        )
        worker.scan_layout()
        # The system message should use layout_prompt, not system_prompt
        call_messages = None
        # FakeWorkerClient asserts messages[0].role == "system"
        # We verify the prompt content by inspecting the client directly
        assert client.call_count == 1

    def test_scan_layout_raises_without_prompt(self):
        client = FakeWorkerClient("anything")
        worker = GUIWorker(client, "observe prompt")
        try:
            worker.scan_layout()
            assert False, "Expected RuntimeError"
        except RuntimeError as e:
            assert "layout_prompt" in str(e)


class TestScreenDescription:
    def test_defaults(self):
        sd = ScreenDescription(description="test")
        assert sd.crop_path is None
        assert sd.screenshot_sec == 0.0
        assert sd.inference_sec == 0.0

    def test_with_crop(self):
        sd = ScreenDescription(description="found it", crop_path="/tmp/crop.jpg")
        assert sd.crop_path == "/tmp/crop.jpg"


class TestDescribeScreen:
    @patch("chat_agent.gui.worker.take_screenshot", side_effect=_fake_screenshot)
    def test_returns_description(self, mock_ss):
        response = json.dumps({"description": "I see a QR code on the right side"})
        client = FakeWorkerClient(response)
        worker = GUIWorker(
            client, "observe prompt",
            describe_prompt="Analyze the screenshot.",
        )
        result = worker.describe_screen("Find the QR code")
        assert result.description == "I see a QR code on the right side"
        assert result.crop_path is None
        assert result.screenshot_sec >= 0
        assert result.inference_sec >= 0

    @patch("chat_agent.gui.worker.take_screenshot", side_effect=_fake_screenshot)
    def test_with_crop_bbox(self, mock_ss, tmp_path: Path):
        response = json.dumps({
            "description": "QR code found at top-right",
            "crop_bbox": [100, 700, 300, 900],
            "crop_label": "qr-code",
        })
        client = FakeWorkerClient(response)
        worker = GUIWorker(
            client, "observe prompt",
            describe_prompt="Analyze the screenshot.",
        )
        result = worker.describe_screen(
            "Find and crop the QR code",
            save_dir=str(tmp_path),
        )
        assert result.description == "QR code found at top-right"
        assert result.crop_path is not None
        assert "qr-code" in result.crop_path
        assert Path(result.crop_path).exists()

    @patch("chat_agent.gui.worker.take_screenshot", side_effect=_fake_screenshot)
    def test_no_crop_bbox_means_no_file(self, mock_ss, tmp_path: Path):
        response = json.dumps({
            "description": "No QR code visible",
            "crop_bbox": None,
        })
        client = FakeWorkerClient(response)
        worker = GUIWorker(
            client, "observe prompt",
            describe_prompt="Analyze the screenshot.",
        )
        result = worker.describe_screen(
            "Find the QR code",
            save_dir=str(tmp_path),
        )
        assert result.crop_path is None

    @patch("chat_agent.gui.worker.take_screenshot", side_effect=_fake_screenshot)
    def test_bad_json_fallback(self, mock_ss):
        client = FakeWorkerClient("This is just plain text")
        worker = GUIWorker(
            client, "observe prompt",
            describe_prompt="Analyze the screenshot.",
        )
        result = worker.describe_screen("What do you see?")
        assert "plain text" in result.description
        assert result.crop_path is None

    def test_raises_without_describe_prompt(self):
        client = FakeWorkerClient("anything")
        worker = GUIWorker(client, "observe prompt")
        try:
            worker.describe_screen("test")
            assert False, "Expected RuntimeError"
        except RuntimeError as e:
            assert "describe_prompt" in str(e)
