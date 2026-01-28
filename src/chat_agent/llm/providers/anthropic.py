import httpx

from ...core.schema import AnthropicConfig
from ..base import Message


class AnthropicClient:
    def __init__(self, config: AnthropicConfig):
        self.model = config.model
        self.api_key = config.api_key
        self.max_tokens = config.max_tokens

    def chat(self, messages: list[Message]) -> str:
        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

        # Extract system message if present
        system = None
        chat_messages = []
        for m in messages:
            if m.role == "system":
                system = m.content
            else:
                chat_messages.append({"role": m.role, "content": m.content})

        payload = {
            "model": self.model,
            "messages": chat_messages,
            "max_tokens": self.max_tokens,
        }
        if system:
            payload["system"] = system

        with httpx.Client(timeout=120.0) as client:
            response = client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()

        return data["content"][0]["text"]
