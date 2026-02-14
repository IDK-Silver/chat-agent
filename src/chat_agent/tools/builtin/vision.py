"""Vision sub-agent: describes images using a vision-capable LLM."""

from ...llm.base import LLMClient
from ...llm.schema import ContentPart, Message


class VisionAgent:
    """Sub-agent that sends images to a vision LLM and returns text descriptions."""

    def __init__(self, client: LLMClient, system_prompt: str):
        self.client = client
        self.system_prompt = system_prompt

    def describe(self, image_parts: list[ContentPart]) -> str:
        """Send image parts to vision LLM and return text description."""
        messages = [
            Message(role="system", content=self.system_prompt),
            Message(role="user", content=image_parts),
        ]
        return self.client.chat(messages)
