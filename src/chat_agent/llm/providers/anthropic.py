import httpx

from ...core.schema import AnthropicConfig
from ..schema import (
    AnthropicMessagePayload,
    AnthropicRequest,
    AnthropicResponse,
    Message,
)


class AnthropicClient:
    def __init__(self, config: AnthropicConfig):
        self.model = config.model
        self.api_key = config.api_key
        self.base_url = config.base_url
        self.max_tokens = config.max_tokens

    def chat(self, messages: list[Message]) -> str:
        url = f"{self.base_url}/v1/messages"
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
                chat_messages.append(
                    AnthropicMessagePayload(role=m.role, content=m.content)
                )

        request = AnthropicRequest(
            model=self.model,
            messages=chat_messages,
            max_tokens=self.max_tokens,
            system=system,
        )

        with httpx.Client(timeout=120.0) as client:
            response = client.post(
                url, headers=headers, json=request.model_dump(exclude_none=True)
            )
            response.raise_for_status()
            data = response.json()

        result = AnthropicResponse.model_validate(data)
        return result.content[0].text
