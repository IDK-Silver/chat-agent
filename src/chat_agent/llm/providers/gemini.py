import httpx

from ...core.schema import GeminiConfig
from ..base import Message


class GeminiClient:
    def __init__(self, config: GeminiConfig):
        self.model = config.model
        self.api_key = config.api_key

    def chat(self, messages: list[Message]) -> str:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        params = {"key": self.api_key}
        headers = {"Content-Type": "application/json"}

        # Convert messages to Gemini format
        # Gemini uses "user" and "model" roles (not "assistant")
        contents = []
        system_instruction = None

        for m in messages:
            if m.role == "system":
                system_instruction = m.content
            else:
                role = "model" if m.role == "assistant" else "user"
                contents.append({"role": role, "parts": [{"text": m.content}]})

        payload = {"contents": contents}
        if system_instruction:
            payload["system_instruction"] = {"parts": [{"text": system_instruction}]}

        with httpx.Client(timeout=120.0) as client:
            response = client.post(url, params=params, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()

        return data["candidates"][0]["content"]["parts"][0]["text"]
