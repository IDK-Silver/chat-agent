"""Tests for tools/builtin/vision.py: VisionAgent."""

from chat_agent.llm.schema import ContentPart, Message
from chat_agent.tools.builtin.vision import VisionAgent


class FakeLLMClient:
    """Minimal LLM client that returns canned responses."""

    def __init__(self, response: str = "A beautiful sunset"):
        self._response = response

    def chat(self, messages, response_schema=None):
        # Verify the message structure
        assert len(messages) == 2
        assert messages[0].role == "system"
        assert messages[1].role == "user"
        assert isinstance(messages[1].content, list)
        return self._response

    def chat_with_tools(self, messages, tools):
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
            def chat(self, messages, response_schema=None):
                received_prompts.append(messages[0].content)
                return "ok"
            def chat_with_tools(self, messages, tools):
                raise NotImplementedError

        agent = VisionAgent(TrackingClient(), "Custom vision prompt")
        agent.describe([ContentPart(type="text", text="test")])
        assert received_prompts == ["Custom vision prompt"]
