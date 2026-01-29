import httpx

from ...core.schema import OpenAIConfig
from ..schema import (
    Message,
    OpenAIMessagePayload,
    OpenAIRequest,
    OpenAIResponse,
)


class OpenAIClient:
    def __init__(self, config: OpenAIConfig):
        self.model = config.model
        self.api_key = config.api_key
        self.base_url = config.base_url
        self.max_tokens = config.max_tokens

    def chat(self, messages: list[Message]) -> str:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        request = OpenAIRequest(
            model=self.model,
            messages=[
                OpenAIMessagePayload(role=m.role, content=m.content)
                for m in messages
            ],
            max_tokens=self.max_tokens,
        )

        with httpx.Client(timeout=120.0) as client:
            response = client.post(url, headers=headers, json=request.model_dump())
            response.raise_for_status()
            data = response.json()

        result = OpenAIResponse.model_validate(data)
        return result.choices[0].message.content
