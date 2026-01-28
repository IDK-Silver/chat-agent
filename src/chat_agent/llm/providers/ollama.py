import httpx

from ...core.schema import OllamaConfig
from ..base import Message


class OllamaClient:
    def __init__(self, config: OllamaConfig):
        self.model = config.model
        self.base_url = config.base_url

    def chat(self, messages: list[Message]) -> str:
        url = f"{self.base_url}/api/chat"
        payload = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": False,
        }

        with httpx.Client(timeout=120.0) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()

        return data["message"]["content"]
