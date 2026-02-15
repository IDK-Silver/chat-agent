"""Tests for gui/worker.py: GUIWorker single-shot observation."""

import json
from unittest.mock import patch

from chat_agent.gui.worker import GUIWorker, WorkerObservation
from chat_agent.llm.schema import ContentPart


class FakeWorkerClient:
    """LLM client that returns canned JSON responses."""

    def __init__(self, response: str):
        self._response = response
        self.call_count = 0

    def chat(self, messages, response_schema=None):
        self.call_count += 1
        assert len(messages) == 2
        assert messages[0].role == "system"
        assert messages[1].role == "user"
        # User message should contain image + text
        assert isinstance(messages[1].content, list)
        return self._response

    def chat_with_tools(self, messages, tools):
        raise NotImplementedError


def _fake_screenshot():
    return ContentPart(type="image", media_type="image/png", data="fakebase64", width=100, height=50)


class TestWorkerObservation:
    def test_defaults(self):
        obs = WorkerObservation(description="test")
        assert obs.found is True
        assert obs.bbox is None

    def test_with_bbox(self):
        obs = WorkerObservation(description="button", bbox=[10, 20, 30, 40], found=True)
        assert obs.bbox == [10, 20, 30, 40]


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
