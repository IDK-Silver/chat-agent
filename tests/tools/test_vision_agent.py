"""Tests for tools/builtin/vision.py: VisionAgent."""

from chat_agent.llm.schema import ContentPart, Message
from chat_agent.tools.builtin.vision import VisionAgent


class FakeLLMClient:
    """Minimal LLM client that returns canned responses."""

    def __init__(self, response: str = "A beautiful sunset"):
        self._response = response
        self.calls = 0

    def chat(self, messages, response_schema=None, temperature=None):
        # Verify the message structure
        self.calls += 1
        assert len(messages) == 2
        assert messages[0].role == "system"
        assert messages[1].role == "user"
        assert isinstance(messages[1].content, list)
        return self._response

    def chat_with_tools(self, messages, tools, temperature=None):
        raise NotImplementedError


class TestVisionAgent:
    def test_describe_returns_text(self):
        client = FakeLLMClient("A 10x20 red rectangle")
        agent = VisionAgent(client, "Describe images.")
        parts = [
            ContentPart(type="text", text="Describe this image:"),
            ContentPart(type="image", media_type="image/png", data="abc", width=10, height=20),
        ]
        result = agent.describe(parts)
        assert result == "A 10x20 red rectangle"

    def test_passes_system_prompt(self):
        received_prompts = []

        class TrackingClient:
            def chat(self, messages, response_schema=None, temperature=None):
                received_prompts.append(messages[0].content)
                return "ok"
            def chat_with_tools(self, messages, tools, temperature=None):
                raise NotImplementedError

        agent = VisionAgent(TrackingClient(), "Custom vision prompt")
        agent.describe([ContentPart(type="text", text="test")])
        assert received_prompts == ["Custom vision prompt"]

    def test_uses_disk_cache_for_same_request(self, tmp_path):
        client = FakeLLMClient("Cached answer")
        agent = VisionAgent(
            client,
            "Describe images.",
            cache_dir=tmp_path,
            model_fingerprint="gemini-vision-v1",
        )
        parts = [
            ContentPart(type="text", text="Describe this image:"),
            ContentPart(type="image", media_type="image/png", data="abc"),
        ]

        first = agent.describe(parts)
        second = agent.describe(parts)

        assert first == "Cached answer"
        assert second == "Cached answer"
        assert client.calls == 1
