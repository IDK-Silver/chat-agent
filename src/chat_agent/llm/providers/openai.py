import httpx

from ..base import Message


class OpenAIClient:
    def __init__(self, config: dict):
        self.model = config["model"]
        self.api_key = config.get("api_key")
        self.base_url = config.get("base_url", "https://api.openai.com/v1")
        self.max_tokens = config.get("max_tokens", 4096)

    def chat(self, messages: list[Message]) -> str:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "max_tokens": self.max_tokens,
        }

        with httpx.Client(timeout=120.0) as client:
            response = client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()

        return data["choices"][0]["message"]["content"]
